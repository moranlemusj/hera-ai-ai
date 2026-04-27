"""Integration tests for the templates module against a real Neon database.

These exercise the actual SQL — INSERT…ON CONFLICT, two-stage HNSW search,
content_hash skip-vs-refresh, category-scoped stale marking. They run against
the live ``NEON_DATABASE_URL`` but only touch rows with the
``00000000-0000-0000-0000-`` prefix; real scraped templates are untouched.

Cost: each new (title, summary) pair costs one Gemini embedding call. The
in-process LRU short-circuits repeats within a single pytest run, so a full
run is ~5-8 Gemini calls — well inside the free tier.
"""

from __future__ import annotations

from app.db import get_conn
from app.services.templates import (
    find_templates,
    mark_stale_except,
    templates_summary,
    upsert_template,
)
from tests.integration.conftest import (
    TEST_CATEGORY,
    make_record,
    tid,
)


async def _row(task_prompt_id: str) -> tuple | None:
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT title, summary, category, content_hash, "
            "       (embedding IS NOT NULL) AS has_embedding, "
            "       is_stale "
            "FROM templates WHERE task_prompt_id = %s",
            (task_prompt_id,),
        )
        return await cur.fetchone()


# ---------------------------------------------------------------------------
# Upsert paths
# ---------------------------------------------------------------------------


async def test_upsert_inserts_new_record(clean_test_rows) -> None:
    record = make_record(1, title="Bold Title", summary="A bold animated title card.")
    is_new = await upsert_template(record)

    assert is_new is True
    row = await _row(tid(1))
    assert row is not None
    title, summary, category, content_hash, has_embedding, is_stale = row
    assert title == "Bold Title"
    assert category == TEST_CATEGORY
    assert content_hash is not None and len(content_hash) == 64
    assert has_embedding is True
    assert is_stale is False


async def test_upsert_returns_false_on_second_call_with_same_content(
    clean_test_rows,
) -> None:
    record = make_record(1, summary="Same content both times.")
    first = await upsert_template(record)
    second = await upsert_template(record)

    assert first is True
    assert second is False


async def test_upsert_skips_re_embedding_when_content_unchanged(
    clean_test_rows,
) -> None:
    record = make_record(1, title="Static", summary="Unchanging text.")
    await upsert_template(record)
    hash_before = (await _row(tid(1)))[3]  # content_hash column

    # Re-upsert with identical title+summary — popularity counters might bump
    # but the embedding source hasn't changed, so hash must not change either.
    record["used"] = 999
    await upsert_template(record)
    hash_after = (await _row(tid(1)))[3]

    assert hash_before == hash_after  # proves _maybe_embed took the skip path


async def test_upsert_refreshes_hash_when_summary_changes(clean_test_rows) -> None:
    record = make_record(1, summary="Original summary.")
    await upsert_template(record)
    hash_before = (await _row(tid(1)))[3]

    record["summary"] = "Brand new summary text — completely different."
    await upsert_template(record)
    hash_after = (await _row(tid(1)))[3]

    assert hash_before != hash_after  # content changed → hash changed


async def test_upsert_refreshes_hash_when_title_changes(clean_test_rows) -> None:
    record = make_record(1, title="Title A", summary="Body unchanged.")
    await upsert_template(record)
    hash_before = (await _row(tid(1)))[3]

    record["title"] = "Title B"
    await upsert_template(record)
    hash_after = (await _row(tid(1)))[3]

    assert hash_before != hash_after  # title is part of the embedding source


# ---------------------------------------------------------------------------
# find_templates — semantic + popularity + trgm ranking
# ---------------------------------------------------------------------------


