"""
Thin Celery publisher wrapper for the pipeline.

Used in USE_CELERY bridge mode to hand off post-extract processing to the
backend worker via a Celery task.
"""

import logging
import os

from celery import Celery
from task_names import PROCESS_CRAWL_JOB

logger = logging.getLogger(__name__)


def get_celery_app() -> Celery:
    """Return a Celery app configured with the Redis broker.

    Raises:
        RuntimeError: If REDIS_URL environment variable is not set.
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError(
            "USE_CELERY=true requires REDIS_URL to be set but it is missing."
        )
    app = Celery("pipeline-publisher", broker=redis_url)
    app.conf.task_always_eager = False
    return app


def publish_process_crawl_job(crawl_job_id: int) -> str:
    """Publish a process_crawl_job Celery task for the given crawl_job_id.

    Args:
        crawl_job_id: The ID of the crawl job to process.

    Returns:
        The Celery task ID as a string.
    """
    app = get_celery_app()
    async_result = app.send_task(PROCESS_CRAWL_JOB, args=[crawl_job_id])
    task_id: str = async_result.id
    logger.info(
        "Published PROCESS_CRAWL_JOB for crawl_job_id=%s task_id=%s",
        crawl_job_id,
        task_id,
    )
    return task_id
