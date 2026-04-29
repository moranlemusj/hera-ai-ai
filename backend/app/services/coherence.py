"""Between-shot coherence checker — Loop B.

After each shot finishes, samples a frame from every completed shot, sends
them to Gemini multimodal alongside the brief and the planned shot list,
and asks: does the remaining plan still tell the right story given what's
actually been rendered so far? If no, returns suggested edits to downstream
shots. Edits to already-rendered shots are silently dropped (we don't redo
work).
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.services._frames import ffprobe_duration, sample_frame
from app.services._gemini_retry import gemini_call

log = logging.getLogger(__name__)

_client: genai.Client | None = None

COHERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "coherent": {"type": "boolean"},
        "reason": {"type": "string"},
        "suggested_edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "new_prompt": {"type": "string"},
                    "new_target_description": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["idx", "rationale"],
            },
        },
    },
    "required": ["coherent", "reason", "suggested_edits"],
}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY not set; required for coherence_check.")
        _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _client


def _fallback_coherent(reason: str) -> dict[str, Any]:
    """Return a 'looks fine, continue' verdict when the check can't run."""
    return {"coherent": True, "reason": f"coherence skipped: {reason}", "suggested_edits": []}


async def check_coherence(
    shot_list: list[dict[str, Any]],
    rendered_paths: list[Path],
    brief: str,
    current_idx: int,
) -> dict[str, Any]:
    """Reason about narrative coherence given the rendered shots so far.

    `current_idx` is the index of the shot that just completed; any
    suggested_edits with idx <= current_idx are silently dropped, since
    we don't undo completed work.
    """
    if not rendered_paths:
        return _fallback_coherent("no rendered shots yet")

    # Sample one frame from each completed shot at 50%.
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        frame_bytes: list[bytes] = []
        for i, video_path in enumerate(rendered_paths):
            try:
                duration = await ffprobe_duration(video_path)
                frame = await sample_frame(
                    video_path, duration * 0.5, td_path / f"shot_{i}.png"
                )
                frame_bytes.append(frame.read_bytes())
            except Exception as exc:  # noqa: BLE001
                log.warning("Coherence frame sample for shot %d failed: %s", i, exc)

        if not frame_bytes:
            return _fallback_coherent("no frames could be sampled")

        # Strip the video bytes out of the shot list before serializing.
        plan_summary = [
            {
                "idx": s.get("idx"),
                "kind": s.get("kind"),
                "target_description": s.get("target_description"),
                "duration_seconds": s.get("duration_seconds"),
                "status": s.get("status"),
                "template_title": s.get("template_title"),
            }
            for s in shot_list
        ]

        prompt = (
            "You are a motion-graphics director reviewing your own work. "
            f"Below are sampled frames from shots 0..{len(frame_bytes) - 1} "
            "that have just rendered, in order, followed by the full planned "
            "shot list including upcoming shots.\n\n"
            f"BRIEF: {brief}\n\n"
            f"PLAN: {json.dumps(plan_summary, indent=2)}\n\n"
            "Question: do the REMAINING (not yet rendered) shots still tell "
            "the right story given how the rendered shots actually came out? "
            "Look for visual style consistency, narrative continuity, pacing.\n\n"
            "If the plan is still good → coherent: true, no edits.\n"
            f"If the plan needs adjustment → coherent: false, return suggested_edits "
            f"for indices STRICTLY GREATER than {current_idx}. You can suggest "
            "a new_prompt (just the prompt for that shot) and/or a "
            "new_target_description (which will trigger re-picking the "
            "template). Each edit needs a one-line rationale."
        )

        contents: list[Any] = [prompt]
        for fb in frame_bytes:
            contents.append(genai_types.Part.from_bytes(data=fb, mime_type="image/png"))

        try:
            client = _get_client()
            resp = await gemini_call(
                client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=COHERENCE_SCHEMA,
                    temperature=0.3,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Coherence Gemini call failed: %s", exc)
            return _fallback_coherent(f"gemini call failed: {exc}")

        raw = resp.text or ""
        if not raw.strip():
            return _fallback_coherent("empty Gemini response")
        try:
            verdict = json.loads(raw)
        except json.JSONDecodeError:
            return _fallback_coherent("invalid JSON")

    # Drop any edits targeting completed shots — we never undo committed work.
    raw_edits = verdict.get("suggested_edits") or []
    filtered = [e for e in raw_edits if isinstance(e.get("idx"), int) and e["idx"] > current_idx]
    if len(filtered) < len(raw_edits):
        log.info(
            "Coherence: dropped %d edit(s) targeting completed shots (idx <= %d)",
            len(raw_edits) - len(filtered),
            current_idx,
        )
    verdict["suggested_edits"] = filtered
    # If we dropped ALL edits, the plan is effectively coherent now.
    if not filtered and not verdict.get("coherent", False):
        verdict["coherent"] = True
        verdict["reason"] = (
            verdict.get("reason", "")
            + " [all suggested edits targeted completed shots; treating as coherent]"
        ).strip()
    return verdict
