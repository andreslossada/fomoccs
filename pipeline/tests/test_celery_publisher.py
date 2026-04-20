"""Tests for celery_publisher.py."""

import os
import sys
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import celery_publisher


def test_get_celery_app_requires_redis_url(monkeypatch):
    """get_celery_app raises RuntimeError when REDIS_URL is not set."""
    monkeypatch.delenv("REDIS_URL", raising=False)

    try:
        celery_publisher.get_celery_app()
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        assert "REDIS_URL" in str(exc)


def test_get_celery_app_returns_broker_url(monkeypatch):
    """get_celery_app returns a Celery app with the correct broker URL."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    app = celery_publisher.get_celery_app()

    assert app.conf.broker_url == "redis://localhost:6379/0"


def test_publish_process_crawl_job_sends_task(monkeypatch):
    """publish_process_crawl_job calls send_task and returns the task id."""
    mock_result = MagicMock()
    mock_result.id = "task-abc-123"

    mock_app = MagicMock()
    mock_app.send_task.return_value = mock_result

    with patch("celery_publisher.get_celery_app", return_value=mock_app):
        task_id = celery_publisher.publish_process_crawl_job(123)

    mock_app.send_task.assert_called_once_with("backend.process_crawl_job", args=[123])
    assert task_id == "task-abc-123"
