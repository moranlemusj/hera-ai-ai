"""End-to-end test for POST /run with HERA_MOCK=1.

Strategy:
- Boot the FastAPI app via httpx ASGI transport (no real network).
- Force HERA_MOCK on so the Hera REST client returns the placeholder mp4.
- Replace the planner with a deterministic stub that returns a fixed shot list
  — this is the legitimate kind of test boundary mock (the planner is the LLM
  call we don't want to pay for in tests; the REST contract we're verifying is
  the graph → Hera → stitch flow, not Gemini).
- Provide source_article directly so we don't hit Jina either.
- Consume the SSE stream and assert the lifecycle events and final video path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from app.config import settings


def _stub_outline(*_: Any, **__: Any) -> dict[str, Any]:
    return {
        "brief_summary": "Test brief summarizing a short demo article.",
        "shots": [
            {
                "kind": "title",
                "target_description": "A bold title card introducing the topic.",
                "duration_seconds": 4.0,
                "rationale": "Establish the subject up front.",
            },
            {
                "kind": "kinetic_typo",
                "target_description": "Animated key statistic appearing on screen.",
                "duration_seconds": 4.0,
                "rationale": "Highlight a number from the article.",
            },
            {
                "kind": "logo_reveal",
                "target_description": "End logo stamp.",
                "duration_seconds": 3.0,
                "rationale": "Sign off the segment.",
            },
        ],
    }


def _stub_pick(shot: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": "NONE",
        "rationale": "test stub — prompt-only render",
        "shot_prompt": shot["target_description"],
        "template_title": None,
    }


@pytest_asyncio.fixture
async def stubbed_planner(monkeypatch):
    """Replace ALL Gemini-backed nodes with deterministic stubs.

    The graph now includes critic / strategist / coherence — without stubs
    they'd run against the placeholder blue mp4, fail to score it well, and
    thrash the strategist for hours. This test exists to verify the plumbing
    (graph wiring, SSE protocol, mp4 served), not to exercise the LLM. Loop
    A and Loop B integration tests cover the agentic paths separately.
    """
    from app.services import coherence, critic, planner, strategist

    async def _aoutline(*a, **k):
        return _stub_outline()

    async def _apick(shot, **_kwargs):
        return _stub_pick(shot)

    async def _agrade(_path, _shot, _brief, **_kwargs):
        return {
            "composition": "ok",
            "typography": "ok",
            "motion": "ok",
            "color": "ok",
            "text_legibility": "ok",
            "narrative_fit": "ok",
            "visual_consistency": "ok",
            "overall_score": 0.95,
            "notes": "stub: pass-through",
        }

    async def _astrat(_shot, _brief):
        return {"strategy": "accept", "rationale": "stub default"}

    async def _acoh(*_a, **_k):
        return {"coherent": True, "reason": "stub", "suggested_edits": []}

    monkeypatch.setattr(planner, "plan_outline", _aoutline)
    monkeypatch.setattr(planner, "pick_template_for_shot", _apick)
    monkeypatch.setattr(critic, "grade_shot", _agrade)
    monkeypatch.setattr(strategist, "pick_strategy", _astrat)
    monkeypatch.setattr(coherence, "check_coherence", _acoh)

    # Force the compiled-graph cache to rebuild so the stubs are visible.
    from app.graph import build as graph_build

    graph_build._compiled = None  # type: ignore[attr-defined]
    yield


@pytest_asyncio.fixture
async def app_client(monkeypatch, stubbed_planner):
    """Boot the real FastAPI app with HERA_MOCK=1, return an httpx ASGI client."""
    monkeypatch.setattr(settings, "HERA_MOCK", True)

    # Defer importing main until after the env is patched
    from app.main import app

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=120.0
        ) as client:
            yield client


def _parse_sse_lines(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: ") :]))
    return events


async def test_run_url_endpoint_streams_full_lifecycle(app_client) -> None:
    payload = {
        "source_article": {
            "title": "Test Topic",
            "byline": None,
            "text": "A short body of text simulating an article. " * 30,
        }
    }

    async with app_client.stream("POST", "/run", json=payload) as resp:
        assert resp.status_code == 200, await resp.aread()
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse_lines(body)
    types = [e["type"] for e in events]

    # Must observe each stage of the graph (v1).
    assert "log" in types  # the "started" log
    node_exits = [e for e in events if e["type"] == "node_exit"]
    seen_nodes = [e["node"] for e in node_exits]
    for expected in (
        "intake",
        "planner",
        "render_one",
        "poll_one",
        "critic",
        "coherence_check",
        "assemble",
    ):
        assert expected in seen_nodes, (
            f"missing node {expected} in stream; saw {seen_nodes}"
        )

    # Three shots, all critic-passing → render_one/poll_one/critic/coherence_check
    # fire exactly 3 times each (no strategist, no replanner).
    assert seen_nodes.count("render_one") == 3
    assert seen_nodes.count("poll_one") == 3
    assert seen_nodes.count("critic") == 3
    assert seen_nodes.count("coherence_check") == 3
    assert seen_nodes.count("strategist") == 0
    assert seen_nodes.count("replanner") == 0

    # Done event with a video URL
    done = [e for e in events if e["type"] == "done"]
    assert done, f"no done event, last events: {events[-5:]}"
    assert done[-1]["final_video_url"].startswith("/video/")

    # The final video file actually exists
    final_path = done[-1]["final_video_path"]
    assert Path(final_path).exists()
    assert Path(final_path).stat().st_size > 0


async def test_run_with_no_input_rejects(app_client) -> None:
    resp = await app_client.post("/run", json={})
    assert resp.status_code == 422
