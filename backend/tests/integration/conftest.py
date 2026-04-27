"""Shared fixtures for integration tests.

Strategy: every test row uses a UUID with the prefix ``00000000-0000-0000-0000-``.
Real Hera-scraped templates use random UUIDs and never collide with that
prefix, so cleanup deletes only test data and leaves the user's ~2k embedded
templates untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from app.db import get_conn

TEST_UUID_PREFIX = "00000000-0000-0000-0000-"
# Sentinel category not used by Hera — keeps test rows out of `find_templates`
# results when callers don't filter by ID.
TEST_CATEGORY = "test"


def tid(n: int) -> str:
    """Stable test UUID like 00000000-0000-0000-0000-000000000001."""
    return f"{TEST_UUID_PREFIX}{n:012x}"


async def _delete_test_rows() -> None:
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM templates WHERE task_prompt_id::text LIKE %s",
            (TEST_UUID_PREFIX + "%",),
        )


@pytest_asyncio.fixture
async def clean_test_rows() -> AsyncIterator[None]:
    """Wipe rows with the test UUID prefix before AND after each test."""
    await _delete_test_rows()
    try:
        yield
    finally:
        await _delete_test_rows()


def make_record(
    n: int,
    *,
    title: str = "Test Title",
    summary: str = "Test summary describing some animation.",
    category: str = TEST_CATEGORY,
    used: int = 0,
    is_premium: bool = False,
    is_ready: bool = True,
) -> dict:
    """Build a Hera-shaped template record for tests."""
    return {
        "task_prompt_id": tid(n),
        "task_id": tid(n + 10_000),
        "title": title,
        "summary": summary,
        "category": category,
        "tags": ["test"],
        "liked": 0,
        "used": used,
        "is_premium": is_premium,
        "is_ready": is_ready,
        "thumbnail_url": "https://example.com/t.png",
        "preview_video_url": None,
        "task_prompts": {"config": {"aspect_ratio": "16/9"}},
    }
