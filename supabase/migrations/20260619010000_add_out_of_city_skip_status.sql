-- Migration: add 'skipped_out_of_city' to extracted_event_status enum.
--
-- The LLM extraction step occasionally returns events from other
-- Venezuelan cities (Maracay, Barquisimeto, Punto Fijo, Maturin, ...),
-- usually from national portals like Fundarte or Teatro. Those events
-- can never resolve to Caracas coordinates, so they sit in the feed
-- occupying marker slots while rendering nothing. backend/api/services/
-- event_merging.py::is_out_of_caracas now drops them and records the
-- reason with this new status.
DO $$ BEGIN
    ALTER TYPE extracted_event_status ADD VALUE 'skipped_out_of_city';
EXCEPTION WHEN duplicate_object THEN null;
END $$;