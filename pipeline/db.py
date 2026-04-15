"""
Scraper-only DB helpers. No processing/merge operations — those live in backend Celery workers.

Handles all database connections and CRUD operations for:
- Crawl jobs and results
- Sources and their crawl status
"""

import json
import os
import sys

try:
    import psycopg2
    from psycopg2 import Error
except ImportError:
    print("Error: psycopg2 is required.")
    print("Install it with: pip install psycopg2-binary")
    sys.exit(1)


# Database Configuration
DB_CONFIG = {
    "local": {
        "host": "localhost",
        "dbname": "momaverse",
        "user": os.environ.get("USER", "postgres"),
        "password": "",
    },
    "production": {
        "host": os.environ.get("PROD_DB_HOST", "localhost"),
        "dbname": os.environ.get("PROD_DB_NAME", "momaverse"),
        "user": os.environ.get("PROD_DB_USER", "momaverse"),
        "password": os.environ.get("PROD_DB_PASS", ""),
    },
}


def get_db_config():
    """Get database config based on environment."""
    env = os.environ.get("FOMO_ENV", "local")
    if env not in DB_CONFIG:
        env = "local"
    return DB_CONFIG[env]


def create_connection():
    """Create database connection."""
    config = get_db_config()
    try:
        conn = psycopg2.connect(
            host=config["host"],
            dbname=config["dbname"],
            user=config["user"],
            password=config["password"],
        )
        conn.autocommit = False
        return conn
    except Error as e:
        print(f"Error connecting to database: {e}")
        return None


def _parse_url_data(url_string):
    """Parse URL data from concatenated format 'url:::js_code|||url:::js_code|||...'"""
    urls = []
    for item in url_string.split("|||"):
        parts = item.split(":::", 1)
        url = parts[0]
        js_code = parts[1] if len(parts) > 1 and parts[1] else None
        urls.append({"url": url, "js_code": js_code})
    return urls


def get_sources_due_for_crawling(cursor, source_ids=None):
    """
    Get sources that are due for crawling based on crawl_frequency.

    Args:
        cursor: Database cursor
        source_ids: Optional list of source IDs to filter by. If provided,
                    only these sources are returned (ignoring crawl_frequency).

    Returns sources where:
    - disabled = FALSE
    - crawl_after is NULL or in the past
    - last_crawled_at is NULL, OR
    - NOW() - last_crawled_at > crawl_frequency days
    """
    if source_ids:
        placeholders = ",".join(["%s"] * len(source_ids))
        cursor.execute(
            f"""
            SELECT s.id, s.name, cc.crawl_frequency, cc.selector, cc.num_clicks,
                   cc.keywords, cc.max_pages, cc.max_batches, cc.notes,
                   cc.delay_before_return_html, cc.content_filter_threshold, cc.scan_full_page,
                   cc.remove_overlay_elements, cc.javascript_enabled, cc.text_mode, cc.light_mode,
                   cc.use_stealth, cc.scroll_delay, cc.crawl_timeout, cc.process_images,
                   cc.crawl_mode, cc.json_api_config,
                   STRING_AGG(CONCAT(su.url, ':::', COALESCE(su.js_code, '')), '|||' ORDER BY su.sort_order) as urls
            FROM sources s
            JOIN crawl_configs cc ON cc.source_id = s.id
            LEFT JOIN source_urls su ON su.source_id = s.id AND su.deleted_at IS NULL
            WHERE s.id IN ({placeholders})
              AND s.deleted_at IS NULL
            GROUP BY s.id, s.name, cc.id
            HAVING STRING_AGG(su.url, '') IS NOT NULL OR cc.crawl_mode = 'json_api'
            ORDER BY s.id ASC
        """,
            source_ids,
        )
    else:
        cursor.execute("""
            SELECT s.id, s.name, cc.crawl_frequency, cc.selector, cc.num_clicks,
                   cc.keywords, cc.max_pages, cc.max_batches, cc.notes,
                   cc.delay_before_return_html, cc.content_filter_threshold, cc.scan_full_page,
                   cc.remove_overlay_elements, cc.javascript_enabled, cc.text_mode, cc.light_mode,
                   cc.use_stealth, cc.scroll_delay, cc.crawl_timeout, cc.process_images,
                   cc.crawl_mode, cc.json_api_config,
                   STRING_AGG(CONCAT(su.url, ':::', COALESCE(su.js_code, '')), '|||' ORDER BY su.sort_order) as urls
            FROM sources s
            JOIN crawl_configs cc ON cc.source_id = s.id
            LEFT JOIN source_urls su ON su.source_id = s.id AND su.deleted_at IS NULL
            WHERE s.disabled = FALSE
              AND s.deleted_at IS NULL
              AND (cc.crawl_after IS NULL OR cc.crawl_after <= CURRENT_DATE)
              AND (cc.force_crawl = TRUE
                   OR cc.last_crawled_at IS NULL
                   OR EXTRACT(DAY FROM NOW() - cc.last_crawled_at) >= COALESCE(cc.crawl_frequency, 7))
            GROUP BY s.id, s.name, cc.id
            HAVING STRING_AGG(su.url, '') IS NOT NULL OR cc.crawl_mode = 'json_api'
            ORDER BY cc.force_crawl DESC, cc.last_crawled_at ASC NULLS LAST
        """)

    sources = []
    for row in cursor.fetchall():
        source = {
            "id": row[0],
            "name": row[1],
            "crawl_frequency": row[2] or 7,
            "selector": row[3],
            "num_clicks": row[4] or 2,
            "keywords": row[5],
            "max_pages": row[6] or 30,
            "max_batches": row[7],
            "notes": row[8],
            "delay_before_return_html": row[9],
            "content_filter_threshold": row[10],
            "scan_full_page": row[11],
            "remove_overlay_elements": row[12],
            "javascript_enabled": row[13],
            "text_mode": row[14],
            "light_mode": row[15],
            "use_stealth": row[16],
            "scroll_delay": float(row[17]) if row[17] is not None else None,
            "crawl_timeout": row[18],
            "process_images": row[19],
            "crawl_mode": row[20] or "browser",
            "json_api_config": row[21]
            if isinstance(row[21], dict)
            else (json.loads(row[21]) if row[21] else {}),
            "urls": _parse_url_data(row[22]) if row[22] else [],
        }
        sources.append(source)

    return sources


