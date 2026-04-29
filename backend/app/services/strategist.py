"""Per-shot strategist — picks ONE intervention from a tool menu when the
critic flags a shot. The agency lives here: the agent reasons about WHY a
shot failed (from the diagnosis) and chooses HOW to fix it (rewrite the
prompt, switch templates, revise via parent_video_id, accept anyway, or
escalate to the human).

The constraint baked into the prompt: don't repeat a strategy that just
failed unless you can articulate why this attempt is materially different.
That's what turns a retry counter into a real reasoning loop.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.services._gemini_retry import gemini_call

log = logging.getLogger(__name__)

_client: genai.Client | None = None

STRATEGY_OPTIONS = [
    "rewrite_prompt",
    "switch_template",
    "revise_via_parent",
    "accept",
    "escalate",
]

STRATEGIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string", "enum": STRATEGY_OPTIONS},
        "rationale": {"type": "string"},
        "shot_prompt": {
            "type": "string",
            "description": (
                "New prompt for Hera. Required for rewrite_prompt and "
                "revise_via_parent; ignored otherwise."
            ),
        },
        "target_description": {
            "type": "string",
            "description": (
                "Refined target description for re-running template selection. "
                "Required for switch_template; ignored otherwise."
            ),
        },
    },
    "required": ["strategy", "rationale"],
}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY not set; required for the strategist.")
        _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _client


def _format_attempts(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "(no prior attempts)"
    lines: list[str] = []
    for i, a in enumerate(attempts, 1):
        diagnosis = a.get("diagnosis") or {}
        score = a.get("score")
        score_s = f"{score:.2f}" if isinstance(score, int | float) else "—"
        notes = diagnosis.get("notes", "(no notes)") if isinstance(diagnosis, dict) else "(no diagnosis)"
        strategy = a.get("strategy", "initial")
        lines.append(
            f"Attempt {i} (strategy={strategy}, score={score_s}): {notes}"
        )
    return "\n".join(lines)


async def pick_strategy(shot: dict[str, Any], brief: str) -> dict[str, Any]:
    """Reason about one bad shot and pick the next intervention.

    Returns a dict with `strategy` plus the field(s) the chosen strategy
    consumes. Falls back to `accept` on any failure so a flaky LLM doesn't
    deadlock the run.
    """
    diagnosis = shot.get("diagnosis") or {}
    attempts = shot.get("attempts") or []

    prompt = (
        "You are a motion-graphics strategist. A rendered shot just got a "
        "weak quality score from the critic. Pick ONE intervention from the "
        "tool menu to try next.\n\n"
        f"BRIEF: {brief}\n\n"
        f"SHOT TARGET: {shot.get('target_description', '(unspecified)')}\n"
        f"SHOT KIND: {shot.get('kind', '(unspecified)')}\n"
        f"CURRENT PROMPT: {shot.get('prompt', '(unspecified)')}\n"
        f"CURRENT TEMPLATE: {shot.get('template_title') or 'NONE (prompt-only)'}\n\n"
        f"LATEST DIAGNOSIS:\n{json.dumps(diagnosis, indent=2)}\n\n"
        f"PRIOR ATTEMPTS:\n{_format_attempts(attempts)}\n\n"
        "Tools available:\n"
        "  - rewrite_prompt:    write a NEW shot_prompt addressing the diagnosis.\n"
        "  - switch_template:   write a refined target_description; the system\n"
        "                       will re-run template selection with it.\n"
        "  - revise_via_parent: write a NEW shot_prompt that will be passed to\n"
        "                       Hera with the prior video_id as parent (cheap\n"
        "                       iteration). Best for small text/style tweaks.\n"
        "  - accept:            override the critic; this shot is good enough.\n"
        "  - escalate:          give up; surface to the human (use sparingly).\n\n"
        "Constraints:\n"
        "  - DO NOT pick the same strategy that just failed unless you can\n"
        "    articulate in your rationale exactly why this attempt is\n"
        "    materially different (different prompt focus, different template,\n"
        "    different aspect of the diagnosis being addressed).\n"
        "  - Provide the field the chosen strategy needs (`shot_prompt` for\n"
        "    rewrite_prompt and revise_via_parent; `target_description` for\n"
        "    switch_template). Leave the others empty.\n"
        "  - `rationale` is REQUIRED — one sentence on why this strategy."
    )

    try:
        client = _get_client()
        resp = await gemini_call(
            client.models.generate_content,
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=STRATEGIST_SCHEMA,
                temperature=0.5,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Strategist Gemini call failed, falling back to accept: %s", exc)
        return {
            "strategy": "accept",
            "rationale": f"Strategist call failed ({exc}); accepting last attempt.",
        }

    raw = resp.text or ""
    if not raw.strip():
        return {
            "strategy": "accept",
            "rationale": "Strategist returned empty response; accepting last attempt.",
        }
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "strategy": "accept",
            "rationale": "Strategist returned invalid JSON; accepting last attempt.",
        }

    # Defensive: enforce the required-field-per-strategy contract.
    strategy = decision.get("strategy")
    if strategy in ("rewrite_prompt", "revise_via_parent") and not decision.get("shot_prompt"):
        log.warning(
            "Strategist picked %s but provided no shot_prompt; downgrading to accept",
            strategy,
        )
        return {"strategy": "accept", "rationale": decision.get("rationale", "")}
    if strategy == "switch_template" and not decision.get("target_description"):
        log.warning(
            "Strategist picked switch_template but no target_description; downgrading to accept",
        )
        return {"strategy": "accept", "rationale": decision.get("rationale", "")}

    return decision
