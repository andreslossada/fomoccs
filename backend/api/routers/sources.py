from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from api.dependencies import CurrentUserDep, SessionDep
from api.models.source import CrawlConfig, Source, SourceUrl
from api.schemas.common import PaginatedResponse
from api.schemas.source import (
    CrawlConfigCreate,
    CrawlConfigResponse,
    CrawlConfigUpdate,
    SourceCreate,
    SourceDetailResponse,
    SourceListItem,
    SourceUpdate,
    SourceUrlCreate,
    SourceUrlResponse,
)

router = APIRouter(prefix="/sources", tags=["sources"])


async def _get_source_or_404(db: SessionDep, source_id: int) -> Source:
    stmt = (
        select(Source)
        .where(Source.id == source_id, Source.active())
        .options(
            selectinload(Source.urls),
            selectinload(Source.crawl_config),
        )
    )
    source = await db.scalar(stmt)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


async def _check_duplicate_urls(db: SessionDep, urls: list[str]) -> None:
    """Raise 409 if any of the given URLs already exist in active source_urls."""
    if not urls:
        return
    stmt = select(SourceUrl.url).where(SourceUrl.active(), SourceUrl.url.in_(urls))
    existing = (await db.execute(stmt)).scalars().all()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"URL(s) already exist: {', '.join(existing)}",
        )


async def _refresh_source(db: SessionDep, source_id: int) -> Source:
    stmt = (
        select(Source)
        .where(Source.id == source_id)
        .options(
            selectinload(Source.urls),
            selectinload(Source.crawl_config),
        )
        .execution_options(populate_existing=True)
    )
    source = await db.scalar(stmt)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.get("/", response_model=PaginatedResponse[SourceListItem])
async def list_sources(
    db: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_deleted: bool = False,
) -> PaginatedResponse[SourceListItem]:
    stmt = select(Source).order_by(Source.name).limit(limit).offset(offset)
    count_stmt = select(func.count(Source.id))

    if not include_deleted:
        stmt = stmt.where(Source.active())
        count_stmt = count_stmt.where(Source.active())

    result = await db.execute(stmt)
    sources = result.scalars().all()
    total = await db.scalar(count_stmt) or 0

    data = [SourceListItem.model_validate(s) for s in sources]
    return PaginatedResponse(data=data, total=total)


@router.get("/{source_id}", response_model=SourceDetailResponse)
async def get_source(
    source_id: int,
    db: SessionDep,
    user: CurrentUserDep,
) -> SourceDetailResponse:
    source = await _get_source_or_404(db, source_id)
    return SourceDetailResponse.model_validate(source)


@router.post(
    "/", response_model=SourceDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_source(
    data: SourceCreate,
    db: SessionDep,
    user: CurrentUserDep,
) -> SourceDetailResponse:
    # Check for duplicate URLs within the request
    url_strings = [str(u.url) for u in data.urls]
    seen: set[str] = set()
    dupes = [u for u in url_strings if u in seen or seen.add(u)]  # type: ignore[func-returns-value]
    if dupes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Duplicate URLs in request: {', '.join(dupes)}",
        )
    await _check_duplicate_urls(db, url_strings)

    source = Source(
        name=data.name,
        type=data.type,
        trust_level=float(data.trust_level) if data.trust_level is not None else None,
        disabled=data.disabled,
    )
    db.add(source)
    await db.flush()

    for url_data in data.urls:
        db.add(
            SourceUrl(
                source_id=source.id,
                url=url_data.url,
                js_code=url_data.js_code,
                sort_order=url_data.sort_order,
            )
        )

    if data.crawl_config is not None:
        config = CrawlConfig(
            source_id=source.id,
            **data.crawl_config.model_dump(),
        )
        db.add(config)

    source_id = source.id  # capture before commit expires attributes

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more URLs already exist",
        )
    source = await _refresh_source(db, source_id)
    return SourceDetailResponse.model_validate(source)


@router.put("/{source_id}", response_model=SourceDetailResponse)
async def update_source(
    source_id: int,
    data: SourceUpdate,
    db: SessionDep,
    user: CurrentUserDep,
) -> SourceDetailResponse:
    source = await _get_source_or_404(db, source_id)

    update_fields = data.model_dump(exclude_unset=True)
    if "trust_level" in update_fields and update_fields["trust_level"] is not None:
        update_fields["trust_level"] = float(update_fields["trust_level"])
    for field, value in update_fields.items():
        setattr(source, field, value)

    await db.commit()
    source = await _refresh_source(db, source_id)
    return SourceDetailResponse.model_validate(source)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: int,
    db: SessionDep,
    user: CurrentUserDep,
) -> Response:
    source = await _get_source_or_404(db, source_id)

    source.soft_delete()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{source_id}/config", response_model=CrawlConfigResponse)
async def upsert_crawl_config(
    source_id: int,
    data: CrawlConfigCreate | CrawlConfigUpdate,
    db: SessionDep,
    user: CurrentUserDep,
) -> CrawlConfigResponse:
    source = await _get_source_or_404(db, source_id)

    if source.crawl_config is not None:
        # Update existing config
        update_fields = data.model_dump(exclude_unset=True)
        for field, value in update_fields.items():
            setattr(source.crawl_config, field, value)
    else:
        # Create new config -- require CrawlConfigCreate fields
        if not isinstance(data, CrawlConfigCreate):
            # If partial update data sent but no config exists, validate required fields
            dump = data.model_dump(exclude_unset=True)
            if "crawl_frequency" not in dump or "crawl_mode" not in dump:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="crawl_frequency and crawl_mode are required "
                    "when creating a new config",
                )
        config = CrawlConfig(
            source_id=source.id,
            **data.model_dump(exclude_unset=True),
        )
        db.add(config)

    await db.commit()
    source = await _refresh_source(db, source_id)
    assert source.crawl_config is not None
    return CrawlConfigResponse.model_validate(source.crawl_config)


@router.post(
    "/{source_id}/urls",
    response_model=SourceUrlResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_source_url(
    source_id: int,
    data: SourceUrlCreate,
    db: SessionDep,
    user: CurrentUserDep,
) -> SourceUrlResponse:
    # Verify source exists
    await _get_source_or_404(db, source_id)

    await _check_duplicate_urls(db, [str(data.url)])

    url = SourceUrl(
        source_id=source_id,
        url=data.url,
        js_code=data.js_code,
        sort_order=data.sort_order,
    )
    db.add(url)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"URL already exists: {data.url}",
        )
    return SourceUrlResponse.model_validate(url)


@router.delete("/{source_id}/urls/{url_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source_url(
    source_id: int,
    url_id: int,
    db: SessionDep,
    user: CurrentUserDep,
) -> Response:
    # Verify source exists
    await _get_source_or_404(db, source_id)

    url = await db.scalar(
        select(SourceUrl).where(
            SourceUrl.id == url_id,
            SourceUrl.source_id == source_id,
            SourceUrl.active(),
        )
    )
    if url is None:
        raise HTTPException(status_code=404, detail="URL not found")

    url.soft_delete()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
