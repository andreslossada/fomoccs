from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api.dependencies import CurrentUserDep, SessionDep
from api.models.base import CrawlJobStatus
from api.models.crawl import CrawlJob, CrawlResult
from api.schemas.common import PaginatedResponse
from api.schemas.crawl import (
    CrawlJobDetailResponse,
    CrawlJobListItem,
    CrawlResultDetailResponse,
)

router = APIRouter(prefix="/crawl-jobs", tags=["crawl-jobs"])


@router.get("/", response_model=PaginatedResponse[CrawlJobListItem])
async def list_crawl_jobs(
    db: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: CrawlJobStatus | None = None,
) -> PaginatedResponse[CrawlJobListItem]:
    stmt = select(CrawlJob).order_by(CrawlJob.started_at.desc())
    count_stmt = select(func.count(CrawlJob.id))

    if status_filter is not None:
        stmt = stmt.where(CrawlJob.status == status_filter)
        count_stmt = count_stmt.where(CrawlJob.status == status_filter)

    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    jobs = result.scalars().all()
    total = await db.scalar(count_stmt) or 0

    data = [CrawlJobListItem.model_validate(j) for j in jobs]
    return PaginatedResponse(data=data, total=total)


@router.get("/{job_id}", response_model=CrawlJobDetailResponse)
async def get_crawl_job(
    job_id: int,
    db: SessionDep,
    user: CurrentUserDep,
) -> CrawlJobDetailResponse:
    stmt = (
        select(CrawlJob)
        .where(CrawlJob.id == job_id)
        .options(selectinload(CrawlJob.results), selectinload(CrawlJob.summary))
    )
    job = await db.scalar(stmt)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    return CrawlJobDetailResponse.model_validate(job)


@router.get("/{job_id}/results/{result_id}", response_model=CrawlResultDetailResponse)
async def get_crawl_result(
    job_id: int,
    result_id: int,
    db: SessionDep,
    user: CurrentUserDep,
) -> CrawlResultDetailResponse:
    stmt = (
        select(CrawlResult)
        .where(
            CrawlResult.id == result_id,
            CrawlResult.crawl_job_id == job_id,
        )
        .options(
            selectinload(CrawlResult.extracted_events),
            selectinload(CrawlResult.content),
            selectinload(CrawlResult.url_results),
        )
    )
    result = await db.scalar(stmt)
    if result is None:
        raise HTTPException(status_code=404, detail="Crawl result not found")
    return CrawlResultDetailResponse.model_validate(result)
