from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "versions"

_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.NEON_DATABASE_URL,
            min_size=1,
            max_size=8,
            timeout=30,
            kwargs={"autocommit": True},
            open=False,
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn() -> AsyncIterator[AsyncConnection]:
    pool = await get_pool()
    async with pool.connection() as conn:
        yield conn


async def run_migrations() -> None:
    """Apply any *.sql files in migrations/versions in lexicographic order, idempotently.

    Tracks applied versions in the `schema_migrations` table.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.warning("No migration files found at %s", MIGRATIONS_DIR)
        return

    async with get_conn() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        result = await conn.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in await result.fetchall()}

        for path in files:
            version = path.stem  # "0001_init"
            if version in applied:
                continue
            log.info("Applying migration: %s", version)
            sql = path.read_text()
            await conn.execute(sql)  # type: ignore[arg-type]
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (version,),
            )
            log.info("Applied: %s", version)


async def healthcheck() -> dict[str, str]:
    async with get_conn() as conn:
        cur = await conn.execute("SELECT version()")
        row = await cur.fetchone()
        version = row[0] if row else "unknown"

        # Verify required extensions
        cur = await conn.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')"
        )
        exts = {r[0] for r in await cur.fetchall()}

    missing = {"vector", "pg_trgm"} - exts
    return {
        "postgres_version": version.split(",")[0],
        "extensions": ",".join(sorted(exts)) or "none",
        "missing_extensions": ",".join(sorted(missing)) or "none",
    }
