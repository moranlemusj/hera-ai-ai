"""Conditional edge routers for the v0 graph."""

from __future__ import annotations

from typing import Any


def route_after_intake(state: dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    if state.get("source_url") and not state.get("source_article"):
        return "fetch_article"
    return "planner"


def route_after_fetch_article(state: dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    return "planner"


def route_after_planner(state: dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    if not state.get("shot_list"):
        return "end"
    return "render_one"


def route_after_render_one(state: dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    return "poll_one"


def route_after_poll_one(state: dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    if state["current_shot_idx"] >= len(state["shot_list"]):
        return "assemble"
    return "render_one"


def route_after_assemble(state: dict[str, Any]) -> str:  # noqa: ARG001
    return "end"
