# source-tiering Specification

## Purpose
TBD - created by archiving change maximize-event-ingestion. Update Purpose after archive.
## Requirements
### Requirement: Sources are classified into risk tiers

The system SHALL assign every source a `tier` value of 1, 2, or 3. The default tier for a newly created source SHALL be 1.

Tier semantics:
- **T1**: Official venue sites and government cultural calendars. Low ban risk. Throttle default: 0.5s between requests.
- **T2**: Ticketing platforms and event aggregators. Medium ban risk. Throttle default: 2s between requests.
- **T3**: Sites that require stealth (Instagram, Facebook). High ban risk. Throttle default: 5s, stealth mode mandatory.

The system MUST apply the tier's throttle default automatically when a source is crawled, and MUST allow `min_request_interval_seconds` to override it per-source.

#### Scenario: New source defaults to Tier 1
- **WHEN** a new row is inserted into `sources` without a `tier` value
- **THEN** the row is stored with `tier=1` and the crawler uses the T1 throttle default (0.5s)

#### Scenario: Tier override is respected
- **WHEN** a source has `tier=1` but `min_request_interval_seconds=3.0`
- **THEN** the crawler uses 3.0s between requests, not the T1 default

### Requirement: Tier is mutable without a code change

The system SHALL allow `tier` and `min_request_interval_seconds` to be updated on an existing source without a redeploy, and the next crawl of that source MUST use the new values.

#### Scenario: Operator promotes a source from T1 to T2
- **WHEN** the operator runs `UPDATE sources SET tier=2 WHERE id=N`
- **THEN** the next crawl of source N uses the T2 throttle default (2s), not T1

