"""Pure routing tests for graph edges. No DB, no LLM, no Hera."""

from __future__ import annotations

from app.graph import edges


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


def test_poll_loops_back_when_more_shots() -> None:
    state = {
        "current_shot_idx": 1,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
    }
    assert edges.route_after_poll_one(state) == "render_one"


def test_poll_assembles_when_all_shots_done() -> None:
    state = {
        "current_shot_idx": 3,
        "shot_list": [{"idx": 0}, {"idx": 1}, {"idx": 2}],
    }
    assert edges.route_after_poll_one(state) == "assemble"


def test_planner_routes_to_end_when_empty_shot_list() -> None:
    state = {"shot_list": []}
    assert edges.route_after_planner(state) == "end"


def test_render_routes_to_end_on_error() -> None:
    state = {"error": "quota exhausted"}
    assert edges.route_after_render_one(state) == "end"
