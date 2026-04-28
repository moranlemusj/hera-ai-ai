"""LangGraph state types for the v0 minimal agent.

Includes scaffolding for v1 fields (critic / strategist / coherence) so we
don't have to migrate the State shape later — they're optional and unused
until v1 wires the corresponding nodes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import add_messages

InputMode = Literal["prompt", "url", "prompt+url", "extension"]
ShotKind = Literal[
    "title",
    "kinetic_typo",
    "chart",
    "lower_third",
    "infographic",
    "logo_reveal",
    "map",
    "social",
    "ad",
    "overlay",
    "other",
]
SHOT_KINDS: list[str] = list(ShotKind.__args__)  # type: ignore[attr-defined]

# Map our shot taxonomy to Hera's catalog categories. Co-located with the
# Literal so additions stay in sync (a kind without a mapping defaults to
# `["others"]` in the planner).
SHOT_KIND_CATEGORIES: dict[str, list[str]] = {
    "title": ["text"],
    "kinetic_typo": ["text"],
    "chart": ["infographics"],
    "lower_third": ["overlays", "text"],
    "infographic": ["infographics"],
    "logo_reveal": ["logos"],
    "map": ["maps"],
    "social": ["socialmedia"],
    "ad": ["ads"],
    "overlay": ["overlays"],
    "other": ["others"],
}

ShotStatus = Literal[
    "planned", "rendering", "ready", "rejected", "approved", "failed"
]
ParentKind = Literal["template", "prior_render"]


class Shot(TypedDict, total=False):
    idx: int
    kind: ShotKind
    prompt: str
    target_description: str
    aspect_ratio: str
    duration_seconds: float
    status: ShotStatus
    # Template selection (planner output)
    template_id: str | None
    template_title: str | None
    template_picked_reason: str | None
    parent_kind: ParentKind | None
    parent_video_id: str | None
    # Render output
    video_id: str | None
    download_url: str | None
    local_path: str | None
    # v1 fields (unused in v0)
    score: float | None
    diagnosis: dict[str, Any] | None
    attempts: list[dict[str, Any]]


class Source(TypedDict):
    url: str
    title: str
    snippet: str
    used_in_shots: list[int]


class State(TypedDict, total=False):
    # Run metadata
    run_id: str
    created_at: str

    # Input
    input_mode: InputMode
    user_prompt: str | None
    source_url: str | None
    source_article: dict[str, Any] | None
    allow_research: bool

    # Research output (unused in v0)
    sources: list[Source]
    search_count: int

    # Planning
    brief_summary: str
    shot_list: list[Shot]
    current_shot_idx: int

    # Iteration (unused in v0)
    revision_log: list[dict[str, Any]]
    coherence_diagnoses: list[dict[str, Any]]
    escalation_question: str | None

    # 401 interrupt flag
    pending_session_refresh: bool

    # Output
    final_video_path: str | None

    # Errors propagated back to the SSE consumer
    error: str | None

    # Reasoning trace (LLM messages)
    messages: Annotated[list, add_messages]
