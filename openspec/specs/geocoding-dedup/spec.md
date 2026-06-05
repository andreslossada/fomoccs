# geocoding-dedup Specification

## Purpose
TBD - created by archiving change maximize-event-ingestion. Update Purpose after archive.
## Requirements
### Requirement: Reuse existing locations before geocoding

When `resolve_location()` is asked to resolve a venue name, the system SHALL first look up an existing active `Location` row by case-insensitive name match (with optional address tiebreaker). Only when no match is found SHALL the system create a new `Location` and trigger geocoding.

The lookup MUST use the configured `Location` dedup helper (`normalize_location_name`) and SHOULD consider `address` when present on the candidate row.

#### Scenario: Same venue from a second source reuses the location
- **WHEN** a MakeTicket event references "Teatro Teresa Carreño" and an existing `Location` row with `LOWER(name) = 'teatro teresa carreño'` exists
- **THEN** the new event is linked to that location's id, no new row is created, and the geocoding API is not called

#### Scenario: New venue triggers geocoding
- **WHEN** an event references "Sala Experimental 4" and no `Location` row matches the name
- **THEN** a new `Location` row is created and the geocoding chain (Google Places → Geoapify) is invoked

#### Scenario: Two venues with the same name and different addresses are kept separate
- **WHEN** an event references "Centro Cultural" with `address='Av. Tamanaco'` and a `Location` row exists with `LOWER(name)='centro cultural'` and a different `address`
- **THEN** a new `Location` row is created (the existing one is not a match)

