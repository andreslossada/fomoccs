from datetime import datetime
from types import SimpleNamespace
from typing import Any

from pydantic import AnyHttpUrl, TypeAdapter

_URL_ADAPTER: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


def http_url(raw: str) -> AnyHttpUrl:
    """Build an ``AnyHttpUrl`` from a plain string for schema constructors.

    Pydantic v2 does accept ``str`` at runtime, but the constructor is typed
    against ``AnyHttpUrl``, so passing a bare string fails mypy strict.
    Going through the adapter keeps the call sites readable and gives us the
    same validation/normalisation the model would apply.
    """
    return _URL_ADAPTER.validate_python(raw)


def make_timestamp() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0)


def make_location_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        name="Test Location",
        short_name=None,
        very_short_name=None,
        address=None,
        description=None,
        lat=40.0,
        lng=-74.0,
        emoji=None,
        alt_emoji=None,
        website_url=None,
        type="venue",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_event_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        name="Test Event",
        short_name=None,
        description=None,
        emoji=None,
        location_id=1,
        sublocation=None,
        status="active",
        reviewed=False,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_user_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        email="test@example.com",
        display_name="Test User",
        is_admin=False,
        created_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_tag_obj(id: int = 1, name: str = "test-tag") -> SimpleNamespace:
    return SimpleNamespace(id=id, name=name)


def make_alternate_name_obj(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=1,
        alternate_name="Alternate Name",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_source_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        name="Test Source",
        type="crawler",
        trust_level=None,
        disabled=False,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_source_url_obj(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=1,
        url="https://example.com",
        js_code=None,
        sort_order=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_crawl_config_obj(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=1,
        source_id=1,
        notes=None,
        default_tags=None,
        crawl_frequency=24,
        crawl_frequency_locked=False,
        crawl_after=None,
        force_crawl=False,
        last_crawled_at=None,
        crawl_mode="browser",
        selector=None,
        num_clicks=None,
        js_code=None,
        keywords=None,
        max_pages=30,
        max_batches=None,
        json_api_config=None,
        delay_before_return_html=None,
        content_filter_threshold=None,
        scan_full_page=None,
        remove_overlay_elements=None,
        javascript_enabled=None,
        text_mode=None,
        light_mode=None,
        use_stealth=None,
        scroll_delay=None,
        crawl_timeout=None,
        process_images=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_crawl_job_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        status="running",
        started_at=now,
        completed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_crawl_result_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        crawl_job_id=1,
        source_id=1,
        status="pending",
        crawled_at=None,
        extracted_at=None,
        processed_at=None,
        error_message=None,
        created_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_crawl_content_obj(**overrides: Any) -> SimpleNamespace:
    defaults = dict(
        id=1,
        crawled_content="<html>content</html>",
        extracted_content="extracted text",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_extracted_event_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        crawl_result_id=1,
        name="Extracted Event",
        short_name=None,
        description=None,
        emoji=None,
        location_id=None,
        location_name=None,
        sublocation=None,
        url=None,
        occurrences=None,
        tags=None,
        created_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_tag_rule_obj(**overrides: Any) -> SimpleNamespace:
    now = make_timestamp()
    defaults = dict(
        id=1,
        rule_type="rewrite",
        pattern="old-tag",
        replacement="new-tag",
        created_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)