async def test_find_templates_returns_relevant_match_first(clean_test_rows) -> None:
    # Seed three semantically distinct templates in the test category.
    await upsert_template(
        make_record(
            1,
            title="World Map Zoom",
            summary="A cinematic zoom into a stylized dark world map highlighting a region.",
        )
    )
    await upsert_template(
        make_record(
            2,
            title="Bar Chart Reveal",
            summary="An animated stacked bar chart revealing quarterly revenue numbers.",
        )
    )
    await upsert_template(
        make_record(
            3,
            title="Logo Stamp",
            summary="A logo reveal with a heavy thud and ink splash effect.",
        )
    )

    hits = await find_templates(
        "animated map of a country with smooth zoom",
        category_hints=[TEST_CATEGORY],
        k=3,
    )

    assert len(hits) == 3
    assert hits[0]["title"] == "World Map Zoom"
    # Sanity: scores are ordered descending
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_find_templates_filters_by_category(clean_test_rows) -> None:
    await upsert_template(
        make_record(1, summary="Animated map of the world.", category=TEST_CATEGORY)
    )
    await upsert_template(
        make_record(2, summary="Animated map of the world.", category="other_test")
    )

    hits = await find_templates(
        "world map", category_hints=[TEST_CATEGORY], k=5
    )

    ids = {h["task_prompt_id"] for h in hits}
    assert tid(1) in ids
    assert tid(2) not in ids


async def test_find_templates_excludes_premium_by_default(clean_test_rows) -> None:
    await upsert_template(
        make_record(
            1,
            summary="Premium animated chart.",
            category=TEST_CATEGORY,
            is_premium=True,
        )
    )
    await upsert_template(
        make_record(
            2,
            summary="Free animated chart.",
            category=TEST_CATEGORY,
            is_premium=False,
        )
    )

    hits_default = await find_templates(
        "animated chart", category_hints=[TEST_CATEGORY], k=5
    )
    hits_with_premium = await find_templates(
        "animated chart",
        category_hints=[TEST_CATEGORY],
        k=5,
        exclude_premium=False,
    )

    ids_default = {h["task_prompt_id"] for h in hits_default}
    ids_with_premium = {h["task_prompt_id"] for h in hits_with_premium}
    assert tid(1) not in ids_default
    assert tid(2) in ids_default
    assert tid(1) in ids_with_premium


# ---------------------------------------------------------------------------
# Stale marking — must be category-scoped to avoid nuking other categories
# ---------------------------------------------------------------------------


async def test_mark_stale_except_is_scoped_to_categories(clean_test_rows) -> None:
    # Two test categories; mark_stale_except("cat_a") must NOT touch cat_b rows.
    await upsert_template(
        make_record(1, summary="Item one.", category="cat_a_test")
    )
    await upsert_template(
        make_record(2, summary="Item two.", category="cat_a_test")
    )
    await upsert_template(
        make_record(3, summary="Item three.", category="cat_b_test")
    )

    # Only id #1 was "seen" in this scrape of cat_a — id #2 should go stale.
    n = await mark_stale_except({tid(1)}, categories=["cat_a_test"])
    assert n == 1

    # cat_a, kept
    row1 = await _row(tid(1))
    assert row1[5] is False  # is_stale
    # cat_a, marked stale
    row2 = await _row(tid(2))
    assert row2[5] is True
    # cat_b, untouched (different category)
    row3 = await _row(tid(3))
    assert row3[5] is False


async def test_find_templates_excludes_stale_rows(clean_test_rows) -> None:
    await upsert_template(make_record(1, summary="Active record.", category=TEST_CATEGORY))
    await upsert_template(make_record(2, summary="Active record.", category=TEST_CATEGORY))

    # Mark #2 stale.
    await mark_stale_except({tid(1)}, categories=[TEST_CATEGORY])

    hits = await find_templates(
        "active record", category_hints=[TEST_CATEGORY], k=5
    )
    ids = {h["task_prompt_id"] for h in hits}
    assert tid(1) in ids
    assert tid(2) not in ids


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


async def test_templates_summary_includes_test_rows(clean_test_rows) -> None:
    await upsert_template(make_record(1, summary="One.", category=TEST_CATEGORY))
    await upsert_template(make_record(2, summary="Two.", category=TEST_CATEGORY))

    summary = await templates_summary()
    by_cat = {c["category"]: c for c in summary["per_category"]}

    assert TEST_CATEGORY in by_cat
    assert by_cat[TEST_CATEGORY]["active"] == 2
    assert by_cat[TEST_CATEGORY]["missing_embedding"] == 0
    # `total` aggregates across all rows (test + real) — just sanity check it's non-zero.
    assert summary["total"] >= 2
