"""Hera public REST API client (api.hera.video, x-api-key auth).

Distinct from `hera_dashboard.py` which uses cookie auth for the unofficial
templates endpoint. This module hits the documented Hera REST surface to
create renders, poll status, and download mp4s.

HERA_MOCK=1 short-circuits every call to a deterministic placeholder mp4 so
dev work doesn't burn the 200-vid/month quota.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.db import get_conn

log = logging.getLogger(__name__)

API_BASE = "https://api.hera.video"
RENDERS_PATH = "/v1/videos"
DEFAULT_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 300.0

_MOCK_DIR = Path(__file__).parent / "_mock_assets"
_MOCK_PLACEHOLDER = _MOCK_DIR / "placeholder.mp4"
_PLACEHOLDER_READY = False

_client: httpx.AsyncClient | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HeraApiError(Exception):
    """Non-401, non-quota Hera failures."""


class HeraApiKeyInvalidError(HeraApiError):
    """REST API returned 401 — HERA_API_KEY is wrong / revoked.

    Distinct from cookie-based dashboard auth (HeraSessionExpiredError in
    services/hera_session.py): this is a server-config problem, not a
    runtime cookie expiry. The agent run cannot recover without an operator
    fixing `.env` and restarting; it ends with state.error.
    """


class HeraQuotaExceededError(Exception):
    """Raised when the monthly render budget is exhausted.

    Recoverable: an operator can raise the cap (Hera plan upgrade or
    increased MONTHLY_RENDER_HARD_CAP), at which point the run resumes.
    The graph surfaces this as the `hera_quota_exhausted` interrupt.
    """


class HeraRenderFailedError(Exception):
    """Raised when Hera reports `status=failed` for a render."""


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        if not settings.HERA_API_KEY and not settings.HERA_MOCK:
            raise RuntimeError("HERA_API_KEY not set; either set it or enable HERA_MOCK=1.")
        _client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=DEFAULT_TIMEOUT,
            headers={"x-api-key": settings.HERA_API_KEY} if settings.HERA_API_KEY else {},
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Mock placeholder
# ---------------------------------------------------------------------------


async def ensure_placeholder() -> Path:
    """Generate the mock placeholder mp4 if missing. Idempotent.

    Should be called once at startup when HERA_MOCK is enabled (see lifespan).
    Subsequent calls are O(1) — we cache the ready flag instead of stat'ing
    the file on every render call.
    """
    global _PLACEHOLDER_READY
    if _PLACEHOLDER_READY:
        return _MOCK_PLACEHOLDER
    if _MOCK_PLACEHOLDER.exists() and _MOCK_PLACEHOLDER.stat().st_size > 0:
        _PLACEHOLDER_READY = True
        return _MOCK_PLACEHOLDER
    _MOCK_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Generating mock placeholder mp4 at %s", _MOCK_PLACEHOLDER)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x180:d=1:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(_MOCK_PLACEHOLDER),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to generate placeholder: {stderr[-200:]!r}")
    _PLACEHOLDER_READY = True
    return _MOCK_PLACEHOLDER


# ---------------------------------------------------------------------------
# Quota tracking
# ---------------------------------------------------------------------------


def _month_start_utc() -> datetime:
    now = datetime.now(tz=UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_monthly_render_count() -> int:
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT video_count FROM hera_usage WHERE month = %s",
            (_month_start_utc().date(),),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def record_render_used() -> int:
    """Increment this month's video_count. Returns the new count."""
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            INSERT INTO hera_usage (month, video_count, last_video_at)
            VALUES (%s, 1, NOW())
            ON CONFLICT (month) DO UPDATE
                SET video_count = hera_usage.video_count + 1,
                    last_video_at = NOW()
            RETURNING video_count
            """,
            (_month_start_utc().date(),),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


# Runtime override for the monthly cap. Set by the `hera_quota_exhausted`
# interrupt resume when an operator confirms they've expanded the budget
# upstream (e.g. upgraded the Hera plan or rolled into a new month).
# Process-local; resets on restart.
_quota_override: int | None = None


def set_quota_override(new_cap: int) -> None:
    """Lift the monthly cap to `new_cap` for the rest of this process's life."""
    global _quota_override
    _quota_override = new_cap


def effective_cap() -> int:
    """The cap the agent should respect right now (override beats config)."""
    return _quota_override if _quota_override is not None else settings.MONTHLY_RENDER_HARD_CAP


