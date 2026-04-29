"""Templates module — scrape, embed, store, and search Hera templates.

Templates live on the Hera dashboard and are picked by `task_prompt_id` (the
value passed as `parent_video_id` when calling Hera's render API). We scrape
once per category, embed each summary with Gemini, and let the planner do
hybrid (semantic + popularity + trigram) search.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np
from psycopg.types.json import Jsonb

from app.config import settings
from app.db import get_conn
from app.services.embeddings import embed_text
from app.services.hera_dashboard import fetch_templates_page

log = logging.getLogger(__name__)

ProgressCb = Callable[[dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _coerce_tags(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        return [t for t in raw.split() if t]
    return None


def _coerce_config(record: dict) -> dict | None:
    """Hera nests render config under task_prompts.config."""
    task_prompts = record.get("task_prompts")
    if isinstance(task_prompts, dict):
        cfg = task_prompts.get("config")
        if isinstance(cfg, dict):
            return cfg
    cfg = record.get("config")
    return cfg if isinstance(cfg, dict) else None


def _content_source(record: dict) -> str:
    """Build the text we embed: ``{title}\\n\\n{summary}``.

    Title alone is short but high-signal (e.g. "WASHINGTON MAP"); summary is
    the long descriptive paragraph. We concatenate so the vector reflects
    both. Empty pieces are dropped to keep the source tight.
    """
    title = (record.get("title") or "").strip()
    summary = (record.get("summary") or "").strip()
    parts = [p for p in (title, summary) if p]
    return "\n\n".join(parts)


def _content_hash(source: str) -> str:
    """sha256 hex of the embedding source."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


async def _maybe_embed(record: dict) -> tuple[np.ndarray | None, str | None]:
    """Decide whether to (re-)embed this template.

    Returns ``(vector_or_None, hash_or_None)``:
      - ``(vec, hash)`` — caller should write the new vector AND the new hash
      - ``(None, hash)`` — caller should write the hash; existing embedding stays
      - ``(None, None)`` — record has no embeddable content; leave both NULL

    Done outside of any DB transaction so we never hold a pool connection
    across the Gemini network call.
    """
    source = _content_source(record)
    if not source:
        return None, None
    new_hash = _content_hash(source)

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT content_hash, (embedding IS NOT NULL) FROM templates "
            "WHERE task_prompt_id = %s",
            (record["task_prompt_id"],),
        )
        row = await cur.fetchone()

    if row is not None:
        existing_hash, has_embedding = row
        if existing_hash == new_hash and has_embedding:
            return None, new_hash

    try:
        vec = await embed_text(source)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Embedding failed for template %s: %s — inserting without embedding",
            record["task_prompt_id"],
            exc,
        )
        return None, new_hash
    return np.asarray(vec, dtype=np.float32), new_hash


async def upsert_template(record: dict) -> bool:
    """Insert or update one template row. Returns True iff a new row was inserted.

    Uses a single ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING (xmax = 0)`` —
    halves DB round trips and removes the existence-check race. The
    ``embedding`` decision is centralized in ``_maybe_embed``: if it returned a
    fresh vector, we use it; otherwise the existing embedding stays via
    ``COALESCE``.
    """
    task_prompt_id = record.get("task_prompt_id")
    if not task_prompt_id:
        raise ValueError("template record missing task_prompt_id")

    embedding, content_hash = await _maybe_embed(record)
    cfg = _coerce_config(record)
    cfg_param = Jsonb(cfg) if cfg else None
    summary = record.get("summary") or ""

    async with get_conn() as conn:
        cur = await conn.execute(
            """
            INSERT INTO templates (
                task_prompt_id, task_id, title, category, summary, tags,
                liked, used, is_premium, is_ready,
                thumbnail_url, preview_video_url, config, embedding, content_hash,
                first_seen_at, last_seen_at, is_stale
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                NOW(), NOW(), FALSE
            )
            ON CONFLICT (task_prompt_id) DO UPDATE SET
                title             = EXCLUDED.title,
                category          = EXCLUDED.category,
                summary           = EXCLUDED.summary,
                tags              = EXCLUDED.tags,
                liked             = EXCLUDED.liked,
                used              = EXCLUDED.used,
                is_premium        = EXCLUDED.is_premium,
                is_ready          = EXCLUDED.is_ready,
                thumbnail_url     = EXCLUDED.thumbnail_url,
                preview_video_url = EXCLUDED.preview_video_url,
                config            = EXCLUDED.config,
                -- COALESCE: if the new scrape returns an empty source (None vec, None hash),
                -- keep the existing embedding+hash. Defends against transient Hera glitches.
                -- Tradeoff: a *legitimately* emptied template won't lose its old vector.
                embedding         = COALESCE(EXCLUDED.embedding, templates.embedding),
                content_hash      = COALESCE(EXCLUDED.content_hash, templates.content_hash),
                last_seen_at      = NOW(),
                is_stale          = FALSE
            RETURNING (xmax = 0) AS inserted
            """,
            (
                task_prompt_id,
                record.get("task_id"),
                record.get("title") or "(untitled)",
                record.get("category") or "others",
                summary,
                _coerce_tags(record.get("tags")),
                record.get("liked") or 0,
                record.get("used") or 0,
                bool(record.get("is_premium")),
                bool(record.get("is_ready", True)),
                record.get("thumbnail_url"),
                record.get("preview_video_url"),
                cfg_param,
                embedding,
                content_hash,
            ),
        )
        row = await cur.fetchone()
    return bool(row[0]) if row else False


