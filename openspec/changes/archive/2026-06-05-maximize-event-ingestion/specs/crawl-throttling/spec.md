## ADDED Requirements

### Requirement: Per-hostname request throttling

The crawler SHALL enforce a minimum interval between consecutive HTTP requests to the same hostname. The interval SHALL be determined by the source's `tier` (or its per-source override `min_request_interval_seconds`).

Tier defaults:
- T1: 0.5 seconds
- T2: 2.0 seconds
- T3: 5.0 seconds

#### Scenario: Throttle paces requests on a Tier 1 source
- **WHEN** a Tier 1 source with no override is crawled
- **THEN** consecutive requests to the same hostname are at least 0.5s apart

#### Scenario: Throttle paces requests on a Tier 2 source
- **WHEN** a Tier 2 source is crawled
- **THEN** consecutive requests to the same hostname are at least 2.0s apart

### Requirement: Backoff on 403 or 429 responses

When the crawler receives a `403 Forbidden` or `429 Too Many Requests` response, the system SHALL record the host as in backoff state and SHALL skip further requests to that host for the duration indicated by `Retry-After` if present, otherwise for 60 seconds.

#### Scenario: 429 with Retry-After is honored
- **WHEN** a request returns 429 with `Retry-After: 120`
- **THEN** the crawler does not make another request to that host for at least 120 seconds

#### Scenario: 403 without Retry-After uses default backoff
- **WHEN** a request returns 403 with no `Retry-After` header
- **THEN** the crawler does not make another request to that host for 60 seconds

#### Scenario: Backoff state is logged
- **WHEN** the crawler enters backoff for a host
- **THEN** a structured log line is emitted with `event=host_backoff`, `hostname`, and `backoff_seconds`
