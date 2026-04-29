"""Pure routing tests for graph edges. No DB, no LLM, no Hera."""

from __future__ import annotations

from app.graph import edges

# ---------------------------------------------------------------------------
# Linear part of the graph (intake → fetch_article → planner → render_one → poll_one)
# ---------------------------------------------------------------------------


def test_intake_routes_to_fetch_when_url_only() -> None:
    state = {"source_url": "https://x.com", "source_article": None}
    assert edges.route_after_intake(state) == "fetch_article"


def test_intake_routes_to_planner_when_prompt_only() -> None:
    state = {"user_prompt": "hello", "source_url": None}
    assert edges.route_after_intake(state) == "planner"


def test_intake_routes_to_planner_when_extension_payload_present() -> None:
    state = {"source_article": {"title": "T", "text": "..."}, "source_url": None}
    assert edges.route_after_intake(state) == "planner"


def test_intake_routes_to_end_on_error() -> None:
    state = {"error": "broken", "source_url": "x"}
    assert edges.route_after_intake(state) == "end"


def test_planner_routes_to_end_when_empty_shot_list() -> None:
    state = {"shot_list": []}
    assert edges.route_after_planner(state) == "end"


def test_render_routes_to_end_on_error() -> None:
    state = {"error": "quota exhausted"}
    assert edges.route_after_render_one(state) == "end"


def test_poll_routes_to_critic() -> None:
    """v1: poll_one → critic unconditionally (was → render_one/assemble in v0)."""
    state = {
        "current_shot_idx": 1,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
    }
    assert edges.route_after_poll_one(state) == "critic"


def test_poll_routes_to_end_on_error() -> None:
    assert edges.route_after_poll_one({"error": "boom"}) == "end"


# ---------------------------------------------------------------------------
# Loop A — critic + strategist
# ---------------------------------------------------------------------------


def test_critic_routes_to_coherence_when_score_above_threshold() -> None:
    state = {
        "current_shot_idx": 0,
        "shot_list": [{"idx": 0, "score": 0.95, "attempts": [{}]}],
    }
    assert edges.route_after_critic(state) == "coherence_check"


def test_critic_routes_to_strategist_when_score_below_threshold_and_attempts_left() -> None:
    state = {
        "current_shot_idx": 0,
        "shot_list": [{"idx": 0, "score": 0.4, "attempts": [{}]}],
    }
    assert edges.route_after_critic(state) == "strategist"


def test_critic_falls_through_to_coherence_when_attempts_exhausted() -> None:
    """Auto-accept when MAX_ATTEMPTS_PER_SHOT (default 4) reached."""
    state = {
        "current_shot_idx": 0,
        "shot_list": [{"idx": 0, "score": 0.1, "attempts": [{}, {}, {}, {}]}],
    }
    assert edges.route_after_critic(state) == "coherence_check"


def test_strategist_routes_to_render_one_when_shot_set_to_rendering() -> None:
    state = {
        "current_shot_idx": 0,
        "shot_list": [{"idx": 0, "status": "rendering"}],
    }
    assert edges.route_after_strategist(state) == "render_one"


def test_strategist_routes_to_coherence_when_shot_approved() -> None:
    state = {
        "current_shot_idx": 0,
        "shot_list": [{"idx": 0, "status": "approved"}],
    }
    assert edges.route_after_strategist(state) == "coherence_check"


def test_strategist_routes_to_end_when_error_set() -> None:
    state = {"error": "strategist escalated", "shot_list": [{"idx": 0}]}
    assert edges.route_after_strategist(state) == "end"


# ---------------------------------------------------------------------------
# Loop B — coherence + replanner
# ---------------------------------------------------------------------------


def test_coherence_routes_to_replanner_when_edits_pending_and_budget_left() -> None:
    state = {
        "current_shot_idx": 1,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
        "pending_coherence_edits": [{"idx": 2, "rationale": "tighten"}],
        "replans": 0,
    }
    assert edges.route_after_coherence_check(state) == "replanner"


def test_coherence_routes_to_render_one_when_more_shots_remain() -> None:
    state = {
        "current_shot_idx": 1,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
        "pending_coherence_edits": [],
    }
    assert edges.route_after_coherence_check(state) == "render_one"


def test_coherence_routes_to_assemble_when_all_shots_done() -> None:
    state = {
        "current_shot_idx": 3,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
        "pending_coherence_edits": [],
    }
    assert edges.route_after_coherence_check(state) == "assemble"


def test_coherence_skips_replanner_when_budget_exhausted() -> None:
    """Even with pending edits, MAX_REPLANS overrides — continue normally."""
    state = {
        "current_shot_idx": 1,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
        "pending_coherence_edits": [{"idx": 2}],
        "replans": 99,  # well over MAX_REPLANS
    }
    assert edges.route_after_coherence_check(state) == "render_one"


def test_replanner_routes_to_render_one() -> None:
    state = {
        "current_shot_idx": 1,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
    }
    assert edges.route_after_replanner(state) == "render_one"


def test_replanner_routes_to_assemble_when_no_more_shots() -> None:
    state = {
        "current_shot_idx": 3,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
    }
    assert edges.route_after_replanner(state) == "assemble"
