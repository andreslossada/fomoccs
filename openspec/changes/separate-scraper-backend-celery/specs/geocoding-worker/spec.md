## ADDED Requirements

### Requirement: Geocode location task
The system SHALL define a Celery task `geocode_location(location_id)` that geocodes a location via Geoapify and updates its coordinates. This task runs on the `geocoding` queue.

#### Scenario: Successful geocoding
- **WHEN** the `geocode_location` task receives a `location_id`
- **THEN** it fetches the location's name from the database
- **THEN** it calls the Geoapify API with Caracas bias
- **THEN** it updates the location's `lat` and `lng` fields
- **THEN** it validates the result is within Caracas bounds

#### Scenario: Geocoding returns no results
- **WHEN** Geoapify returns no results for a location name
- **THEN** the location's coordinates remain null
- **THEN** the task completes successfully (no retry)

#### Scenario: Geocoding result outside Caracas
- **WHEN** Geoapify returns coordinates outside the Caracas bounding box
- **THEN** the coordinates are discarded (not saved)
- **THEN** the task completes successfully

#### Scenario: Geoapify API error
- **WHEN** the Geoapify API returns an error or times out
- **THEN** the task retries with exponential backoff (up to 3 retries)

### Requirement: Non-blocking geocoding
The `geocode_location` task MUST NOT block event processing. It runs on a separate `geocoding` queue and is fired asynchronously by the event processing consumer.

#### Scenario: Event processing continues without geocoding result
- **WHEN** event processing creates a new location and queues geocoding
- **THEN** the event is created with `location_id` set but coordinates may be null
- **THEN** geocoding runs asynchronously and updates coordinates later

### Requirement: Reuse existing geocoding service
The geocoding task SHALL reuse the existing `backend/api/services/geocoding.py` module (Geoapify client, Caracas bounds validation, haversine distance).

#### Scenario: Geocoding task calls existing service
- **WHEN** the `geocode_location` task runs
- **THEN** it calls `geocode_location_name()` from `backend/api/services/geocoding.py`
- **THEN** it uses the `GEOAPIFY_API_KEY` environment variable for authentication
