"""Re-run ``_process_crawl_job`` for every crawl job that still has
CrawlResults in ``extracted`` status. Used as a one-off maintenance
script; safe to delete after use.

Finds every distinct ``crawl_job_id`` with at least one extracted
result and calls :func:`api.tasks.processing._process_crawl_job` on it.
That function is idempotent — if ``extracted_events`` already exist,
the step that resolves locations/processes tags is re-run but the
``CrawlContent`` JSON parse is skipped (see
``backend/api/main.py:trigger_process_crawl_job`` line 92).

This is the same async function that the Celery
``backend.process_crawl_job`` task wraps.
"""

import asyncio
import logging
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.tasks.processing import _process_crawl_job  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("reprocess_crawl_jobs")


def _dsn() -> str:
    from os import environ
    return (
        f"postgresql://{environ['DB_USER']}:{environ['DB_PASS']}"
        f"@{environ['DB_HOST']}:{environ.get('DB_PORT', '5432')}"
        f"/{environ['DB_NAME']}"
    )


def find_pending_job_ids() -> list[int]:
    """Return distinct ``crawl_job_id`` values that still have unprocessed
    ``extracted`` crawl results, sorted oldest first (cheap jobs run
    first so we don't starve recent ones if interrupted)."""
    conn = psycopg2.connect(_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cr.crawl_job_id, MIN(cj.started_at) AS oldest
                FROM crawl_results cr
                LEFT JOIN crawl_jobs cj ON cj.id = cr.crawl_job_id
                WHERE cr.status = 'extracted'
                GROUP BY cr.crawl_job_id
                ORDER BY oldest ASC NULLS LAST
            """)
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


async def main(limit: int | None = None) -> None:
    job_ids = find_pending_job_ids()
    log.info("Found %d jobs with extracted CrawlResults to process", len(job_ids))
    if limit is not None:
        job_ids = job_ids[:limit]
        log.info("Limiting to first %d jobs", limit)

    succeeded = 0
    failed = 0
    for job_id in job_ids:
        log.info("=== Processing crawl job %d ===", job_id)
        try:
            await _process_crawl_job(job_id)
            succeeded += 1
        except Exception:
            log.exception("Job %d failed", job_id)
            failed += 1
            continue

    log.info("Done. succeeded=%d failed=%d", succeeded, failed)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    load_dotenv(BACKEND_DIR / ".env")
    asyncio.run(main(limit=n))