def create_crawl_job(cursor, connection):
    """Create a new crawl job and return its id."""
    cursor.execute(
        "INSERT INTO crawl_jobs (status, started_at) VALUES ('running', NOW()) RETURNING id",
    )
    new_id = cursor.fetchone()[0]
    connection.commit()
    return new_id


def create_crawl_result(cursor, connection, crawl_job_id, source_id, _filename=None):
    """Create a new crawl result record.

    _filename is accepted for backward compatibility but ignored (column dropped).
    """
    cursor.execute(
        """INSERT INTO crawl_results (crawl_job_id, source_id, status, created_at)
           VALUES (%s, %s, 'pending', NOW())
           ON CONFLICT (crawl_job_id, source_id) DO UPDATE SET status = 'pending'
           RETURNING id""",
        (crawl_job_id, source_id),
    )
    new_id = cursor.fetchone()[0]
    connection.commit()
    return new_id


def update_crawl_result(cursor, connection, crawl_result_id, status, **kwargs):
    """
    Generic update function for crawl results.

    Status and timestamps are stored on crawl_results. Content (crawled_content,
    extracted_content) is stored in the crawl_contents table.

    Args:
        cursor: Database cursor
        connection: Database connection
        crawl_result_id: ID of the crawl result to update
        status: New status value
        **kwargs: Additional fields to update (content, error_message)
    """
    updates = ["status = %s"]
    params = [status]

    timestamp_map = {
        "crawled": "crawled_at",
        "extracted": "extracted_at",
        "processed": "processed_at",
    }
    if status in timestamp_map:
        updates.append(f"{timestamp_map[status]} = NOW()")

    if "error_message" in kwargs:
        updates.append("error_message = %s")
        error_msg = kwargs["error_message"]
        params.append(error_msg[:65535] if error_msg else None)

    params.append(crawl_result_id)

    cursor.execute(
        f"UPDATE crawl_results SET {', '.join(updates)} WHERE id = %s", tuple(params)
    )

    # Content lives in crawl_contents (1:1 with crawl_results)
    if "content" in kwargs:
        content_value = kwargs["content"]
        if status == "crawled":
            column = "crawled_content"
        elif status == "extracted":
            column = "extracted_content"
        else:
            column = None

        if column:
            cursor.execute(
                f"""INSERT INTO crawl_contents (crawl_result_id, {column})
                    VALUES (%s, %s)
                    ON CONFLICT (crawl_result_id) DO UPDATE SET {column} = EXCLUDED.{column}""",
                (crawl_result_id, content_value),
            )

    connection.commit()


def update_crawl_result_crawled(cursor, connection, crawl_result_id, content):
    """Update crawl result with crawled content."""
    update_crawl_result(cursor, connection, crawl_result_id, "crawled", content=content)


def update_crawl_result_extracted(cursor, connection, crawl_result_id, content):
    """Update crawl result with extracted content."""
    update_crawl_result(
        cursor, connection, crawl_result_id, "extracted", content=content
    )


def update_crawl_result_processed(
    cursor, connection, crawl_result_id, _event_count=None
):
    """Update crawl result as processed.

    _event_count is accepted for backward compatibility but ignored (column dropped).
    """
    update_crawl_result(cursor, connection, crawl_result_id, "processed")


def update_crawl_result_failed(cursor, connection, crawl_result_id, error_message):
    """Update crawl result as failed."""
    update_crawl_result(
        cursor, connection, crawl_result_id, "failed", error_message=error_message
    )


def update_source_last_crawled(cursor, connection, source_id):
    """Update the last_crawled_at timestamp for a source and reset force_crawl flag."""
    cursor.execute(
        "UPDATE crawl_configs SET last_crawled_at = NOW(), force_crawl = FALSE WHERE source_id = %s",
        (source_id,),
    )
    connection.commit()


