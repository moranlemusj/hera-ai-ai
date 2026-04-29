"""Integration test for Loop A — critic + strategist + bounded retry.

The critic is stubbed to fail shot 0 once then pass; the strategist is
stubbed to pick `rewrite_prompt`. We assert on the SSE event stream that
the strategist actually fired and a second render happened.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.integration.conftest import boot_agent_app


def _stub_outline() -> dict[str, Any]:
    return {
        "brief_summary": "Loop A test brief.",
        "shots": [
            {"kind": "title", "target_description": "S1.", "duration_seconds": 4.0, "rationale": "open"},
            {"kind": "logo_reveal", "target_description": "S2.", "duration_seconds": 3.0, "rationale": "close"},
        ],
    }


def _parse_sse(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_critic_rejects_shot_then_strategist_rewrites_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Per-shot critic call counter: shot 0 attempt 1 = bad, all else = good.
    critic_calls: dict[int, int] = {}

    async def _agrade(_path, shot, _brief, **_kwargs):
        idx = int(shot.get("idx", 0))
        attempt = critic_calls.get(idx, 0) + 1
        critic_calls[idx] = attempt
        if idx == 0 and attempt == 1:
            return {
                "composition": "weak",
                "typography": "weak",
                "motion": "ok",
                "color": "ok",
                "text_legibility": "weak",
                "narrative_fit": "ok",
                "visual_consistency": "n/a",
                "overall_score": 0.3,
                "notes": "stub: title illegible",
            }
        return {
            "composition": "ok",
            "typography": "ok",
            "motion": "ok",
            "color": "ok",
            "text_legibility": "ok",
            "narrative_fit": "ok",
            "visual_consistency": "ok",
            "overall_score": 0.95,
            "notes": "stub: looks good",
        }

    async def _astrategy(shot, _brief):
        return {
            "strategy": "rewrite_prompt",
            "rationale": "stub: rewrote with stronger contrast",
            "shot_prompt": (shot.get("prompt") or "") + " (revised)",
        }

    async def _aoutline(*_a, **_k):
        return _stub_outline()

    async with boot_agent_app(
        monkeypatch,
        plan_outline=_aoutline,
        grade_shot=_agrade,
        pick_strategy=_astrategy,
    ) as client, client.stream(
        "POST",
        "/run",
        json={
            "source_article": {
                "title": "Loop A Test",
                "byline": None,
                "text": "Body for the Loop A integration test, long enough to plan.",
            }
        },
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse(body)

    # Critic fired ≥3 times: shot 0 ×2 (fail then pass), shot 1 ×1 (pass).
    critic_events = [e for e in events if e["type"] == "critic_diagnosis"]
    assert len(critic_events) >= 3, (
        f"expected ≥3 critic_diagnosis, got {len(critic_events)}"
    )

    # Strategist fired exactly once for shot 0 with rewrite_prompt.
    strategist_events = [e for e in events if e["type"] == "strategist_decision"]
    assert len(strategist_events) == 1
    assert strategist_events[0]["idx"] == 0
    assert strategist_events[0]["strategy"] == "rewrite_prompt"

    done = [e for e in events if e["type"] == "done"]
    assert done, f"no done event; tail: {events[-5:]}"
