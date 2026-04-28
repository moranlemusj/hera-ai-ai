"""Opt-in contract test against the LIVE Hera REST API.

The point: catch request/response shape drift if Hera ever changes their
schema. The rest of the test suite uses HERA_MOCK=1 which short-circuits the
serialization path entirely.

This test costs ~1 Hera video credit per run. It is **skipped by default**
and only runs when:
  - HERA_LIVE=1 is set in the environment
  - HERA_API_KEY is set
  - HERA_MOCK is NOT 1 (mock would defeat the purpose)

To run:
    HERA_LIVE=1 pytest tests/integration/test_hera_api_live.py -v

Smallest possible spec is used (360p, 1:1, 1s, 24fps) to minimize cost.
"""

from __future__ import annotations

import os

import pytest

from app.config import settings
from app.services import hera_api


# Each tier of skip with a distinct reason so a misconfigured opt-in shows
# something more useful than "skipped".
def _skip_reason() -> str | None:
    if os.getenv("HERA_LIVE") != "1":
        return "HERA_LIVE != '1' — opt in to spend Hera quota"
    if not settings.HERA_API_KEY:
        return "HERA_API_KEY not set"
    if settings.HERA_MOCK:
        return "HERA_MOCK=1 — disable mock to hit real Hera"
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")


@pytest.mark.asyncio
async def test_create_render_accepts_our_request_shape() -> None:
    """A real Hera POST /v1/videos call with the smallest legal spec must succeed.

    Validates the wire shape we build in `hera_api.create_render` — most
    importantly, that fps is sent as a string, aspect_ratio + resolution are
    valid enum values, and the response yields a non-empty video_id.
    """
    video_id = await hera_api.create_render(
        prompt="contract test — solid color background",
        aspect="1:1",
        fps=24,
        resolution="360p",
        duration_seconds=1.0,
    )
    assert isinstance(video_id, str)
    assert video_id, "Hera returned an empty video_id"


@pytest.mark.asyncio
async def test_poll_render_returns_known_status_values() -> None:
    """A poll on a freshly created job must return a recognizable status."""
    video_id = await hera_api.create_render(
        prompt="contract test — solid color background",
        aspect="1:1",
        fps=24,
        resolution="360p",
        duration_seconds=1.0,
    )
    result = await hera_api.poll_render(video_id)
    assert isinstance(result, dict)
    status = result.get("status")
    assert status in {"queued", "rendering", "ready", "failed"}, (
        f"unexpected status from Hera: {status!r}; full body: {result}"
    )
