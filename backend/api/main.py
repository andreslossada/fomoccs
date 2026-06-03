import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from api.admin import setup_admin
from api.config import get_settings
from api.routers import auth, crawl_jobs, events, feed, locations, sources, tag_rules

app = FastAPI(title="FomoCCS API")


@app.exception_handler(IntegrityError)
async def integrity_error_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "Conflict: duplicate or constraint violation"},
    )


cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(locations.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(feed.router, prefix="/api/v1")
app.include_router(sources.router, prefix="/api/v1")
app.include_router(crawl_jobs.router, prefix="/api/v1")
app.include_router(tag_rules.router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/admin/process-crawl-job/{job_id}")
async def trigger_process_crawl_job(job_id: int, api_key: str):
    settings = get_settings()
    if api_key != settings.sync_api_key:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

    from api.database import AsyncSessionLocal
    from api.models.crawl import CrawlResult, CrawlContent, ExtractedEvent, CrawlResultStatus
    from sqlalchemy import select, update
    import json as json_mod

    # Step 1: Parse crawl_contents JSON and create ExtractedEvent records
    created = 0
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CrawlContent, CrawlResult.crawl_job_id, CrawlResult.source_id)
            .join(CrawlResult, CrawlContent.crawl_result_id == CrawlResult.id)
            .where(
                CrawlResult.crawl_job_id == job_id,
                CrawlResult.status == CrawlResultStatus.extracted,
                CrawlContent.extracted_content.isnot(None),
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        for cc, cj_id, source_id in rows:
            if not cc.extracted_content:
                continue
            try:
                parsed = json_mod.loads(cc.extracted_content)
                events = parsed.get("events", []) if isinstance(parsed, dict) else []
                if isinstance(parsed, list):
                    events = parsed
                for evt in events:
                    ee = ExtractedEvent(
                        crawl_result_id=cc.crawl_result_id,
                        name=str(evt.get("name", ""))[:500],
                        location_name=str(evt.get("location", evt.get("location_name", "")) or "")[:255],
                        sublocation=str(evt.get("sublocation", "") or "")[:255],
                        description=str(evt.get("description", "") or ""),
                        url=str(evt.get("url", "") or "")[:2000],
                        occurrences=json_mod.dumps(evt.get("occurrences", [])),
                        tags=json_mod.dumps(evt.get("tags", evt.get("hashtags", [])) or []),
                        emoji=str(evt.get("emoji", "") or ""),
                    )
                    session.add(ee)
                    created += 1
                # Mark crawl_result as having extracted events ready
                stmt_update = (
                    update(CrawlResult)
                    .where(CrawlResult.id == cc.crawl_result_id)
                    .values(status=CrawlResultStatus.extracted)
                )
                await session.execute(stmt_update)
            except Exception as e:
                print(f"Error parsing content for crawl_result {cc.crawl_result_id}: {e}")
        
        await session.commit()

    # Step 2: Process the extracted events
    from api.tasks.processing import _process_crawl_job
    await _process_crawl_job(job_id)
    return {"status": "ok", "job_id": job_id, "extracted_events_created": created}


# SQLAdmin mounted on /admin — must come before the catch-all static mount
setup_admin(app)

# Serve frontend static files — must be last so API routes take priority
# Skipped in Docker where frontend is served from Cloud Storage
_frontend_dir = Path(__file__).resolve().parent.parent.parent / "src"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
