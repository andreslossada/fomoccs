"""
Task name constants for Celery tasks.

This module mirrors backend/api/task_names.py. The constants are duplicated
here to avoid importing from the backend package. They MUST stay in sync with
the backend constants.
"""

PROCESS_CRAWL_JOB = "backend.process_crawl_job"
