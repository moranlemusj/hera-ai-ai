-- Templates scraped from app.hera.video/api/templates.
-- task_prompt_id is the value passed as `parent_video_id` to Hera's render API.

CREATE TABLE IF NOT EXISTS templates (
    task_prompt_id      UUID PRIMARY KEY,
    task_id             UUID,
    title               TEXT NOT NULL,
    category            TEXT NOT NULL,
    summary             TEXT NOT NULL,
    tags                TEXT[],
    liked               INT  DEFAULT 0,
    used                INT  DEFAULT 0,
    is_premium          BOOLEAN DEFAULT FALSE,
    is_ready            BOOLEAN DEFAULT TRUE,
    thumbnail_url       TEXT,
    preview_video_url   TEXT,
    config              JSONB,
    embedding           vector(768),
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_stale            BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_templates_embedding
    ON templates USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_templates_category
    ON templates (category) WHERE NOT is_stale;

CREATE INDEX IF NOT EXISTS idx_templates_used
    ON templates (used DESC);

CREATE INDEX IF NOT EXISTS idx_templates_summary_trgm
    ON templates USING gin (summary gin_trgm_ops);
