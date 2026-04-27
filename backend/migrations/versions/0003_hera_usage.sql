-- Monthly Hera quota tracker. Plan: 200 videos / 100 images per calendar month.
-- Incremented on every successful POST /v1/videos so we can refuse runs near the cap.

CREATE TABLE IF NOT EXISTS hera_usage (
    month         DATE PRIMARY KEY,           -- first day of the month, UTC
    video_count   INT NOT NULL DEFAULT 0,
    image_count   INT NOT NULL DEFAULT 0,
    last_video_at TIMESTAMPTZ
);
