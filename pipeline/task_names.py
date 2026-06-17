"""
Task name constants for Celery tasks.

SOURCE OF TRUTH: backend/api/task_names.py
This module mirrors the backend constants. They MUST stay in sync.
The pipeline cannot import from backend (different Python version / venv).

When adding a new task:
1. Define it in backend/api/task_names.py first
2. Copy it here
"""

PROCESS_CRAWL_JOB = "backend.process_crawl_job"
GEOCODE_LOCATION = "backend.geocode_location"
