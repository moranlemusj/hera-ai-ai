"""POST /run — kick off an agent run, stream LangGraph events as SSE.
POST /resume/{thread_id} — resume from an interrupt.
GET  /video/{run_id} — serve the final mp4.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.graph.build import get_compiled_graph

log = logging.getLogger(__name__)

router = APIRouter(tags=["run"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ArticlePayload(BaseModel):
    title: str = ""
    byline: str | None = None
    text: str
    url: str | None = None


class RunPayload(BaseModel):
    user_prompt: str | None = Field(None, min_length=1)
    source_url: str | None = Field(None, min_length=1)
    source_article: ArticlePayload | None = None

    @model_validator(mode="after")
    def at_least_one_input(self) -> RunPayload:
        if not (self.user_prompt or self.source_url or self.source_article):
            raise ValueError(
                "At least one of user_prompt, source_url, source_article is required."
            )
        return self


class ResumePayload(BaseModel):
    """Body of POST /resume/{thread_id}.

    Interrupt kinds and the field they consume:
      - hera_quota_exhausted: optional `new_cap` (int) — operator-confirmed
        new in-process cap. Empty body == "I've expanded upstream, just retry".
      - (v1) plan_review: structured plan edits in `extra`.
      - (v1) escalation: free-form guidance in `extra`.
    """

    new_cap: int | None = None
    extra: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------


def _event(payload: dict[str, Any]) -> dict[str, str]:
    # default=repr ensures any unexpected non-JSON value (e.g. an exception
    # echoed via state) serializes as its repr instead of crashing the stream.
    return {"data": json.dumps(payload, default=repr)}


async def _stream_graph(
    initial_input: Any,
    thread_id: str,
    *,
    is_resume: bool = False,
) -> AsyncIterator[dict[str, str]]:
    graph = await get_compiled_graph()
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    yield _event(
        {"type": "log", "level": "info", "message": ("resumed" if is_resume else "started")}
    )

    try:
        async for chunk in graph.astream(
            initial_input, config=config, stream_mode="updates"
        ):
            if not isinstance(chunk, dict):
                continue
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":
                    # An interrupt fired. Surface the payload to the client.
                    interrupts = update if isinstance(update, list | tuple) else [update]
                    for itr in interrupts:
                        value = getattr(itr, "value", itr)
                        kind = (
                            value.get("kind", "interrupt")
                            if isinstance(value, dict)
                            else "interrupt"
                        )
                        yield _event(
                            {
                                "type": "interrupt",
                                "kind": kind,
                                "payload": value,
                                "thread_id": thread_id,
                            }
                        )
                    return  # Stop streaming; client must call /resume
                yield _event({"type": "node_exit", "node": node_name, "patch": update})
                # Convenience events for the UI
                if isinstance(update, dict):
                    if update.get("error"):
                        yield _event(
                            {"type": "log", "level": "error", "message": update["error"]}
                        )
                    if "shot_list" in update:
                        for shot in update["shot_list"]:
                            yield _event(
                                {
                                    "type": "shot_status",
                                    "idx": shot.get("idx"),
                                    "status": shot.get("status"),
                                    "video_id": shot.get("video_id"),
                                    "template_title": shot.get("template_title"),
                                    "template_id": shot.get("template_id"),
                                    "template_picked_reason": shot.get("template_picked_reason"),
                                }
                            )
                    if update.get("final_video_path"):
                        yield _event(
                            {
                                "type": "done",
                                "final_video_url": _video_url_for(update["final_video_path"]),
                                "final_video_path": update["final_video_path"],
                            }
                        )
    except asyncio.CancelledError:
        log.info("SSE stream cancelled by client (thread_id=%s)", thread_id)
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Graph stream failed: %s", exc)
        yield _event({"type": "log", "level": "error", "message": str(exc)})


def _video_url_for(local_path: str) -> str:
    p = Path(local_path)
    # expected: data/renders/{run_id}/final.mp4
    return f"/video/{p.parent.name}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run")
async def start_run(payload: RunPayload) -> EventSourceResponse:
    thread_id = str(uuid.uuid4())
    initial_state: dict[str, Any] = {
        "user_prompt": payload.user_prompt,
        "source_url": payload.source_url,
        "source_article": payload.source_article.model_dump() if payload.source_article else None,
    }
    return EventSourceResponse(
        _stream_graph(initial_state, thread_id),
        headers={"x-thread-id": thread_id},
    )


@router.post("/resume/{thread_id}")
async def resume_run(thread_id: str, payload: ResumePayload) -> EventSourceResponse:
    # We forward the resolution as Command(resume=...). The interrupt-receiving
    # node should handle whichever shape is appropriate.
    resolution: dict[str, Any] = {}
    if payload.new_cap is not None:
        resolution["new_cap"] = payload.new_cap
    if payload.extra:
        resolution.update(payload.extra)
    return EventSourceResponse(
        _stream_graph(Command(resume=resolution), thread_id, is_resume=True),
        headers={"x-thread-id": thread_id},
    )


@router.get("/video/{run_id}")
async def get_video(run_id: str) -> FileResponse:
    final_path = settings.RENDERS_DIR / run_id / "final.mp4"
    if not final_path.exists():
        raise HTTPException(404, f"No final video for run_id={run_id}")
    return FileResponse(final_path, media_type="video/mp4", filename=f"{run_id}.mp4")
