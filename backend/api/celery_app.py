from celery import Celery
from kombu import Queue

from api.config import get_settings
from api.task_names import GEOCODE_LOCATION

settings = get_settings()

celery = Celery("fomoccs")

celery.conf.update(
    broker_url=settings.redis_url,
    result_backend=settings.redis_url,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_queue="default",
    task_queues=(
        Queue("default"),
        Queue("geocoding"),
    ),
    task_routes={
        GEOCODE_LOCATION: {"queue": "geocoding"},
    },
    task_default_retry_delay=60,
    task_max_retries=3,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery.autodiscover_tasks(["api.tasks"])
