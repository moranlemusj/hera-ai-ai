"""Conditional edge routers for the v1 graph.

Loop A (per-shot quality):
  poll_one → critic
  critic   → coherence_check (score >= threshold OR attempts exhausted)
           → strategist (score < threshold AND attempts < MAX)
  strategist → render_one (chose a render-modifying strategy)
             → coherence_check (chose accept)
             → END (chose escalate / state.error set)

Loop B (between-shot coherence):
  coherence_check → replanner (incoherent AND replans < MAX)
                  → render_one OR assemble (otherwise)
  replanner → render_one OR assemble
"""

from __future__ import annotations

from typing import Any

from app.config import settings


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
    return "critic"


def route_after_critic(state: dict[str, Any]) -> str:
    """Score gate: high score OR attempts exhausted → coherence; else → strategist."""
    if state.get("error"):
        return "end"
    idx = state["current_shot_idx"]
    shot = state["shot_list"][idx]
    score = shot.get("score") or 0.0
    attempts = len(shot.get("attempts") or [])
    if score >= settings.ACCEPT_THRESHOLD:
        return "coherence_check"
    if attempts >= settings.MAX_ATTEMPTS_PER_SHOT:
        # Auto-accept fallback when out of attempts. Phase 8 will replace this
        # with a human escalation interrupt.
        return "coherence_check"
    return "strategist"


def route_after_strategist(state: dict[str, Any]) -> str:
    """If the strategist mutated the shot for re-render, loop back; else move on."""
    if state.get("error"):
        return "end"
    idx = state["current_shot_idx"]
    shot = state["shot_list"][idx]
    status = shot.get("status")
    if status == "approved":
        return "coherence_check"
    if status == "rendering":
        return "render_one"
    # Any other state (e.g. failed) shouldn't happen here, but bail safely.
    return "end"


def route_after_coherence_check(state: dict[str, Any]) -> str:
    """If incoherent and we have replan budget, replan; else continue to next shot."""
    if state.get("error"):
        return "end"
    pending = state.get("pending_coherence_edits") or []
    replans = state.get("replans") or 0
    if pending and replans < settings.MAX_REPLANS:
        return "replanner"
    # Continue: more shots → render_one, else assemble
    if state["current_shot_idx"] >= len(state["shot_list"]):
        return "assemble"
    return "render_one"


def route_after_replanner(state: dict[str, Any]) -> str:
    if state.get("error"):
        return "end"
    if state["current_shot_idx"] >= len(state["shot_list"]):
        return "assemble"
    return "render_one"


def route_after_assemble(state: dict[str, Any]) -> str:  # noqa: ARG001
    return "end"
