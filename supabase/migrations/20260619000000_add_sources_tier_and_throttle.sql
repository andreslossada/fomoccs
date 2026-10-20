-- Migration: add tier and min_request_interval_seconds columns to sources.
--
-- The pipeline (pipeline/db.py:get_sources_due_for_crawling) and the bulk
-- seed script (scripts/bulk_add_sources.py) both reference these columns,
-- but they were missing from the initial schema. Without them:
--   * Tier-based cadence (T1 = every 6h, T2 = every 12h, T3 = every 24h)
--     cannot be enforced by the scheduler.
--   * Per-hostname throttling cannot be customized per source.
--
-- Default values keep all existing rows functional after the upgrade:
--   * tier defaults to 2 (every 12h) — middle cadence.
--   * min_request_interval_seconds defaults to NULL (= use tier default).

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS tier INTEGER NOT NULL DEFAULT 2
        CHECK (tier BETWEEN 1 AND 3);

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS min_request_interval_seconds NUMERIC(5,2);
