import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from api.admin import setup_admin
from api.dependencies import verify_api_key
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


cors_origins = os.environ.get("CORS_ORIGINS", "").split(",")

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
async def trigger_process_crawl_job(
    job_id: int,
    _api_key: None = Depends(verify_api_key),
) -> dict[str, object]:
    import json as json_mod

    from sqlalchemy import exists, select, update

    from api.database import AsyncSessionLocal
    from api.models.base import CrawlResultStatus
    from api.models.crawl import (
        CrawlContent,
        CrawlResult,
        ExtractedEvent,
    )

    # Step 1: Parse crawl_contents JSON and create ExtractedEvent records
    created = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        stmt = (
            select(
                CrawlContent,
                CrawlResult.crawl_job_id,
                CrawlResult.source_id,
                CrawlResult.id,
            )
            .join(CrawlResult, CrawlContent.crawl_result_id == CrawlResult.id)
            .where(
                CrawlResult.crawl_job_id == job_id,
                CrawlResult.status == CrawlResultStatus.extracted,
                CrawlContent.extracted_content.isnot(None),
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        for cc, cj_id, source_id, cr_id in rows:
            if not cc.extracted_content:
                continue

            # Skip crawl_results that already have ExtractedEvent rows
            has_events = await session.scalar(
                select(exists().where(ExtractedEvent.crawl_result_id == cr_id))
            )
            if has_events:
                continue

            try:
                parsed = json_mod.loads(cc.extracted_content)
                events = parsed.get("events", []) if isinstance(parsed, dict) else []
                if isinstance(parsed, list):
                    events = parsed
                for evt in events:
                    raw_loc = evt.get("location", evt.get("location_name", "")) or ""
                    if isinstance(raw_loc, dict):
                        raw_loc = (
                            raw_loc.get("venue_name", raw_loc.get("name", "")) or ""
                        )

                    # LLM output is untrusted: every text field must be
                    # clipped to its column width in *characters*.
                    # ``emoji`` is VARCHAR(10) and multi-codepoint emoji
                    # (ZWJ sequences, flags, skin tones) exceed that.
                    emoji_raw = str(evt.get("emoji", "") or "").strip()[:10].strip()

                    # Each event gets its own SAVEPOINT. Without it, one
                    # row that violates a constraint poisons the session
                    # and every later event in the batch fails with
                    # PendingRollbackError — which is how a single bad
                    # emoji used to discard a whole source's events.
                    try:
                        async with session.begin_nested():
                            session.add(
                                ExtractedEvent(
                                    crawl_result_id=cc.crawl_result_id,
                                    name=str(evt.get("name", "") or "")[:500],
                                    location_name=str(raw_loc or "")[:255],
                                    sublocation=str(evt.get("sublocation", "") or "")[
                                        :255
                                    ],
                                    description=str(evt.get("description", "") or "")[
                                        :5000
                                    ],
                                    url=str(evt.get("url", "") or "")[:2000],
                                    occurrences=json_mod.dumps(
                                        evt.get("occurrences", [])
                                    ),
                                    tags=json_mod.dumps(
                                        evt.get("tags", evt.get("hashtags", [])) or []
                                    )[:2000],
                                    emoji=emoji_raw,
                                )
                            )
                            await session.flush()
                        created += 1
                    except Exception as e:
                        skipped += 1
                        print(
                            f"Skipped malformed event in result "
                            f"{cc.crawl_result_id}: {type(e).__name__}: "
                            f"{str(e)[:120]}"
                        )
                # Mark crawl_result as having extracted events ready
                stmt_update = (
                    update(CrawlResult)
                    .where(CrawlResult.id == cc.crawl_result_id)
                    .values(status=CrawlResultStatus.extracted)
                )
                await session.execute(stmt_update)
            except Exception as e:
                mid = cc.crawl_result_id
                print(f"Error parsing extracted content for result {mid}: {e}")
                # Keep going: a malformed payload for one URL must not
                # abort the rest of the job.
                await session.rollback()

        await session.commit()

    # Step 2: Process the extracted events
    from api.tasks.processing import _process_crawl_job

    await _process_crawl_job(job_id)
    return {
        "status": "ok",
        "job_id": job_id,
        "extracted_events_created": created,
        "extracted_events_skipped": skipped,
    }


# SQLAdmin mounted on /admin — must come before the catch-all static mount
setup_admin(app)

# Serve frontend static files — must be last so API routes take priority
# Skipped in Docker where frontend is served from Cloud Storage
#
# The served directory is the BUILD OUTPUT (dist/), not the sources (src/).
# The sources are not a runnable site: build.js substitutes the {{TOKEN}}
# placeholders in index.html and injects window.__CITY__ (map centre,
# timezone, filter roots) and API_BASE_URL. Serving src/ directly therefore
# shows literal {{SITE_NAME}} in the title and gives the map no centre, so it
# renders blank. dist/ is also what production serves.
#
# With no build yet, fall back to a page that says so rather than a broken
# app.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DIST_DIR = _REPO_ROOT / "dist"
_SRC_DIR = _REPO_ROOT / "src"
if (_DIST_DIR / "index.html").is_file():
    # follow_symlink: a dev build links data/, fonts/, images/ and vendor/ from
    # dist/ back to src/ so edits show up without a rebuild. Starlette refuses
    # to traverse symlinks by default, which turned every asset into a 404.
    app.mount(
        "/",
        StaticFiles(directory=_DIST_DIR, html=True, follow_symlink=True),
        name="frontend",
    )
elif _SRC_DIR.is_dir():
    # A checkout with no build yet. In Docker neither directory exists — the
    # frontend is served from Cloud Storage there — so nothing is registered
    # and the API root stays a plain 404.
    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False)
    async def _frontend_not_built() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<title>Fomoccs — frontend not built</title>"
            "<body style='font:16px/1.6 system-ui;padding:3rem;max-width:40rem;"
            "margin:auto'>"
            "<h1>The frontend has not been built</h1>"
            "<p>This server serves the built frontend from <code>dist/</code>, "
            "which does not exist yet.</p>"
            "<p>Build it with <code>npm run build</code> (or <code>npm run dev</code> "
            "to rebuild on change), then reload this page.</p>"
            "</body>",
            status_code=503,
        )
