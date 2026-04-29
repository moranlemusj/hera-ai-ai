"""Per-shot quality critic — Loop A's eye.

Samples 1-2 frames from a rendered shot's mp4, calls Gemini Flash with
vision, and returns a structured rubric. Used by the agent's Loop A to
decide whether a shot needs another attempt (via the strategist) or can be
accepted and moved on from.
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

RUBRIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "composition": {"type": "string", "enum": ["ok", "weak"]},
        "typography": {"type": "string", "enum": ["ok", "weak"]},
        "motion": {"type": "string", "enum": ["ok", "weak", "jittery"]},
        "color": {"type": "string", "enum": ["ok", "weak", "off_brand"]},
        "text_legibility": {"type": "string", "enum": ["ok", "weak"]},
        "narrative_fit": {"type": "string", "enum": ["ok", "weak", "off_topic"]},
        "visual_consistency": {"type": "string", "enum": ["ok", "weak", "n/a"]},
        "overall_score": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
    },
    "required": [
        "composition",
        "typography",
        "motion",
        "color",
        "text_legibility",
        "narrative_fit",
        "visual_consistency",
        "overall_score",
        "notes",
    ],
}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY not set; required for the critic.")
        _client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _client


def _fallback_pass(reason: str) -> dict[str, Any]:
    """Return an "everything's fine, accept" rubric when the critic can't run."""
    return {
        "composition": "ok",
        "typography": "ok",
        "motion": "ok",
        "color": "ok",
        "text_legibility": "ok",
        "narrative_fit": "ok",
        "visual_consistency": "ok",
        "overall_score": 1.0,
        "notes": f"critic unavailable, accepting: {reason}",
    }


def _format_arc(arc: list[dict[str, Any]] | None, current_idx: int) -> str:
    if not arc:
        return "(arc unavailable)"
    lines: list[str] = []
    for s in arc:
        idx = s.get("idx", "?")
        kind = s.get("kind", "?")
        target = s.get("target_description", "(no description)")
        marker = "  ← CURRENT" if idx == current_idx else ""
        lines.append(f"  Shot {idx} ({kind}): {target}{marker}")
    return "\n".join(lines)


# Limit to the most recent 2 prior shots — past that, signal-to-noise drops
# and the multimodal payload grows expensive. Two frames are enough to anchor
# palette/typography continuity without overweighting older shots.
_MAX_PRIOR_FRAMES = 2


async def grade_shot(
    local_path: Path,
    shot: dict[str, Any],
    brief: str,
    *,
    arc: list[dict[str, Any]] | None = None,
    prior_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Score a rendered shot against a structured rubric using Gemini vision.

    `arc` is the full planned shot list (with idx, kind, target_description) so
    the critic can judge how the rendered shot fits the surrounding narrative.
    `prior_paths` are mp4s of already-approved shots; we sample one frame from
    each (capped at the most recent ``_MAX_PRIOR_FRAMES``) so the critic can
    grade visual consistency — palette, typography style — against actually
    rendered earlier shots, not just against the prompt.

    On any failure (missing file, ffmpeg error, Gemini timeout) returns a
    pass-through rubric so the run continues — the critic should never break
    the pipeline.
    """
    if not local_path.exists() or local_path.stat().st_size == 0:
        return _fallback_pass(f"missing or empty mp4: {local_path}")

    try:
        duration = await ffprobe_duration(local_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("ffprobe failed: %s", exc)
        return _fallback_pass("ffprobe failed")

    # Sample two frames at 25% and 75% timestamps (clamped to keep them inside
    # the clip even for very short videos).
    t1 = max(0.0, duration * 0.25)
    t2 = max(t1 + 0.05, duration * 0.75)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        try:
            frame1 = await sample_frame(local_path, t1, td_path / "f1.png")
            frame2 = await sample_frame(local_path, t2, td_path / "f2.png")
        except Exception as exc:  # noqa: BLE001
            log.warning("Frame sample failed: %s", exc)
            return _fallback_pass("frame sample failed")

        # Sample one mid-frame from each of the last few approved shots so the
        # critic can grade visual consistency against what actually rendered.
        prior_frame_bytes: list[bytes] = []
        recent_priors = (prior_paths or [])[-_MAX_PRIOR_FRAMES:]
        for i, p in enumerate(recent_priors):
            try:
                pdur = await ffprobe_duration(p)
                pframe = await sample_frame(p, pdur * 0.5, td_path / f"prior_{i}.png")
                prior_frame_bytes.append(pframe.read_bytes())
            except Exception as exc:  # noqa: BLE001
                log.warning("Prior-frame sample failed for %s: %s", p, exc)

        try:
            client = _get_client()
            f1_bytes = frame1.read_bytes()
            f2_bytes = frame2.read_bytes()
        except Exception as exc:  # noqa: BLE001
            log.warning("Critic setup failed: %s", exc)
            return _fallback_pass("setup failed")

        current_idx = int(shot.get("idx", 0))
        prior_block = (
            f"\nThe first {len(prior_frame_bytes)} frame(s) are MID-FRAMES from the "
            "immediately preceding approved shots, in order. Use them as visual "
            "anchors for `visual_consistency` (palette, typography family, density, "
            "tone). The final two frames are from the CURRENT shot (25% and 75%) — "
            "those are what you're grading.\n"
            if prior_frame_bytes
            else "\nNo prior shots are available yet — grade `visual_consistency` "
            "as 'n/a'.\n"
        )

        prompt = (
            "You are a strict but fair motion-graphics quality critic, judging "
            "both individual shot quality AND how well a shot fits its sequence.\n\n"
            f"BRIEF: {brief}\n\n"
            f"PLANNED ARC:\n{_format_arc(arc, current_idx)}\n\n"
            f"CURRENT SHOT TARGET: {shot.get('target_description', '(unspecified)')}\n"
            f"CURRENT SHOT KIND: {shot.get('kind', '(unspecified)')}\n"
            f"{prior_block}"
            "Grade the CURRENT shot against the rubric. Be specific in `notes` "
            "(e.g. \"text illegible at frame 25%\", \"palette diverges from prior "
            "shot — switches from cool blues to warm reds without motivation\"). "
            "Only reduce `overall_score` below 0.7 for genuine problems — minor "
            "stylistic deviations from a perfect ideal should still score well. "
            "`narrative_fit` should be 'weak' or 'off_topic' if the shot doesn't "
            "advance the planned arc; `visual_consistency` should be 'weak' if "
            "the look departs from earlier shots without an obvious story reason."
        )

        contents: list[Any] = [prompt]
        for pb in prior_frame_bytes:
            contents.append(genai_types.Part.from_bytes(data=pb, mime_type="image/png"))
        contents.append(genai_types.Part.from_bytes(data=f1_bytes, mime_type="image/png"))
        contents.append(genai_types.Part.from_bytes(data=f2_bytes, mime_type="image/png"))

        try:
            resp = await gemini_call(
                client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RUBRIC_SCHEMA,
                    temperature=0.2,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini critic call failed: %s", exc)
            return _fallback_pass(f"gemini call failed: {exc}")

        raw = resp.text or ""
        if not raw.strip():
            return _fallback_pass("empty Gemini response")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("Critic returned invalid JSON: %s", exc)
            return _fallback_pass("invalid JSON")
