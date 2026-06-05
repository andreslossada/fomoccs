## ADDED Requirements

### Requirement: Structured per-source completion log

When the pipeline finishes processing a source, it SHALL emit a single JSON line on stdout with the following keys:
- `event` = `"source_complete"`
- `source_id` (int)
- `crawl_status_code` (int, or null on crawler error)
- `event_count` (int, number of events written to the `events` table for this run)
- `llm_provider` (string, name of the LLM that succeeded, or null)
- `geocode_provider` (string, `"google"` / `"geoapify"` / `null`)
- `geocode_hit` (bool, true if a known location was reused; false if a new geocoding call was made)
- `duration_s` (float)

#### Scenario: Successful source emits structured log
- **WHEN** a source crawl finishes with 12 events written, LLM provider `groq`, 3 new locations geocoded via Google
- **THEN** the log line contains `event=source_complete, source_id=N, crawl_status_code=200, event_count=12, llm_provider=groq, geocode_provider=google, geocode_hit=false, duration_s=87.4`

#### Scenario: Source with no events still logs
- **WHEN** a source completes with `crawl_status_code=200` but no events pass dedup
- **THEN** the log line contains `event_count=0` and the line is still emitted (not suppressed)

### Requirement: Source errors emit a failure log

When a source pipeline raises an exception, the system SHALL emit a JSON line with `event="source_error"`, the source id, the exception class name, and a one-line message. The exception MUST NOT prevent sibling sources from completing.

#### Scenario: Crawler exception does not crash the job
- **WHEN** source 11 raises `httpx.ConnectError`
- **THEN** a `source_error` log line is emitted and sources 10 and 12 still complete normally
