"""Admin endpoints — internal management surface for the agent backend.

Mounted under `/admin/*` from main.py. The webapp / extension calls these for
session setup and template scraping.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.services import hera_session, templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Hera session
# ---------------------------------------------------------------------------


class CurlPayload(BaseModel):
    curl: str = Field(..., min_length=10, description="Pasted cURL command from DevTools")


class SessionStatusResponse(BaseModel):
    status: str
    expires_at: str | None
    last_validated: str | None
    seconds_until_expiry: float | None


@router.get("/hera_session", response_model=SessionStatusResponse)
async def get_hera_session() -> dict[str, Any]:
    return await hera_session.get_status()


@router.post("/hera_session", response_model=SessionStatusResponse)
async def update_hera_session(payload: CurlPayload) -> dict[str, Any]:
    try:
        return await hera_session.update_from_curl(payload.curl)
    except hera_session.HeraSessionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.delete("/hera_session", status_code=204)
async def delete_hera_session() -> None:
    await hera_session.clear_session()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


class RefreshPayload(BaseModel):
    category: str | None = Field(
        default=None,
        description=(
            "Single category to refresh; null/omitted refreshes all configured "
            "categories."
        ),
    )


@router.get("/templates_summary")
async def get_templates_summary() -> dict[str, Any]:
    return await templates.templates_summary()


@router.post("/refresh_templates")
async def refresh_templates(payload: RefreshPayload) -> EventSourceResponse:
    """Scrape templates and stream progress events.

    Each yielded event is one of:
      - { "category": ..., "page": ..., "count": ..., "inserted": ..., "updated": ... }
      - { "error": "..." } (and we abort)
      - { "done": true, "summary": {...} }
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=64)

    async def progress_cb(event: dict[str, Any]) -> None:
        await queue.put({"type": "progress", **event})

    async def runner() -> None:
        try:
            summary = await templates.scrape_all(payload.category, progress_cb)
            await queue.put({"type": "done", "summary": summary})
        except hera_session.HeraSessionExpiredError as exc:
            await queue.put({"type": "error", "code": exc.code, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("Refresh templates failed")
            await queue.put({"type": "error", "code": "scrape_failed", "message": str(exc)})
        finally:
            await queue.put(None)

    async def event_source():
        task = asyncio.create_task(runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"data": json.dumps(event)}
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(event_source())


@router.get("/templates/search")
async def templates_search(
    q: str = Query(..., min_length=1, description="Description / query text"),
    k: int = Query(5, ge=1, le=20),
    category: list[str] | None = Query(None, description="Optional category filters"),
    exclude_premium: bool = Query(True),
) -> dict[str, Any]:
    results = await templates.find_templates(
        q,
        category_hints=category,
        k=k,
        exclude_premium=exclude_premium,
    )
    return {"query": q, "k": k, "results": results}
