# pipeline-parallelization Specification

## Purpose
TBD - created by archiving change maximize-event-ingestion. Update Purpose after archive.
## Requirements
### Requirement: Pipeline processes multiple sources concurrently

When the pipeline is invoked with `python main.py --ids=1,2,3,...`, the system SHALL run each source's crawl + extract + process steps concurrently rather than sequentially, subject to a configurable concurrency bound.

The default concurrency bound SHALL be 5. The bound MUST be overridable via the `PIPELINE_CONCURRENCY` environment variable.

#### Scenario: Three sources finish in roughly the time of the slowest
- **WHEN** the pipeline is invoked with `--ids=10,11,12` and each source takes 60s
- **THEN** the total wall time is approximately 60-75s, not 180s

#### Scenario: A single source failure does not block siblings
- **WHEN** source 11 raises an unhandled exception during crawl
- **THEN** sources 10 and 12 still complete and write their results

#### Scenario: Concurrency cap is respected
- **WHEN** `PIPELINE_CONCURRENCY=3` and 10 sources are queued
- **THEN** at most 3 source pipelines are active simultaneously at any point in time

### Requirement: Per-source scheduling is decoupled

The system MUST allow Cloud Scheduler to invoke the pipeline with different `--ids` lists on independent cadences, and one schedule's failure MUST NOT delay the next.

#### Scenario: Ticketing cadence and venue cadence are independent
- **WHEN** the ticketing schedule runs every 6h and the venue schedule runs every 12h
- **THEN** a slow ticketing run does not delay the next venue run

