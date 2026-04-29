"""Integration test: find_templates filters by template duration when target given.

Seeds two templates — one with a fixed 60s duration, one with "AUTO" — and
verifies that searching with `target_duration_seconds=5` returns only the
AUTO template (60s is way outside the ±5s window).
"""

from __future__ import annotations

from app.db import get_conn
from app.services.templates import find_templates, upsert_template
from tests.integration.conftest import TEST_CATEGORY, make_record, tid


async def _set_duration(task_prompt_id: str, duration_value: str | None) -> None:
    """Patch a row's `config.durationSeconds` to a specific value."""
    async with get_conn() as conn:
        if duration_value is None:
            cfg_sql = "config = jsonb_set(coalesce(config, '{}'::jsonb), '{durationSeconds}', 'null'::jsonb, true)"
        else:
            cfg_sql = (
                "config = jsonb_set(coalesce(config, '{}'::jsonb), "
                "'{durationSeconds}', to_jsonb(%s::text), true)"
            )
        if duration_value is None:
            await conn.execute(
                f"UPDATE templates SET {cfg_sql} WHERE task_prompt_id = %s",
                (task_prompt_id,),
            )
        else:
            await conn.execute(
                f"UPDATE templates SET {cfg_sql} WHERE task_prompt_id = %s",
                (duration_value, task_prompt_id),
            )


async def test_duration_filter_excludes_long_templates(clean_test_rows) -> None:
    # Seed a 60s and an AUTO template, both matching the same description.
    await upsert_template(
        make_record(
            1,
            title="Long template",
            summary="A motion graphic suitable for the topic of testing.",
            category=TEST_CATEGORY,
        )
    )
    await _set_duration(tid(1), "60")

    await upsert_template(
        make_record(
            2,
            title="Auto template",
            summary="A motion graphic suitable for the topic of testing.",
            category=TEST_CATEGORY,
        )
    )
    await _set_duration(tid(2), "AUTO")

    # Without a duration target — both pass.
    no_filter = await find_templates(
        "topic of testing", category_hints=[TEST_CATEGORY], k=5
    )
    no_filter_ids = {h["task_prompt_id"] for h in no_filter}
    assert tid(1) in no_filter_ids
    assert tid(2) in no_filter_ids

    # With target=5 — the 60s template gets filtered out.
    filtered = await find_templates(
        "topic of testing",
        category_hints=[TEST_CATEGORY],
        k=5,
        target_duration_seconds=5.0,
    )
    filtered_ids = {h["task_prompt_id"] for h in filtered}
    assert tid(1) not in filtered_ids, (
        "60s template should be filtered out when target=5s (±5)"
    )
    assert tid(2) in filtered_ids, "AUTO template should always pass"


async def test_duration_filter_includes_in_window(clean_test_rows) -> None:
    """A template with duration == target ± buffer is included."""
    await upsert_template(
        make_record(
            1,
            title="Window template",
            summary="Animation about windows of time.",
            category=TEST_CATEGORY,
        )
    )
    await _set_duration(tid(1), "8")  # within ±5 of target 5

    hits = await find_templates(
        "windows of time",
        category_hints=[TEST_CATEGORY],
        k=5,
        target_duration_seconds=5.0,
    )
    assert tid(1) in {h["task_prompt_id"] for h in hits}


async def test_duration_filter_excludes_out_of_window(clean_test_rows) -> None:
    """A template whose duration is outside ±buffer is excluded."""
    await upsert_template(
        make_record(
            1,
            title="Way too long",
            summary="A really long meditation animation.",
            category=TEST_CATEGORY,
        )
    )
    await _set_duration(tid(1), "20")  # ±5 of target 3 means 20 is out

    hits = await find_templates(
        "meditation animation",
        category_hints=[TEST_CATEGORY],
        k=5,
        target_duration_seconds=3.0,
    )
    assert tid(1) not in {h["task_prompt_id"] for h in hits}
