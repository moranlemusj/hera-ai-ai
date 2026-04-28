"""Gemini-powered planner.

Two passes:
1. Outline — produce a 3-6 shot list (kind, target_description, duration, rationale).
2. Template selection — for each shot, find_templates(top-3) → Gemini picks
   one OR explicitly chooses None for prompt-only render.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.graph.state import SHOT_KIND_CATEGORIES, SHOT_KINDS
from app.services.templates import find_templates

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY not set; required for the planner.")
        _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _client

def _build_outline_schema(min_shots: int, max_shots: int) -> dict[str, Any]:
    """Build the outline JSON schema with the per-run shot budget baked in.

    Forcing maxItems = MAX_RENDERS_PER_RUN means the planner produces a
    complete narrative within the budget instead of us truncating its tail.
    """
    return {
        "type": "object",
        "properties": {
            "brief_summary": {
                "type": "string",
                "description": "One paragraph synthesizing the brief — used downstream.",
            },
            "shots": {
                "type": "array",
                "minItems": min_shots,
                "maxItems": max_shots,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": SHOT_KINDS},
                        "target_description": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": [
                        "kind",
                        "target_description",
                        "duration_seconds",
                        "rationale",
                    ],
                },
            },
        },
        "required": ["brief_summary", "shots"],
    }


PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "template_id": {
            "type": "string",
            "description": (
                "task_prompt_id of the chosen template, OR the literal string "
                "'NONE' if no candidate is a strong fit and we should render "
                "from prompt alone."
            ),
        },
        "rationale": {"type": "string"},
        "shot_prompt": {
            "type": "string",
            "description": "Final prompt to send to Hera for this shot.",
        },
    },
    "required": ["template_id", "rationale", "shot_prompt"],
}


def _shot_kind_to_categories(kind: str) -> list[str]:
    """Map our shot taxonomy to Hera's catalog categories for filtering."""
    return SHOT_KIND_CATEGORIES.get(kind, ["others"])


def _build_outline_prompt(
    user_prompt: str | None,
    article: dict[str, Any] | None,
    *,
    min_shots: int,
    max_shots: int,
    target_total_duration: float,
) -> str:
    parts = [
        f"You are a motion-graphics director. Plan an animated video as a sequence of "
        f"{min_shots}-{max_shots} shots that together tell a complete story (intro, body, payoff).",
        f"You MUST produce no more than {max_shots} shots — pace the narrative to fit.",
        "",
        "Each shot must have:",
        f"- kind: one of {', '.join(SHOT_KINDS)}",
        "- target_description: ONE sentence describing what the shot should look like",
        "- duration_seconds: between 3 and 8",
        "- rationale: ONE sentence on why this shot, why this kind",
        "",
        f"Aim for a total duration around {target_total_duration:.0f}s (±50%); do not pad shots to fill time if "
        "the story is shorter than the budget allows.",
        "Also produce a brief_summary: one paragraph synthesizing the source material.",
        "",
    ]
    if user_prompt:
        parts.append(f"User intent / lens:\n{user_prompt}\n")
    if article:
        title = article.get("title") or ""
        text = (article.get("text") or "")[:6000]
        parts.append(f"Source article:\nTITLE: {title}\n\n{text}\n")
    if not user_prompt and not article:
        parts.append("(No external input provided — invent a generic concept.)\n")
    return "\n".join(parts)


def _build_pick_prompt(shot: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    parts = [
        "Pick the best template for this shot, OR pick 'NONE' if no candidate is a strong match.",
        f"Shot kind: {shot['kind']}",
        f"Shot target: {shot['target_description']}",
        f"Shot rationale: {shot['rationale']}",
        "",
        "Candidates (top-3 by hybrid search):",
    ]
    for i, c in enumerate(candidates, 1):
        parts.append(
            f"\n[{i}] template_id={c['task_prompt_id']}  title={c['title']!r}\n"
            f"     category={c['category']}  used={c.get('used', 0)}\n"
            f"     summary: {(c.get('summary') or '')[:300]}"
        )
    parts.append(
        "\nReturn the chosen template_id (or 'NONE'), a one-line rationale, "
        "and a final shot_prompt to send to Hera. The shot_prompt should be "
        "concrete and self-contained — Hera doesn't see the template summary."
    )
    return "\n".join(parts)


async def _gemini_json(
    prompt: str, schema: dict[str, Any], model: str | None = None
) -> dict[str, Any]:
    client = _get_client()
    used_model = model or settings.GEMINI_MODEL

    def _call() -> Any:
        return client.models.generate_content(
            model=used_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.4,
            ),
        )

    resp = await asyncio.to_thread(_call)
    raw = resp.text or ""
    if not raw.strip():
        raise RuntimeError("Gemini returned empty body")
    return json.loads(raw)


async def plan_outline(
    user_prompt: str | None,
    article: dict[str, Any] | None,
    *,
    max_shots: int,
    target_total_duration: float = 30.0,
) -> dict[str, Any]:
    """Plan a complete shot list within `max_shots`.

    The schema's `maxItems` and the prompt both reflect the budget so the
    planner produces a coherent, fitting narrative — we never truncate its
    tail after the fact. ``min_shots`` is bounded above by ``max_shots`` so a
    very tight quota (1 shot) is still satisfiable.
    """
    if max_shots < 1:
        raise ValueError(f"max_shots must be >= 1, got {max_shots}")
    min_shots = min(3, max_shots)
    schema = _build_outline_schema(min_shots, max_shots)
    prompt = _build_outline_prompt(
        user_prompt,
        article,
        min_shots=min_shots,
        max_shots=max_shots,
        target_total_duration=target_total_duration,
    )
    return await _gemini_json(prompt, schema)


async def pick_template_for_shot(shot: dict[str, Any]) -> dict[str, Any]:
    """Decide template_id ('NONE' allowed) + the final shot_prompt."""
    cats = _shot_kind_to_categories(shot["kind"])
    candidates = await find_templates(
        shot["target_description"],
        category_hints=cats,
        k=3,
        exclude_premium=True,
    )
    if not candidates:
        # No templates in DB / category — fall back to prompt-only directly.
        return {
            "template_id": "NONE",
            "rationale": "No matching templates in the local catalog.",
            "shot_prompt": shot["target_description"],
        }
    pick_prompt = _build_pick_prompt(shot, candidates)
    decision = await _gemini_json(pick_prompt, PICK_SCHEMA)
    # Validate the chosen template_id is one of the candidates (or NONE)
    valid_ids = {c["task_prompt_id"] for c in candidates}
    if decision["template_id"] != "NONE" and decision["template_id"] not in valid_ids:
        log.warning(
            "Planner picked an unknown template_id %r — falling back to NONE",
            decision["template_id"],
        )
        decision["template_id"] = "NONE"
        decision["rationale"] = (
            "Planner returned an unknown id; fell back to prompt-only."
        )
    # Find the title for display if a template was chosen
    title = None
    if decision["template_id"] != "NONE":
        title = next(
            (c["title"] for c in candidates if c["task_prompt_id"] == decision["template_id"]),
            None,
        )
    return {**decision, "template_title": title}
