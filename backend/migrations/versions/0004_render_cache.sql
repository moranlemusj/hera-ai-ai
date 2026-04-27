-- Render cache — keyed by content hash so identical shot specs reuse Hera output
-- instead of burning quota. Particularly important during dev with HERA_MOCK=0.

CREATE TABLE IF NOT EXISTS render_cache (
    cache_key      TEXT PRIMARY KEY,        -- sha256(prompt|template_id|aspect|duration|...)
    video_id       TEXT NOT NULL,
    download_url   TEXT,
    local_path     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count      INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_render_cache_last_used
    ON render_cache (last_used_at DESC);
