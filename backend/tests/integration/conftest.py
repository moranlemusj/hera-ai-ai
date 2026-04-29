"""Shared fixtures for integration tests.

Strategy: every test row uses a UUID with the prefix ``00000000-0000-0000-0000-``.
Real Hera-scraped templates use random UUIDs and never collide with that
prefix, so cleanup deletes only test data and leaves the user's ~2k embedded
templates untouched.

Also: a shared `boot_agent_app` helper for tests that need to run the full
LangGraph against a stubbed planner/critic/strategist/coherence — Loop A
and Loop B integration tests both consume this.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

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


# ---------------------------------------------------------------------------
# Agent app harness — used by tests that exercise the full LangGraph against
# stubbed Gemini-backed services. Each test stubs only the behaviors it
# wants; the helper handles HERA_MOCK + LifespanManager + ASGI client.
# ---------------------------------------------------------------------------


def _stub_pick_template(shot: dict) -> dict:
    """Default template-pick stub: prompt-only, no real find_templates call."""
    return {
        "template_id": "NONE",
        "rationale": "test stub — prompt-only render",
        "shot_prompt": shot["target_description"],
        "template_title": None,
    }


@asynccontextmanager
async def boot_agent_app(
    monkeypatch: Any,
    *,
    plan_outline: Callable[..., Any],
    grade_shot: Callable[..., Any] | None = None,
    pick_strategy: Callable[..., Any] | None = None,
    check_coherence: Callable[..., Any] | None = None,
    pick_template_for_shot: Callable[..., Any] | None = None,
):
    """Boot the FastAPI app with HERA_MOCK=1 and the given Gemini stubs.

    Yields an httpx ASGI client. Tests need only supply the stub functions
    they care about; defaults are pass-through (critic always passes,
    strategist accepts, coherence is coherent).
    """
    from app.config import settings as app_settings
    from app.services import coherence, critic, planner, strategist

    monkeypatch.setattr(app_settings, "HERA_MOCK", True)

    async def _default_grade(*_a, **_k):
        return {
            "composition": "ok",
            "typography": "ok",
            "motion": "ok",
            "color": "ok",
            "text_legibility": "ok",
            "narrative_fit": "ok",
            "visual_consistency": "ok",
            "overall_score": 0.95,
            "notes": "stub: pass",
        }

    async def _default_strategy(*_a, **_k):
        return {"strategy": "accept", "rationale": "stub default"}

    async def _default_coherent(*_a, **_k):
        return {"coherent": True, "reason": "stub", "suggested_edits": []}

    async def _default_pick(shot, *, arc=None):  # noqa: ARG001
        return _stub_pick_template(shot)

    monkeypatch.setattr(planner, "plan_outline", plan_outline)
    monkeypatch.setattr(planner, "pick_template_for_shot", pick_template_for_shot or _default_pick)
    monkeypatch.setattr(critic, "grade_shot", grade_shot or _default_grade)
    monkeypatch.setattr(strategist, "pick_strategy", pick_strategy or _default_strategy)
    monkeypatch.setattr(coherence, "check_coherence", check_coherence or _default_coherent)

    # Force the compiled-graph singleton to rebuild against the patched modules.
    from app.graph import build as graph_build

    graph_build._compiled = None  # type: ignore[attr-defined]

    from app.main import app

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=180.0
        ) as client:
            yield client