# ---------------------------------------------------------------------------
# Stale management
# ---------------------------------------------------------------------------


async def mark_stale_except(
    seen_ids: set[str],
    categories: list[str] | None = None,
) -> int:
    """Mark every template not in seen_ids as stale.

    Scoped to `categories` if provided so a single-category refresh doesn't
    flag rows in other categories.
    """
    if not seen_ids:
        return 0

    async with get_conn() as conn:
        if categories:
            cur = await conn.execute(
                """
                UPDATE templates SET is_stale = TRUE
                WHERE category = ANY(%s) AND task_prompt_id <> ALL(%s::uuid[])
                  AND NOT is_stale
                """,
                (categories, list(seen_ids)),
            )
        else:
            cur = await conn.execute(
                """
                UPDATE templates SET is_stale = TRUE
                WHERE task_prompt_id <> ALL(%s::uuid[]) AND NOT is_stale
                """,
                (list(seen_ids),),
            )
        return cur.rowcount or 0


# ---------------------------------------------------------------------------
# Scrape orchestration
# ---------------------------------------------------------------------------


async def scrape_all(
    category: str | None = None,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Scrape one category or all of them. Returns a summary dict.

    progress_cb (if given) is awaited with {category, page, count, inserted, updated}
    after each successful page so callers can stream progress over SSE.
    """
    cats = [category] if category else list(settings.TEMPLATE_CATEGORIES)
    seen_ids: set[str] = set()
    inserted = 0
    updated = 0
    failed_categories: list[dict[str, str]] = []

    for cat in cats:
        page = 1
        while True:
            try:
                data = await fetch_templates_page(cat, page, settings.SCRAPE_PAGE_SIZE)
            except Exception as exc:  # noqa: BLE001
                log.exception("Scrape failed for category=%s page=%d", cat, page)
                failed_categories.append({"category": cat, "error": str(exc)})
                if progress_cb is not None:
                    await progress_cb(
                        {
                            "category": cat,
                            "page": page,
                            "count": 0,
                            "inserted": 0,
                            "updated": 0,
                            "error": str(exc),
                        }
                    )
                break

            page_inserted = 0
            page_updated = 0
            page_errors: list[str] = []
            for record in data:
                seen_ids.add(record["task_prompt_id"])
                try:
                    is_new = await upsert_template(record)
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "upsert failed for template %s", record.get("task_prompt_id")
                    )
                    page_errors.append(f"{record.get('task_prompt_id')}: {exc}")
                    continue
                if is_new:
                    page_inserted += 1
                else:
                    page_updated += 1
                await asyncio.sleep(settings.SCRAPE_RECORD_PACE_SECONDS)

            inserted += page_inserted
            updated += page_updated
            log.info(
                "Scraped %s p%d: %d records (%d new, %d updated, %d errors)",
                cat,
                page,
                len(data),
                page_inserted,
                page_updated,
                len(page_errors),
            )
            if progress_cb is not None:
                event: dict[str, Any] = {
                    "category": cat,
                    "page": page,
                    "count": len(data),
                    "inserted": page_inserted,
                    "updated": page_updated,
                }
                if page_errors:
                    event["upsert_errors"] = page_errors
                await progress_cb(event)

            if len(data) < settings.SCRAPE_PAGE_SIZE:
                break
            page += 1
            await asyncio.sleep(settings.SCRAPE_PACE_SECONDS)

    stale_marked = await mark_stale_except(seen_ids, cats)

    summary = {
        "categories_scraped": cats,
        "templates_seen": len(seen_ids),
        "inserted": inserted,
        "updated": updated,
        "stale_marked": stale_marked,
        "failed_categories": failed_categories,
    }
    log.info("Scrape complete: %s", json.dumps(summary))
    return summary


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def find_templates(
    description: str,
    category_hints: list[str] | None = None,
    k: int = 3,
    exclude_premium: bool = True,
    target_duration_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Hybrid template search.

    Two-stage to keep the HNSW index in play:
    1. Inner query: pure ANN — `ORDER BY embedding <=> qvec LIMIT k * 5`
    2. Outer query: rescore the candidates with cosine + popularity + trigram
       and re-order to the final top-k.

    If `target_duration_seconds` is given, candidates are pre-filtered to those
    whose template duration is compatible:
      - "AUTO" templates always pass (they pick their own length)
      - missing/null durationSeconds passes (older entries)
      - numeric durationSeconds within ±DURATION_FILTER_BUFFER_SECONDS of target

    Without this filter the planner can pick a 60-second template for a
    5-second shot, blowing the run length way past the budget.
    """
    if not description.strip():
        return []

    qvec = np.asarray(await embed_text(description), dtype=np.float32)

    sql = """
    WITH candidates AS (
        SELECT
            task_prompt_id, task_id, title, category, summary, tags,
            liked, used, is_premium, thumbnail_url, preview_video_url, config,
            embedding
        FROM templates
        WHERE NOT is_stale
          AND is_ready
          AND embedding IS NOT NULL
          AND (%(cats)s::text[] IS NULL OR category = ANY(%(cats)s))
          AND (%(exclude_premium)s = FALSE OR is_premium = FALSE)
          AND (
            %(target_dur)s::numeric IS NULL
            OR config->>'durationSeconds' = 'AUTO'
            OR config->>'durationSeconds' IS NULL
            OR (
              config->>'durationSeconds' ~ '^[0-9]+(\\.[0-9]+)?$'
              AND (config->>'durationSeconds')::numeric
                  BETWEEN %(target_dur)s::numeric - %(buf)s::numeric
                      AND %(target_dur)s::numeric + %(buf)s::numeric
            )
          )
        ORDER BY embedding <=> %(qvec)s
        LIMIT %(prefetch)s
    )
    SELECT
        task_prompt_id, task_id, title, category, summary, tags,
        liked, used, is_premium, thumbnail_url, preview_video_url, config,
        (1 - (embedding <=> %(qvec)s)) AS sim,
        LEAST(LN(GREATEST(used, 0) + 1) / 12.0, 1.0) AS pop,
        similarity(summary, %(qtext)s) AS trgm,
        (
            (1 - (embedding <=> %(qvec)s)) * %(w_sim)s
            + LEAST(LN(GREATEST(used, 0) + 1) / 12.0, 1.0) * %(w_pop)s
            + similarity(summary, %(qtext)s) * %(w_trgm)s
        ) AS score
    FROM candidates
    ORDER BY score DESC
    LIMIT %(k)s
    """

    params = {
        "qvec": qvec,
        "qtext": description,
        "cats": category_hints,
        "exclude_premium": exclude_premium,
        "target_dur": target_duration_seconds,
        "buf": settings.DURATION_FILTER_BUFFER_SECONDS,
        "w_sim": settings.SEARCH_WEIGHT_SIM,
        "w_pop": settings.SEARCH_WEIGHT_POPULAR,
        "w_trgm": settings.SEARCH_WEIGHT_TRGM,
        "k": k,
        "prefetch": k * 5,
    }

    async with get_conn() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        cols = [c.name for c in cur.description] if cur.description else []

    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(zip(cols, row, strict=False))
        d["task_prompt_id"] = str(d["task_prompt_id"])
        if d.get("task_id") is not None:
            d["task_id"] = str(d["task_id"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


async def templates_summary() -> dict[str, Any]:
    """Per-category counts, last-scraped timestamps, totals."""
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT
                category,
                COUNT(*) FILTER (WHERE NOT is_stale) AS active,
                COUNT(*) FILTER (WHERE is_stale) AS stale,
                COUNT(*) FILTER (WHERE embedding IS NULL) AS missing_embedding,
                MAX(last_seen_at) AS last_seen
            FROM templates
            GROUP BY category
            ORDER BY category
            """
        )
        per_cat = []
        for row in await cur.fetchall():
            cat, active, stale, missing_emb, last_seen = row
            per_cat.append(
                {
                    "category": cat,
                    "active": active,
                    "stale": stale,
                    "missing_embedding": missing_emb,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                }
            )

        cur = await conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE NOT is_stale) AS active,
                COUNT(*) AS total,
                MAX(last_seen_at) AS last_seen
            FROM templates
            """
        )
        row = await cur.fetchone()
        if row is None:
            active_total = 0
            total = 0
            last_seen = None
        else:
            active_total, total, last_seen = row

    return {
        "total": total or 0,
        "active": active_total or 0,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "per_category": per_cat,
    }
