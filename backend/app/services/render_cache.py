"""Content-keyed cache for Hera renders.

Same shot spec → same hash → reuse the local mp4. Particularly important when
quota is tight (200 vids/month) and during agent reruns / restarts.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TypedDict

from app.db import get_conn

log = logging.getLogger(__name__)


class CachedRender(TypedDict):
    video_id: str
    download_url: str | None
    local_path: str | None


def cache_key(
    prompt: str,
    parent_video_id: str | None,
    aspect: str,
    duration_seconds: float,
    fps: int,
    resolution: str,
) -> str:
    """sha256 hex of all fields that influence the rendered output."""
    parts = [
        prompt,
        parent_video_id or "",
        aspect,
        f"{duration_seconds:.3f}",
        str(fps),
        resolution,
    ]
    payload = "\x1f".join(parts).encode("utf-8")  # \x1f = unit separator
    return hashlib.sha256(payload).hexdigest()


async def get_cached(key: str) -> CachedRender | None:
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT video_id, download_url, local_path FROM render_cache WHERE cache_key = %s",
            (key,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    return CachedRender(video_id=row[0], download_url=row[1], local_path=row[2])


async def record_hit(key: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE render_cache SET last_used_at = NOW(), hit_count = hit_count + 1 "
            "WHERE cache_key = %s",
            (key,),
        )


async def store(
    key: str,
    *,
    video_id: str,
    download_url: str | None,
    local_path: str | None,
) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO render_cache (cache_key, video_id, download_url, local_path)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                video_id     = EXCLUDED.video_id,
                download_url = EXCLUDED.download_url,
                local_path   = EXCLUDED.local_path,
                last_used_at = NOW()
            """,
            (key, video_id, download_url, local_path),
        )
