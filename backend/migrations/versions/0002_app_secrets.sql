-- App secrets — currently holds the Hera dashboard session cookies.
-- Single-row pattern keyed by `key`; updatable from the UI.

CREATE TABLE IF NOT EXISTS app_secrets (
    key             TEXT PRIMARY KEY,
    value           JSONB NOT NULL,
    expires_at      TIMESTAMPTZ,
    last_validated  TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
