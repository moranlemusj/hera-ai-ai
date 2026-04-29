"""Integration test for Loop B — coherence_check + replanner.

Critic always passes. Coherence flags an incoherence after shot 0 with a
suggested edit to shot 2; replanner should apply it before shot 2 renders.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.integration.conftest import boot_agent_app


def _stub_outline() -> dict[str, Any]:
    return {
        "brief_summary": "Loop B test brief.",
        "shots": [
            {"kind": "title", "target_description": "S1.", "duration_seconds": 3.0, "rationale": "open"},
            {"kind": "kinetic_typo", "target_description": "S2.", "duration_seconds": 4.0, "rationale": "body"},
            {"kind": "chart", "target_description": "S3.", "duration_seconds": 3.0, "rationale": "data"},
            {"kind": "logo_reveal", "target_description": "S4.", "duration_seconds": 3.0, "rationale": "close"},
        ],
    }


def _parse_sse(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_coherence_failure_triggers_replan_on_downstream_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coherence_call = {"n": 0}

    async def _acoherent(_shots, _paths, _brief, current_idx):
        coherence_call["n"] += 1
        # First call (after shot 0) → flag shot 2 for a prompt edit.
        if coherence_call["n"] == 1 and current_idx == 0:
            return {
                "coherent": False,
                "reason": "stub: shots 1+ need a different angle",
                "suggested_edits": [
                    {
                        "idx": 2,
                        "new_prompt": "EDITED_BY_REPLANNER",
                        "rationale": "stub edit",
                    }
                ],
            }
        return {"coherent": True, "reason": "stub", "suggested_edits": []}

    async def _aoutline(*_a, **_k):
        return _stub_outline()

    async with boot_agent_app(
        monkeypatch,
        plan_outline=_aoutline,
        check_coherence=_acoherent,
    ) as client, client.stream(
        "POST",
        "/run",
        json={
            "source_article": {
                "title": "Loop B Test",
                "byline": None,
                "text": "Body for Loop B integration test.",
            }
        },
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk

    events = _parse_sse(body)

    # First coherence verdict was incoherent with one suggested edit.
    coherence_events = [e for e in events if e["type"] == "coherence_diagnosis"]
    assert coherence_events, "no coherence_diagnosis events emitted"
    assert coherence_events[0]["coherent"] is False
    assert coherence_events[0]["suggested_edits_count"] == 1

    # Replanner applied at least one edit on shot idx 2.
    replan_events = [e for e in events if e["type"] == "replan_applied"]
    assert replan_events, "no replan_applied events emitted"
    assert 2 in replan_events[0]["edited_indices"]

    done = [e for e in events if e["type"] == "done"]
    assert done, f"no done event; tail: {events[-5:]}"
