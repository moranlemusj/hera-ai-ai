"""LangGraph nodes for the v1 agent.

Each node is a pure async function that takes State and returns a partial
state update (LangGraph merges it). v1 adds the agentic loops:
  Loop A — critic → strategist → render (per shot)
  Loop B — coherence_check → replanner (between shots)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from app.config import settings
from app.services import (
    coherence,
    critic,
    hera_api,
    planner,
    render_cache,
    stitch,
    strategist,
)
from app.services.jina import ArticleFetchError, fetch_article

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------


def _classify_input_mode(state: dict[str, Any]) -> str:
    has_prompt = bool(state.get("user_prompt"))
    has_url = bool(state.get("source_url"))
    has_article = bool(state.get("source_article"))
    if has_article:
        return "extension"
    if has_prompt and has_url:
        return "prompt+url"
    if has_url:
        return "url"
    if has_prompt:
        return "prompt"
    raise ValueError("Need at least one of: user_prompt, source_url, source_article")


async def intake(state: dict[str, Any]) -> dict[str, Any]:
    mode = _classify_input_mode(state)
    return {
        "input_mode": mode,
        "run_id": state.get("run_id") or str(uuid.uuid4()),
        "created_at": state.get("created_at") or datetime.now(tz=UTC).isoformat(),
        "current_shot_idx": 0,
        "shot_list": [],
    }


# ---------------------------------------------------------------------------
# fetch_article
# ---------------------------------------------------------------------------


async def fetch_article_node(state: dict[str, Any]) -> dict[str, Any]:
    url = state["source_url"]
    try:
        article = await fetch_article(url)
        return {"source_article": dict(article)}
    except ArticleFetchError as exc:
        log.warning("Article fetch failed: %s", exc)
        if state.get("user_prompt"):
            # We can still plan from the prompt alone.
            return {"source_article": None}
        return {"error": f"Could not extract article: {exc}"}


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    # Budget the planner: schema's maxItems == MAX_RENDERS_PER_RUN so the
    # planner produces a complete narrative within quota instead of us
    # lopping off the tail.
    outline = await planner.plan_outline(
        state.get("user_prompt"),
        state.get("source_article"),
        max_shots=settings.MAX_RENDERS_PER_RUN,
        target_total_duration=settings.TARGET_TOTAL_DURATION,
    )

    shots_raw = outline["shots"]
    if len(shots_raw) > settings.MAX_RENDERS_PER_RUN:
        # Defensive: the schema enforces this, but if Gemini ever drifts we
        # fail loudly rather than silently truncating mid-story.
        return {
            "error": (
                f"Planner returned {len(shots_raw)} shots but budget is "
                f"{settings.MAX_RENDERS_PER_RUN}; refusing to truncate."
            )
        }

    shot_list: list[dict[str, Any]] = []

    # Run template selection in parallel — independent calls. Pass the whole
    # outline as `arc` so each shot's prompt is built with awareness of its
    # neighbors, not in isolation.
    arc = [{"idx": i, **s} for i, s in enumerate(shots_raw)]
    decisions = await asyncio.gather(
        *[
            planner.pick_template_for_shot({"idx": i, **s}, arc=arc)
            for i, s in enumerate(shots_raw)
        ],
        return_exceptions=True,
    )

    for idx, (shot, decision) in enumerate(zip(shots_raw, decisions, strict=True)):
        if isinstance(decision, Exception):
            log.warning("Template-pick failed for shot %d: %s", idx, decision)
            template_id = None
            template_title = None
            template_picked_reason = f"template selection errored: {decision}"
            shot_prompt = shot["target_description"]
        else:
            tid = decision["template_id"]
            template_id = None if tid == "NONE" else tid
            template_title = decision.get("template_title")
            template_picked_reason = decision["rationale"]
            shot_prompt = decision["shot_prompt"]

        shot_list.append(
            {
                "idx": idx,
                "kind": shot["kind"],
                "target_description": shot["target_description"],
                # Carry the planner's per-shot rationale through so re-picks
                # (strategist's switch_template, replanner) can pass the shot
                # back to pick_template_for_shot without a KeyError.
                "rationale": shot.get("rationale", ""),
                "prompt": shot_prompt,
                "aspect_ratio": settings.DEFAULT_ASPECT_RATIO,
                "duration_seconds": float(shot["duration_seconds"]),
                "status": "planned",
                "template_id": template_id,
                "template_title": template_title,
                "template_picked_reason": template_picked_reason,
                "parent_kind": "template" if template_id else None,
                "parent_video_id": template_id,
                "video_id": None,
                "download_url": None,
                "local_path": None,
                "score": None,
                "diagnosis": None,
                "attempts": [],
            }
        )

    return {
        "brief_summary": outline["brief_summary"],
        "shot_list": shot_list,
        "current_shot_idx": 0,
    }


# ---------------------------------------------------------------------------
# render_one
# ---------------------------------------------------------------------------


def _shot_cache_key(shot: dict[str, Any]) -> str:
    return render_cache.cache_key(
        prompt=shot["prompt"],
        parent_video_id=shot.get("parent_video_id"),
        aspect=shot["aspect_ratio"],
        duration_seconds=shot["duration_seconds"],
        fps=settings.DEFAULT_FPS,
        resolution=settings.DEFAULT_RESOLUTION,
    )


async def _interrupt_for_quota(count: int, cap: int) -> None:
    """Pause the graph until an operator confirms quota has been expanded.

    Resume payload:
      - {} or {"acknowledge": true} — operator says "I've raised the cap
        upstream / new month rolled / etc., just retry"
      - {"new_cap": N} — operator explicitly raises the in-process cap to N
        for the rest of this run (process-local, lost on restart)

    On resume we re-check the quota; if still over, the run errors out via
    state.error → END so we don't loop forever.
    """
    payload = interrupt(
        {
            "kind": "hera_quota_exhausted",
            "reason": f"Monthly Hera quota reached: {count}/{cap}",
            "current_count": count,
            "current_cap": cap,
        }
    )
    if isinstance(payload, dict):
        new_cap = payload.get("new_cap")
        if isinstance(new_cap, int) and new_cap > cap:
            hera_api.set_quota_override(new_cap)
            log.info("Quota override applied: %d → %d", cap, new_cap)


async def render_one(state: dict[str, Any]) -> dict[str, Any]:
    idx = state["current_shot_idx"]
    shot_list: list[dict[str, Any]] = list(state["shot_list"])
    shot = dict(shot_list[idx])

    key = _shot_cache_key(shot)
    cached = await render_cache.get_cached(key)
    if cached and cached.get("local_path") and Path(cached["local_path"]).exists():
        await render_cache.record_hit(key)
        shot["video_id"] = cached["video_id"]
        shot["local_path"] = cached["local_path"]
        shot["status"] = "ready"
        shot_list[idx] = shot
        log.info("Shot %d: cache hit (%s)", idx, cached["video_id"])
        return {"shot_list": shot_list}

    # Quota guard — interruptable. The agent may retry the same shot if the
    # operator raises the cap or confirms a new month rolled over.
    while True:
        try:
            await hera_api.check_quota_or_raise()
            break
        except hera_api.HeraQuotaExceededError as exc:
            await _interrupt_for_quota(
                count=await hera_api.get_monthly_render_count(),
                cap=hera_api.effective_cap(),
            )
            # Re-check on next loop iteration. If still over, surface as
            # state.error → END (don't loop forever).
            count = await hera_api.get_monthly_render_count()
            if count >= hera_api.effective_cap():
                return {
                    "error": f"Quota still exhausted after operator ack: {exc}"
                }

    try:
        video_id = await hera_api.create_render(
            shot["prompt"],
            aspect=shot["aspect_ratio"],
            fps=settings.DEFAULT_FPS,
            resolution=settings.DEFAULT_RESOLUTION,
            duration_seconds=shot["duration_seconds"],
            parent_video_id=shot.get("parent_video_id"),
        )
    except hera_api.HeraApiKeyInvalidError as exc:
        # No interrupt — fixing this requires editing .env and restarting.
        log.error("Hera REST 401: %s", exc)
        shot["status"] = "failed"
        shot_list[idx] = shot
        return {"shot_list": shot_list, "error": str(exc)}
    except hera_api.HeraApiError as exc:
        log.exception("Render failed for shot %d: %s", idx, exc)
        shot["status"] = "failed"
        shot_list[idx] = shot
        return {"shot_list": shot_list, "error": f"create_render failed: {exc}"}

    shot["video_id"] = video_id
    shot["status"] = "rendering"
    shot_list[idx] = shot
    return {"shot_list": shot_list}


# ---------------------------------------------------------------------------
# poll_one
# ---------------------------------------------------------------------------


async def poll_one(state: dict[str, Any]) -> dict[str, Any]:
    idx = state["current_shot_idx"]
    shot_list: list[dict[str, Any]] = list(state["shot_list"])
    shot = dict(shot_list[idx])

    if shot["status"] == "ready" and shot.get("local_path"):
        # Cache hit path — already downloaded; the critic still gets a swing
        # at it for consistency with the v1 graph.
        shot_list[idx] = shot
        return {"shot_list": shot_list}

    video_id = shot["video_id"]
    deadline = (
        asyncio.get_event_loop().time() + settings.RENDER_TIMEOUT_SECONDS
    )
    while True:
        try:
            result = await hera_api.poll_render(video_id)
        except hera_api.HeraApiKeyInvalidError as exc:
            log.error("Hera REST 401 during poll: %s", exc)
            shot["status"] = "failed"
            shot_list[idx] = shot
            return {"shot_list": shot_list, "error": str(exc)}

        # Hera's GET /v1/videos/{id} status enum is: "in-progress" | "success" | "failed".
        # The output array is `outputs` and each entry's URL field is `file_url`.
        status = result.get("status")
        if status == "success":
            outputs = result.get("outputs") or []
            if not outputs:
                raise hera_api.HeraApiError("success status but no outputs")
            url = outputs[0].get("file_url")
            if not url:
                raise hera_api.HeraApiError("output has no file_url")
            renders_dir = settings.RENDERS_DIR / state["run_id"]
            renders_dir.mkdir(parents=True, exist_ok=True)
            dest = renders_dir / f"shot_{idx}.mp4"
            await hera_api.download_render(url, dest)

            shot["download_url"] = url
            shot["local_path"] = str(dest)
            shot["status"] = "ready"

            await render_cache.store(
                _shot_cache_key(shot),
                video_id=video_id,
                download_url=url,
                local_path=str(dest),
            )
            break
        if status == "failed":
            shot["status"] = "failed"
            shot_list[idx] = shot
            return {
                "shot_list": shot_list,
                "error": f"Hera reported render failed: {result.get('error', 'unknown')}",
            }
        if asyncio.get_event_loop().time() > deadline:
            shot["status"] = "failed"
            shot_list[idx] = shot
            return {
                "shot_list": shot_list,
                "error": f"Render {video_id} timed out after {settings.RENDER_TIMEOUT_SECONDS}s",
            }
        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

    shot_list[idx] = shot
    return {"shot_list": shot_list}


# ---------------------------------------------------------------------------
# critic — Loop A entry: grade the rendered shot
# ---------------------------------------------------------------------------


async def critic_node(state: dict[str, Any]) -> dict[str, Any]:
    idx = state["current_shot_idx"]
    shot_list: list[dict[str, Any]] = list(state["shot_list"])
    shot = dict(shot_list[idx])

    if not shot.get("local_path"):
        # Render failed earlier — nothing to grade. Force-fail the shot.
        log.warning("critic invoked without local_path for shot %d", idx)
        shot["status"] = "failed"
        shot_list[idx] = shot
        return {"shot_list": shot_list, "error": "no rendered file to critique"}

    # Collect mp4 paths from earlier approved shots so the critic can grade
    # visual consistency against what actually rendered, not just the prompt.
    prior_paths: list[Path] = []
    for prior in shot_list[:idx]:
        prior_path = prior.get("local_path")
        if prior_path and prior.get("status") in {"approved", "ready"}:
            prior_paths.append(Path(prior_path))

    diagnosis = await critic.grade_shot(
        Path(shot["local_path"]),
        shot,
        state.get("brief_summary", ""),
        arc=shot_list,
        prior_paths=prior_paths,
    )

    score = float(diagnosis.get("overall_score", 1.0))
    shot["score"] = score
    shot["diagnosis"] = diagnosis

    attempts = list(shot.get("attempts") or [])
    attempts.append(
        {
            "strategy": shot.get("last_strategy", "initial"),
            "rationale": shot.get("last_strategy_rationale", ""),
            "prompt": shot.get("prompt"),
            "template_id": shot.get("template_id"),
            "video_id": shot.get("video_id"),
            "score": score,
            "diagnosis": diagnosis,
        }
    )
    shot["attempts"] = attempts
    shot_list[idx] = shot
    return {"shot_list": shot_list}


# ---------------------------------------------------------------------------
# strategist — Loop A intervention picker
# ---------------------------------------------------------------------------


async def strategist_node(state: dict[str, Any]) -> dict[str, Any]:
    idx = state["current_shot_idx"]
    shot_list: list[dict[str, Any]] = list(state["shot_list"])
    shot = dict(shot_list[idx])

    decision = await strategist.pick_strategy(shot, state.get("brief_summary", ""))
    strat = decision["strategy"]
    shot["last_strategy"] = strat
    shot["last_strategy_rationale"] = decision.get("rationale", "")

    if strat == "accept":
        # current_shot_idx stays put so coherence_check (next node) sees this shot.
        shot["status"] = "approved"
        shot_list[idx] = shot
        return {"shot_list": shot_list}
    if strat == "escalate":
        # Until Phase 8 lands a real interrupt, escalate ends the run with a
        # clear error and a state.error → END route. No silent retries.
        shot["status"] = "failed"
        shot_list[idx] = shot
        return {
            "shot_list": shot_list,
            "error": f"strategist escalated: {decision.get('rationale', '(no rationale)')}",
        }
    if strat == "rewrite_prompt":
        shot["prompt"] = decision["shot_prompt"]
        shot["video_id"] = None
        shot["download_url"] = None
        shot["local_path"] = None
        shot["status"] = "rendering"
    elif strat == "switch_template":
        new_pick = await planner.pick_template_for_shot(
            {**shot, "target_description": decision["target_description"]},
            arc=shot_list,
        )
        tid = new_pick["template_id"]
        shot["template_id"] = None if tid == "NONE" else tid
        shot["template_title"] = new_pick.get("template_title")
        shot["template_picked_reason"] = new_pick.get("rationale", "")
        shot["target_description"] = decision["target_description"]
        shot["prompt"] = new_pick["shot_prompt"]
        shot["parent_kind"] = "template" if shot["template_id"] else None
        shot["parent_video_id"] = shot["template_id"]
        shot["video_id"] = None
        shot["download_url"] = None
        shot["local_path"] = None
        shot["status"] = "rendering"
    elif strat == "revise_via_parent":
        prior_video_id = shot.get("video_id")
        if prior_video_id:
            shot["parent_video_id"] = prior_video_id
            shot["parent_kind"] = "prior_render"
        shot["prompt"] = decision["shot_prompt"]
        shot["video_id"] = None
        shot["download_url"] = None
        shot["local_path"] = None
        shot["status"] = "rendering"

    shot_list[idx] = shot
    return {"shot_list": shot_list}


# ---------------------------------------------------------------------------
# coherence_check — Loop B
# ---------------------------------------------------------------------------


async def coherence_check_node(state: dict[str, Any]) -> dict[str, Any]:
    idx = state["current_shot_idx"]
    shot_list: list[dict[str, Any]] = state["shot_list"]
    rendered_paths = [
        Path(s["local_path"])
        for s in shot_list[: idx + 1]
        if s.get("local_path")
    ]

    verdict = await coherence.check_coherence(
        list(shot_list),
        rendered_paths,
        state.get("brief_summary", ""),
        current_idx=idx,
    )

    diagnoses = list(state.get("coherence_diagnoses") or [])
    diagnoses.append({"after_idx": idx, **verdict})

    pending = verdict.get("suggested_edits") if not verdict.get("coherent") else []

    return {
        "coherence_diagnoses": diagnoses,
        "pending_coherence_edits": pending,
        # The shot just accepted — advance the cursor past it whether or not
        # we replan downstream.
        "current_shot_idx": idx + 1,
    }


# ---------------------------------------------------------------------------
# replanner — applies coherence's suggested_edits to downstream shots
# ---------------------------------------------------------------------------


async def replanner_node(state: dict[str, Any]) -> dict[str, Any]:
    edits = list(state.get("pending_coherence_edits") or [])
    shot_list: list[dict[str, Any]] = list(state["shot_list"])
    edited_indices: list[int] = []

    for edit in edits:
        target_idx = edit.get("idx")
        if not isinstance(target_idx, int) or target_idx >= len(shot_list):
            continue
        shot = dict(shot_list[target_idx])
        if edit.get("new_prompt"):
            shot["prompt"] = edit["new_prompt"]
        if edit.get("new_target_description"):
            shot["target_description"] = edit["new_target_description"]
            new_pick = await planner.pick_template_for_shot(shot, arc=shot_list)
            tid = new_pick["template_id"]
            shot["template_id"] = None if tid == "NONE" else tid
            shot["template_title"] = new_pick.get("template_title")
            shot["template_picked_reason"] = new_pick.get("rationale", "")
            shot["prompt"] = new_pick["shot_prompt"]
            shot["parent_kind"] = "template" if shot["template_id"] else None
            shot["parent_video_id"] = shot["template_id"]
        shot_list[target_idx] = shot
        edited_indices.append(target_idx)

    return {
        "shot_list": shot_list,
        "pending_coherence_edits": [],
        "replans": (state.get("replans") or 0) + 1,
        "last_replan_edited_indices": edited_indices,
    }


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


async def assemble(state: dict[str, Any]) -> dict[str, Any]:
    paths = [Path(s["local_path"]) for s in state["shot_list"] if s.get("local_path")]
    if not paths:
        return {"error": "No rendered shots to stitch"}
    final_dir = settings.RENDERS_DIR / state["run_id"]
    final_path = final_dir / "final.mp4"
    await stitch.stitch_concat(paths, final_path)
    return {"final_video_path": str(final_path)}
