"""Compile the v1 LangGraph and wire the PostgresSaver checkpointer.

Uses the existing app.db pool (no new connections) — `AsyncPostgresSaver`
accepts the same psycopg pool we already manage.

Graph shape:
  intake → (fetch_article →)? planner → render_one → poll_one → critic
  critic → strategist → render_one (loop) | coherence_check
  coherence_check → replanner | render_one | assemble
  replanner → render_one | assemble
  assemble → END
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.db import get_pool
from app.graph import edges, nodes
from app.graph.state import State

log = logging.getLogger(__name__)

_compiled: CompiledStateGraph[Any, Any, Any, Any] | None = None
_checkpointer: AsyncPostgresSaver | None = None


async def _make_checkpointer() -> AsyncPostgresSaver:
    pool = await get_pool()
    saver = AsyncPostgresSaver(conn=pool)  # type: ignore[arg-type]
    await saver.setup()
    return saver


async def _build() -> CompiledStateGraph[Any, Any, Any, Any]:
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(State)
    builder.add_node("intake", nodes.intake)
    builder.add_node("fetch_article", nodes.fetch_article_node)
    builder.add_node("planner", nodes.planner_node)
    builder.add_node("render_one", nodes.render_one)
    builder.add_node("poll_one", nodes.poll_one)
    builder.add_node("critic", nodes.critic_node)
    builder.add_node("strategist", nodes.strategist_node)
    builder.add_node("coherence_check", nodes.coherence_check_node)
    builder.add_node("replanner", nodes.replanner_node)
    builder.add_node("assemble", nodes.assemble)

    builder.add_edge(START, "intake")
    builder.add_conditional_edges(
        "intake",
        edges.route_after_intake,
        {"fetch_article": "fetch_article", "planner": "planner", "end": END},
    )
    builder.add_conditional_edges(
        "fetch_article",
        edges.route_after_fetch_article,
        {"planner": "planner", "end": END},
    )
    builder.add_conditional_edges(
        "planner",
        edges.route_after_planner,
        {"render_one": "render_one", "end": END},
    )
    builder.add_conditional_edges(
        "render_one",
        edges.route_after_render_one,
        {"poll_one": "poll_one", "end": END},
    )
    builder.add_conditional_edges(
        "poll_one",
        edges.route_after_poll_one,
        {"critic": "critic", "end": END},
    )
    builder.add_conditional_edges(
        "critic",
        edges.route_after_critic,
        {"strategist": "strategist", "coherence_check": "coherence_check", "end": END},
    )
    builder.add_conditional_edges(
        "strategist",
        edges.route_after_strategist,
        {"render_one": "render_one", "coherence_check": "coherence_check", "end": END},
    )
    builder.add_conditional_edges(
        "coherence_check",
        edges.route_after_coherence_check,
        {"replanner": "replanner", "render_one": "render_one", "assemble": "assemble", "end": END},
    )
    builder.add_conditional_edges(
        "replanner",
        edges.route_after_replanner,
        {"render_one": "render_one", "assemble": "assemble", "end": END},
    )
    builder.add_edge("assemble", END)

    global _checkpointer
    _checkpointer = await _make_checkpointer()
    return builder.compile(checkpointer=_checkpointer)


async def get_compiled_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    global _compiled
    if _compiled is None:
        _compiled = await _build()
    return _compiled