async def check_quota_or_raise() -> None:
    """Raise HeraQuotaExceededError if at or above the effective monthly cap."""
    if settings.HERA_MOCK:
        return
    count = await get_monthly_render_count()
    cap = effective_cap()
    if count >= cap:
        raise HeraQuotaExceededError(
            f"Monthly Hera render cap reached: {count}/{cap}"
        )
    if count >= settings.MONTHLY_RENDER_WARN_THRESHOLD:
        log.warning("Approaching Hera quota: %d / %d this month", count, cap)


# ---------------------------------------------------------------------------
# Real API calls
# ---------------------------------------------------------------------------


def _mock_video_id(prompt: str, parent_video_id: str | None) -> str:
    h = hashlib.sha256(f"{prompt}|{parent_video_id or ''}".encode()).hexdigest()[:12]
    return f"mock-{h}"


async def create_render(
    prompt: str,
    *,
    aspect: str = "16:9",
    fps: int = 30,
    resolution: str = "1080p",
    duration_seconds: float = 5.0,
    parent_video_id: str | None = None,
    style_id: str | None = None,
    reference_image_urls: list[str] | None = None,
) -> str:
    """Create a Hera render job. Returns the video_id."""
    if settings.HERA_MOCK:
        await ensure_placeholder()
        vid = _mock_video_id(prompt, parent_video_id)
        log.info("[MOCK] create_render → %s", vid)
        return vid

    # Hera's outputs schema requires `fps` as a string from {"24","25","30","60"}.
    # We accept int in our public signature for ergonomics and cast here.
    body: dict[str, Any] = {
        "prompt": prompt,
        "outputs": [
            {
                "format": "mp4",
                "aspect_ratio": aspect,
                "fps": str(fps),
                "resolution": resolution,
            }
        ],
        "duration_seconds": duration_seconds,
    }
    if parent_video_id:
        body["parent_video_id"] = parent_video_id
    if style_id:
        body["style_id"] = style_id
    if reference_image_urls:
        body["reference_image_urls"] = reference_image_urls

    client = await get_client()

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(httpx.TransportError),
        reraise=True,
    ):
        with attempt:
            resp = await client.post(RENDERS_PATH, json=body)

    if resp.status_code == 401:
        raise HeraApiKeyInvalidError(
            "Hera REST API returned 401 — HERA_API_KEY is invalid or revoked. "
            "Update .env and restart the backend."
        )
    if resp.status_code >= 400:
        raise HeraApiError(f"create_render {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    video_id = data.get("video_id")
    if not video_id:
        raise HeraApiError(f"create_render returned no video_id: {data}")
    await record_render_used()
    log.info("create_render → %s", video_id)
    return str(video_id)


_MOCK_CREATED: dict[str, datetime] = {}
_MOCK_RENDER_DURATION = timedelta(seconds=2.0)


async def poll_render(video_id: str) -> dict[str, Any]:
    """Returns {status, exports?, error?}.

    status ∈ {queued, rendering, ready, failed}.
    """
    if settings.HERA_MOCK:
        # Mirror the real Hera response shape so any future shape mismatch
        # surfaces in mock-mode tests instead of biting us live (status enum
        # is "in-progress" | "success" | "failed"; outputs[].file_url).
        now = datetime.now(tz=UTC)
        started = _MOCK_CREATED.setdefault(video_id, now)
        if now - started < _MOCK_RENDER_DURATION:
            return {"status": "in-progress"}
        await ensure_placeholder()
        return {
            "status": "success",
            "outputs": [
                {
                    "status": "success",
                    "file_url": f"file://{_MOCK_PLACEHOLDER.resolve()}",
                    "config": {
                        "format": "mp4",
                        "aspect_ratio": "16:9",
                        "fps": "30",
                        "resolution": "1080p",
                    },
                }
            ],
        }

    client = await get_client()
    resp = await client.get(f"{RENDERS_PATH}/{video_id}")
    if resp.status_code == 401:
        raise HeraApiKeyInvalidError(
            "Hera REST API returned 401 — HERA_API_KEY is invalid or revoked. "
            "Update .env and restart the backend."
        )
    if resp.status_code >= 400:
        raise HeraApiError(f"poll_render {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def download_render(url: str, dest: Path) -> Path:
    """Stream the rendered mp4 to disk. Handles file:// URLs in mock mode."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("file://"):
        src = Path(url[len("file://") :])
        shutil.copyfile(src, dest)
        return dest

    async with (
        httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client,
        client.stream("GET", url) as resp,
    ):
        if resp.status_code >= 400:
            raise HeraApiError(f"download_render {resp.status_code}")
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes():
                fh.write(chunk)
    return dest
