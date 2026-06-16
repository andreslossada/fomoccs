from decimal import Decimal

import pytest
from pydantic import ValidationError

from api.models.base import CrawlMode, SourceType
from api.schemas.source import (
    CrawlConfigCreate,
    CrawlConfigResponse,
    CrawlConfigUpdate,
    SourceCreate,
    SourceDetailResponse,
    SourceListItem,
    SourceResponse,
    SourceUpdate,
    SourceUrlCreate,
    SourceUrlResponse,
)
from tests.schemas.helpers import (
    make_crawl_config_obj,
    make_source_obj,
    make_source_url_obj,
)

# ---------------------------------------------------------------------------
# SourceUrlCreate
# ---------------------------------------------------------------------------


def test_source_url_create_valid():
    url = SourceUrlCreate(url="https://example.com")
    assert str(url.url) == "https://example.com/"
    assert url.js_code is None
    assert url.sort_order == 0


def test_source_url_create_max_length():
    with pytest.raises(ValidationError):
        SourceUrlCreate(url="x" * 2001)


# ---------------------------------------------------------------------------
# SourceUrlResponse
# ---------------------------------------------------------------------------


def test_source_url_response_from_orm():
    obj = make_source_url_obj()
    resp = SourceUrlResponse.model_validate(obj, from_attributes=True)
    assert resp.id == 1
    assert resp.url == "https://example.com"


# ---------------------------------------------------------------------------
# CrawlConfigCreate
# ---------------------------------------------------------------------------


def test_crawl_config_create_minimal():
    config = CrawlConfigCreate(crawl_frequency=24, crawl_mode=CrawlMode.browser)
    assert config.crawl_frequency == 24
    assert config.crawl_mode == CrawlMode.browser
    assert config.max_pages == 30


def test_crawl_config_create_requires_frequency():
    with pytest.raises(ValidationError):
        CrawlConfigCreate(crawl_mode=CrawlMode.browser)  # type: ignore[call-arg]


def test_crawl_config_create_requires_mode():
    with pytest.raises(ValidationError):
        CrawlConfigCreate(crawl_frequency=24)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CrawlConfigUpdate
# ---------------------------------------------------------------------------


def test_crawl_config_update_all_optional():
    update = CrawlConfigUpdate()
    assert update.crawl_frequency is None
    assert update.crawl_mode is None


# ---------------------------------------------------------------------------
# CrawlConfigResponse
# ---------------------------------------------------------------------------


def test_crawl_config_response_from_orm():
    obj = make_crawl_config_obj()
    resp = CrawlConfigResponse.model_validate(obj, from_attributes=True)
    assert resp.id == 1
    assert resp.crawl_frequency == 24
    assert resp.crawl_mode == CrawlMode.browser


# ---------------------------------------------------------------------------
# SourceCreate
# ---------------------------------------------------------------------------


def test_source_create_valid_minimal():
    src = SourceCreate(name="Test", type=SourceType.crawler)
    assert src.name == "Test"
    assert src.type == SourceType.crawler
    assert src.urls == []
    assert src.crawl_config is None


def test_source_create_valid_full():
    src = SourceCreate(
        name="Full Source",
        type=SourceType.api,
        trust_level=Decimal("0.8"),
        disabled=True,
        urls=[SourceUrlCreate(url="https://example.com")],
        crawl_config=CrawlConfigCreate(
            crawl_frequency=12, crawl_mode=CrawlMode.json_api
        ),
    )
    assert len(src.urls) == 1
    assert src.crawl_config is not None
    assert src.trust_level == Decimal("0.8")


def test_source_create_name_required():
    with pytest.raises(ValidationError):
        SourceCreate(type=SourceType.crawler)  # type: ignore[call-arg]


def test_source_create_type_required():
    with pytest.raises(ValidationError):
        SourceCreate(name="Test")  # type: ignore[call-arg]


def test_source_create_name_max_length():
    with pytest.raises(ValidationError):
        SourceCreate(name="x" * 256, type=SourceType.crawler)


# ---------------------------------------------------------------------------
# SourceUpdate
# ---------------------------------------------------------------------------


def test_source_update_all_optional():
    update = SourceUpdate()
    assert update.name is None
    assert update.type is None


def test_source_update_partial():
    update = SourceUpdate(name="New Name")
    assert update.name == "New Name"


# ---------------------------------------------------------------------------
# SourceResponse / SourceListItem
# ---------------------------------------------------------------------------


def test_source_response_from_orm():
    obj = make_source_obj()
    resp = SourceResponse.model_validate(obj, from_attributes=True)
    assert resp.id == 1
    assert resp.name == "Test Source"
    assert resp.type == SourceType.crawler


def test_source_list_item_from_orm():
    obj = make_source_obj(trust_level=0.5)
    item = SourceListItem.model_validate(obj, from_attributes=True)
    assert item.trust_level == 0.5


# ---------------------------------------------------------------------------
# SourceDetailResponse
# ---------------------------------------------------------------------------


def test_source_detail_response_from_orm():
    obj = make_source_obj(
        urls=[make_source_url_obj()],
        crawl_config=make_crawl_config_obj(),
    )
    resp = SourceDetailResponse.model_validate(obj, from_attributes=True)
    assert len(resp.urls) == 1
    assert resp.crawl_config is not None
    assert resp.crawl_config.crawl_frequency == 24


def test_source_detail_response_no_config():
    obj = make_source_obj(urls=[], crawl_config=None)
    resp = SourceDetailResponse.model_validate(obj, from_attributes=True)
    assert resp.urls == []
    assert resp.crawl_config is None
