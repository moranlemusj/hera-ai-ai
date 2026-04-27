-- Track the sha256 of the embedding source ({title}\n\n{summary}) so the
-- scraper can detect content changes and refresh stale embeddings instead of
-- silently keeping the old vector forever.
--
-- Existing rows: leave content_hash NULL. The next scrape will populate it
-- and (because the existing embedding was computed from summary-only) will
-- re-embed under the new title+summary source. That's the migration's
-- one-time cost — embedding is otherwise skipped when the hash matches.

ALTER TABLE templates
    ADD COLUMN IF NOT EXISTS content_hash TEXT;