def complete_crawl_job(cursor, connection, crawl_job_id):
    """Mark a crawl job as completed."""
    cursor.execute(
        "UPDATE crawl_jobs SET status = 'completed', completed_at = NOW() WHERE id = %s",
        (crawl_job_id,),
    )
    connection.commit()


def save_crawl_summary(cursor, crawl_job_id, tracker):
    """Save token usage summary for a crawl job.

    Does not commit — caller is responsible for committing the transaction.
    """
    cursor.execute(
        """INSERT INTO crawl_summaries
            (crawl_job_id, api_calls, input_tokens, output_tokens, thinking_tokens, estimated_cost)
        VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            crawl_job_id,
            tracker.api_calls,
            tracker.input_tokens,
            tracker.output_tokens,
            tracker.thinking_tokens,
            round(tracker.total_cost, 6),
        ),
    )


def get_incomplete_crawl_results(cursor):
    """
    Get crawl results that need extraction (retry support).

    Returns results that are:
    - In 'crawled' status (need extraction)
    - In 'failed' status but have crawled_content (extraction failed, can retry)

    Returns results from any crawl job, not just today's.
    """
    cursor.execute("""
        SELECT cr.id, cr.status, cr.source_id, cr.crawl_job_id,
               s.name, cc.notes, cj.started_at,
               CASE
                   WHEN cr.status = 'failed' AND cnt.crawled_content IS NOT NULL
                        AND cnt.extracted_content IS NULL THEN 'crawled'
                   ELSE cr.status
               END as effective_status
        FROM crawl_results cr
        JOIN sources s ON cr.source_id = s.id
        JOIN crawl_jobs cj ON cr.crawl_job_id = cj.id
        LEFT JOIN crawl_contents cnt ON cnt.crawl_result_id = cr.id
        LEFT JOIN crawl_configs cc ON cc.source_id = s.id
        WHERE s.disabled = FALSE
          AND s.deleted_at IS NULL
          AND (
              cr.status = 'crawled'
              OR (cr.status = 'failed' AND cnt.crawled_content IS NOT NULL)
          )
        ORDER BY cr.status, cj.started_at DESC
    """)

    results = []
    for row in cursor.fetchall():
        results.append(
            {
                "crawl_result_id": row[0],
                "status": row[7],  # effective_status
                "original_status": row[1],
                "source_id": row[2],
                "crawl_job_id": row[3],
                "name": row[4],
                "notes": row[5],
                "started_at": row[6],
                # Backward-compatible aliases (callers updated in later phases)
                "website_id": row[2],
                "crawl_run_id": row[3],
                "run_date": row[6],
            }
        )

    return results


def get_crawled_content(cursor, crawl_result_id):
    """Get crawled content for a crawl result."""
    cursor.execute(
        "SELECT crawled_content FROM crawl_contents WHERE crawl_result_id = %s",
        (crawl_result_id,),
    )
    result = cursor.fetchone()
    return result[0] if result else None


def get_existing_upcoming_events(cursor, source_id):
    """
    Get existing upcoming events from a source for inclusion in extraction prompt.

    Returns active events with occurrences from today onwards,
    formatted as JSON-compatible dicts.
    """
    cursor.execute(
        """
        SELECT
            e.id, e.name, e.description,
            l.name as location, e.sublocation,
            STRING_AGG(
                json_build_object(
                    'start_date', eo.start_date,
                    'start_time', eo.start_time,
                    'end_date', eo.end_date,
                    'end_time', eo.end_time
                )::text,
                ','
                ORDER BY eo.start_date
            ) as occurrences_json,
            STRING_AGG(DISTINCT eu.url, ',' ORDER BY eu.url) as urls,
            STRING_AGG(DISTINCT t.name, ',' ORDER BY t.name) as tags,
            e.emoji
        FROM events e
        LEFT JOIN locations l ON e.location_id = l.id
        LEFT JOIN event_occurrences eo ON e.id = eo.event_id
        LEFT JOIN event_urls eu ON e.id = eu.event_id
        LEFT JOIN event_tags et ON e.id = et.event_id
        LEFT JOIN tags t ON et.tag_id = t.id
        JOIN event_sources es ON es.event_id = e.id
        WHERE es.source_id = %s
          AND e.status = 'active'
          AND e.deleted_at IS NULL
          AND eo.start_date >= CURRENT_DATE
        GROUP BY e.id, e.name, e.description, l.name, e.sublocation, e.emoji
        ORDER BY MIN(eo.start_date)
    """,
        (source_id,),
    )

    events = []
    for row in cursor.fetchall():
        event = {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "location": row[3],
            "sublocation": row[4],
            "occurrences": json.loads(f"[{row[5]}]") if row[5] else [],
            "urls": row[6].split(",") if row[6] else [],
            "hashtags": row[7].split(",") if row[7] else [],
            "emoji": row[8],
        }
        events.append(event)

    return events
