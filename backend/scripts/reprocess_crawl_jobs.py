"""Re-run _process_crawl_job for the crawl jobs that have stuck
crawl_results in 'extracted' status. Used as a one-off maintenance
script; safe to delete after use.

This calls the same async function that Celery's
`backend.process_crawl_job` task wraps, so the end result is
identical to letting a worker pick the task up.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Allow `import api...` to resolve from backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.tasks.processing import _process_crawl_job  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("reprocess_crawl_jobs")


async def main() -> None:
    # Jobs 7, 10, 11, 15 each have at least one crawl_result in
    # 'extracted' status that the Celery worker never picked up.
    job_ids = [7, 10, 11, 15]

    for job_id in job_ids:
        log.info("=== Processing crawl job %d ===", job_id)
        try:
            await _process_crawl_job(job_id)
        except Exception:
            log.exception("Job %d failed", job_id)
            raise

    log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
