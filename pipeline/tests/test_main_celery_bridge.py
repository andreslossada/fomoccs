"""Tests for the USE_CELERY bridge mode in main.py."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_browser_source():
    """Return a minimal browser-mode source dict."""
    return {
        "id": 1,
        "name": "Test Source",
        "crawl_mode": "browser",
        "urls": ["https://example.com"],
        "notes": "",
        "text_mode": True,
        "light_mode": True,
        "use_stealth": False,
        "process_images": 0,
        "base_url": "",
        "max_batches": None,
    }


def _make_mock_connection():
    """Return a mock DB connection with a cursor."""
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


def _make_mock_token_tracker():
    """Return a mock TokenTracker."""
    tracker = MagicMock()
    tracker.api_calls = 0
    tracker.merge = MagicMock()
    return tracker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCeleryBridgeMode:
    """Tests for USE_CELERY=true pipeline behaviour."""

    def test_main_celery_mode_stops_after_extract(self, monkeypatch):
        """
        With USE_CELERY=true the pipeline publishes the crawl job to Celery and
        returns True without calling processor.process_events or
        merger.merge_extracted_events.
        """
        import main

        monkeypatch.setenv("USE_CELERY", "true")
        monkeypatch.setattr(main, "USE_CELERY", True)

        mock_conn, mock_cursor = _make_mock_connection()

        # DB mocks
        monkeypatch.setattr("db.create_connection", lambda: mock_conn)
        monkeypatch.setattr("db.get_incomplete_crawl_results", lambda cursor: [])
        monkeypatch.setattr(
            "db.get_sources_due_for_crawling",
            lambda cursor, source_ids=None: [_make_browser_source()],
        )
        monkeypatch.setattr("db.create_crawl_job", lambda cursor, conn: 42)
        monkeypatch.setattr("db.complete_crawl_job", MagicMock())
        monkeypatch.setattr("db.save_crawl_summary", MagicMock())

        # Crawler mock — crawl_source returns a result_id
        mock_crawl_source = AsyncMock(return_value=101)
        monkeypatch.setattr("crawler.crawl_source", mock_crawl_source)
        monkeypatch.setattr(
            "crawler.get_browser_config", MagicMock(return_value=MagicMock())
        )

        # Extractor mock — extract_events returns (True, tracker)
        mock_tracker = _make_mock_token_tracker()
        mock_extract_events = AsyncMock(return_value=(True, mock_tracker))
        monkeypatch.setattr("extractor.extract_events", mock_extract_events)

        # Celery publisher mock
        mock_publish = MagicMock(return_value="task-xyz")
        monkeypatch.setattr("celery_publisher.publish_process_crawl_job", mock_publish)

        # processor and merger — should NOT be called
        mock_process_events = MagicMock(return_value=(0, MagicMock()))
        mock_merge = MagicMock(return_value=(0, 0))
        monkeypatch.setattr("processor.process_events", mock_process_events)
        monkeypatch.setattr("merger.merge_extracted_events", mock_merge)

        # AsyncWebCrawler mock
        mock_web_crawler = AsyncMock()
        mock_crawler_ctx = MagicMock()
        mock_crawler_ctx.__aenter__ = AsyncMock(return_value=mock_web_crawler)
        mock_crawler_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("main.AsyncWebCrawler", return_value=mock_crawler_ctx):
            result = asyncio.run(main.run_pipeline())

        assert result is True
        mock_publish.assert_called_once_with(42)
        mock_process_events.assert_not_called()
        mock_merge.assert_not_called()

    def test_main_legacy_mode_runs_full_pipeline(self, monkeypatch):
        """
        With USE_CELERY unset/false the pipeline runs processor and merger and
        does NOT call publish_process_crawl_job.
        """
        import main

        monkeypatch.delenv("USE_CELERY", raising=False)
        monkeypatch.setattr(main, "USE_CELERY", False)

        mock_conn, mock_cursor = _make_mock_connection()

        monkeypatch.setattr("db.create_connection", lambda: mock_conn)
        monkeypatch.setattr("db.get_incomplete_crawl_results", lambda cursor: [])
        monkeypatch.setattr(
            "db.get_sources_due_for_crawling",
            lambda cursor, source_ids=None: [_make_browser_source()],
        )
        monkeypatch.setattr("db.create_crawl_job", lambda cursor, conn: 42)
        monkeypatch.setattr("db.complete_crawl_job", MagicMock())
        monkeypatch.setattr("db.save_crawl_summary", MagicMock())

        mock_crawl_source = AsyncMock(return_value=101)
        monkeypatch.setattr("crawler.crawl_source", mock_crawl_source)
        monkeypatch.setattr(
            "crawler.get_browser_config", MagicMock(return_value=MagicMock())
        )

        mock_tracker = _make_mock_token_tracker()
        mock_extract_events = AsyncMock(return_value=(True, mock_tracker))
        monkeypatch.setattr("extractor.extract_events", mock_extract_events)

        # processor and merger SHOULD be called
        mock_loc_stats = MagicMock()
        mock_loc_stats.created = 0
        mock_loc_stats.merge = MagicMock()
        mock_loc_stats.summary = MagicMock(return_value="")
        mock_process_events = MagicMock(return_value=(5, mock_loc_stats))
        mock_merge = MagicMock(return_value=(3, 2))
        monkeypatch.setattr("processor.process_events", mock_process_events)
        monkeypatch.setattr(
            "processor.LocationStats", MagicMock(return_value=mock_loc_stats)
        )
        monkeypatch.setattr("merger.merge_extracted_events", mock_merge)

        # publisher should NOT be called
        mock_publish = MagicMock(return_value="task-xyz")
        monkeypatch.setattr("celery_publisher.publish_process_crawl_job", mock_publish)

        mock_web_crawler = AsyncMock()
        mock_crawler_ctx = MagicMock()
        mock_crawler_ctx.__aenter__ = AsyncMock(return_value=mock_web_crawler)
        mock_crawler_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("main.AsyncWebCrawler", return_value=mock_crawler_ctx):
            result = asyncio.run(main.run_pipeline())

        assert result is True
        mock_process_events.assert_called()
        mock_merge.assert_called_once()
        mock_publish.assert_not_called()

    def test_main_celery_mode_without_redis_url_raises(self, monkeypatch):
        """
        With USE_CELERY=true but no REDIS_URL the pipeline propagates
        RuntimeError from publish_process_crawl_job and returns False.
        """
        import main

        monkeypatch.setenv("USE_CELERY", "true")
        monkeypatch.setattr(main, "USE_CELERY", True)
        monkeypatch.delenv("REDIS_URL", raising=False)

        mock_conn, mock_cursor = _make_mock_connection()

        monkeypatch.setattr("db.create_connection", lambda: mock_conn)
        monkeypatch.setattr("db.get_incomplete_crawl_results", lambda cursor: [])
        monkeypatch.setattr(
            "db.get_sources_due_for_crawling",
            lambda cursor, source_ids=None: [_make_browser_source()],
        )
        monkeypatch.setattr("db.create_crawl_job", lambda cursor, conn: 42)
        monkeypatch.setattr("db.complete_crawl_job", MagicMock())
        monkeypatch.setattr("db.save_crawl_summary", MagicMock())

        mock_crawl_source = AsyncMock(return_value=101)
        monkeypatch.setattr("crawler.crawl_source", mock_crawl_source)
        monkeypatch.setattr(
            "crawler.get_browser_config", MagicMock(return_value=MagicMock())
        )

        mock_tracker = _make_mock_token_tracker()
        mock_extract_events = AsyncMock(return_value=(True, mock_tracker))
        monkeypatch.setattr("extractor.extract_events", mock_extract_events)

        # publish raises RuntimeError (REDIS_URL missing)
        def _raise_runtime(*args, **kwargs):
            raise RuntimeError(
                "USE_CELERY=true requires REDIS_URL to be set but it is missing."
            )

        monkeypatch.setattr(
            "celery_publisher.publish_process_crawl_job", _raise_runtime
        )

        mock_web_crawler = AsyncMock()
        mock_crawler_ctx = MagicMock()
        mock_crawler_ctx.__aenter__ = AsyncMock(return_value=mock_web_crawler)
        mock_crawler_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("main.AsyncWebCrawler", return_value=mock_crawler_ctx):
            result = asyncio.run(main.run_pipeline())

        # run_pipeline catches Exception and returns False
        assert result is False
