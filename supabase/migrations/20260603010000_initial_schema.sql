-- Custom PostgreSQL Enum Types (idempotent via DO blocks)
DO $$ BEGIN CREATE TYPE crawl_job_status        AS ENUM ('running', 'completed', 'failed'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE crawl_result_status     AS ENUM ('pending', 'crawled', 'extracted', 'processed', 'failed'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE crawl_mode              AS ENUM ('browser', 'json_api'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE event_status            AS ENUM ('active', 'archived', 'draft', 'cancelled'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE extracted_event_status  AS ENUM ('created', 'merged', 'skipped_no_location', 'skipped_no_occurrences', 'skipped_duplicate', 'skipped_tag_removed'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE location_type           AS ENUM ('venue', 'area', 'meeting_point'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE source_type             AS ENUM ('crawler', 'api', 'user_submission', 'partner_feed'); EXCEPTION WHEN duplicate_object THEN null; END $$;
DO $$ BEGIN CREATE TYPE tag_rule_type           AS ENUM ('rewrite', 'exclude', 'remove'); EXCEPTION WHEN duplicate_object THEN null; END $$;

-- Tables with no foreign key dependencies
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id           SERIAL       NOT NULL PRIMARY KEY,
    status       crawl_job_status NOT NULL DEFAULT 'running',
    started_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS locations (
    id              SERIAL          NOT NULL PRIMARY KEY,
    name            VARCHAR(255)    NOT NULL,
    short_name      VARCHAR(100),
    very_short_name VARCHAR(50),
    address         VARCHAR(500),
    description     TEXT,
    lat             NUMERIC(10,6),
    lng             NUMERIC(10,6),
    emoji           VARCHAR(10),
    alt_emoji       VARCHAR(10),
    website_url     VARCHAR(500),
    type            location_type   NOT NULL DEFAULT 'venue',
    deleted_at      TIMESTAMP,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    id          SERIAL          NOT NULL PRIMARY KEY,
    name        VARCHAR(255)    NOT NULL,
    type        source_type     NOT NULL,
    trust_level NUMERIC(2,1),
    disabled    BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at  TIMESTAMP,
    created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id   SERIAL       NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tag_rules (
    id          SERIAL          NOT NULL PRIMARY KEY,
    rule_type   tag_rule_type   NOT NULL,
    pattern     VARCHAR(100)    NOT NULL,
    replacement VARCHAR(100),
    deleted_at  TIMESTAMP,
    created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (rule_type, pattern)
);

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL          NOT NULL PRIMARY KEY,
    email         VARCHAR(255)    NOT NULL UNIQUE,
    display_name  VARCHAR(100),
    password_hash VARCHAR(255)    NOT NULL,
    is_admin      BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

-- Tables with foreign key dependencies
CREATE TABLE IF NOT EXISTS crawl_configs (
    id                        SERIAL       NOT NULL PRIMARY KEY,
    source_id                 INTEGER      NOT NULL UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
    notes                     TEXT,
    default_tags              TEXT[],
    crawl_frequency           INTEGER      NOT NULL,
    crawl_frequency_locked    BOOLEAN      NOT NULL DEFAULT FALSE,
    crawl_after               DATE,
    force_crawl               BOOLEAN      NOT NULL DEFAULT FALSE,
    last_crawled_at           TIMESTAMP,
    crawl_mode                crawl_mode   NOT NULL DEFAULT 'browser',
    selector                  VARCHAR(500),
    num_clicks                INTEGER,
    js_code                   TEXT,
    keywords                  VARCHAR(255),
    max_pages                 INTEGER      NOT NULL DEFAULT 30,
    max_batches               INTEGER,
    json_api_config           JSONB,
    delay_before_return_html  INTEGER,
    content_filter_threshold  NUMERIC(3,2),
    scan_full_page            BOOLEAN,
    remove_overlay_elements   BOOLEAN,
    javascript_enabled        BOOLEAN,
    text_mode                 BOOLEAN,
    light_mode                BOOLEAN,
    use_stealth               BOOLEAN,
    scroll_delay              NUMERIC(3,2),
    crawl_timeout             INTEGER,
    process_images            BOOLEAN
);

CREATE TABLE IF NOT EXISTS crawl_results (
    id            SERIAL               NOT NULL PRIMARY KEY,
    crawl_job_id  INTEGER              NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,
    source_id     INTEGER              NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status        crawl_result_status  NOT NULL DEFAULT 'pending',
    crawled_at    TIMESTAMP,
    extracted_at  TIMESTAMP,
    processed_at  TIMESTAMP,
    error_message TEXT,
    created_at    TIMESTAMP            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (crawl_job_id, source_id)
);

CREATE TABLE IF NOT EXISTS crawl_contents (
    id               SERIAL  NOT NULL PRIMARY KEY,
    crawl_result_id  INTEGER NOT NULL UNIQUE REFERENCES crawl_results(id) ON DELETE CASCADE,
    crawled_content  TEXT,
    extracted_content TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          SERIAL          NOT NULL PRIMARY KEY,
    name        VARCHAR(500)    NOT NULL,
    short_name  VARCHAR(255),
    description TEXT,
    emoji       VARCHAR(10),
    location_id INTEGER        NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    sublocation VARCHAR(255),
    status      event_status    NOT NULL DEFAULT 'active',
    reviewed    BOOLEAN         NOT NULL DEFAULT FALSE,
    deleted_at  TIMESTAMP,
    created_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_urls (
    id         SERIAL          NOT NULL PRIMARY KEY,
    source_id  INTEGER         NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    url        VARCHAR(2000)   NOT NULL,
    js_code    TEXT,
    sort_order INTEGER         NOT NULL DEFAULT 0,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_occurrences (
    id         SERIAL       NOT NULL PRIMARY KEY,
    event_id   INTEGER      NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    start_date DATE         NOT NULL,
    start_time VARCHAR(20),
    end_date   DATE,
    end_time   VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS location_alternate_names (
    id             SERIAL        NOT NULL PRIMARY KEY,
    location_id    INTEGER       NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    alternate_name VARCHAR(255)  NOT NULL
);

CREATE TABLE IF NOT EXISTS location_tags (
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (location_id, tag_id)
);

CREATE TABLE IF NOT EXISTS event_tags (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, tag_id)
);

CREATE TABLE IF NOT EXISTS event_urls (
    id       SERIAL         NOT NULL PRIMARY KEY,
    event_id INTEGER        NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    url      VARCHAR(2000)  NOT NULL
);

CREATE TABLE IF NOT EXISTS extracted_events (
    id              SERIAL          NOT NULL PRIMARY KEY,
    crawl_result_id INTEGER         NOT NULL REFERENCES crawl_results(id) ON DELETE CASCADE,
    name            VARCHAR(500)    NOT NULL,
    short_name      VARCHAR(255),
    description     TEXT,
    emoji           VARCHAR(10),
    location_id     INTEGER         REFERENCES locations(id) ON DELETE SET NULL,
    location_name   VARCHAR(255),
    sublocation     VARCHAR(255),
    url             VARCHAR(2000),
    occurrences     JSONB,
    tags            JSONB,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_sources (
    id                 SERIAL       NOT NULL PRIMARY KEY,
    event_id           INTEGER      NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    extracted_event_id INTEGER      REFERENCES extracted_events(id) ON DELETE CASCADE,
    source_id          INTEGER      NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    trust_score        NUMERIC(2,1),
    is_primary         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, extracted_event_id)
);

CREATE TABLE IF NOT EXISTS extracted_event_logs (
    id                 SERIAL                   NOT NULL PRIMARY KEY,
    extracted_event_id INTEGER                  NOT NULL REFERENCES extracted_events(id) ON DELETE CASCADE,
    status             extracted_event_status   NOT NULL,
    event_id           INTEGER                  REFERENCES events(id) ON DELETE SET NULL,
    message            TEXT,
    created_at         TIMESTAMP                NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_summaries (
    id              SERIAL          NOT NULL PRIMARY KEY,
    crawl_job_id    INTEGER         NOT NULL UNIQUE REFERENCES crawl_jobs(id) ON DELETE CASCADE,
    api_calls       INTEGER         NOT NULL DEFAULT 0,
    input_tokens    INTEGER         NOT NULL DEFAULT 0,
    output_tokens   INTEGER         NOT NULL DEFAULT 0,
    thinking_tokens INTEGER         NOT NULL DEFAULT 0,
    estimated_cost  NUMERIC(10,6)   NOT NULL DEFAULT 0,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_url_results (
    id              SERIAL               NOT NULL PRIMARY KEY,
    crawl_result_id INTEGER              NOT NULL REFERENCES crawl_results(id) ON DELETE CASCADE,
    url             VARCHAR(2000)        NOT NULL,
    status          crawl_result_status  NOT NULL DEFAULT 'pending',
    crawled_content TEXT,
    error_message   TEXT,
    crawled_at      TIMESTAMP,
    created_at      TIMESTAMP            NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Partial unique index
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_urls_url_active
    ON source_urls (url)
    WHERE deleted_at IS NULL;
