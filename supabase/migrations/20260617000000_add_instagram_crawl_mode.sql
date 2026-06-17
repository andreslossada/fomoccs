-- Migration: Add 'instagram' to crawl_mode enum
DO $$ BEGIN
    ALTER TYPE crawl_mode ADD VALUE 'instagram';
EXCEPTION WHEN duplicate_object THEN null;
END $$;
