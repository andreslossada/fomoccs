"""
Event Processing Pipeline

Orchestrates the scraping and extraction workflow:

1. Crawl - Query sources table, crawl due sites, store in crawl_results
2. Stream - Each worker crawls a source then immediately extracts events
   (OpenCode Go → Gemini → ...), overlapping I/O across sources
3. Handoff - Publish crawl_job_id to backend Celery task for processing/merging

Usage:
    python main.py                     # Process all sources due for crawling
    python main.py --ids 941           # Process specific source ID(s)
    python main.py --ids 941,942,943   # Process multiple source IDs
    python main.py --limit 5           # Only crawl first 5 sources due
"""

import argparse
import asyncio
import json
import os
import sys

from dotenv import load_dotenv
load_dotenv()

import crawler
import db
import extractor
from celery_publisher import publish_process_crawl_job
from crawl4ai import AsyncWebCrawler
from extractor import TokenTracker


async def run_pipeline(source_ids=None, limit=None, tier=None):
    """Execute the scraping and extraction pipeline.

    Args:
        source_ids: Optional list of source IDs to process. If None, processes
                    all sources due for crawling based on crawl_frequency.
        limit: Optional maximum number of sources to crawl.
        tier: Optional tier (1/2/3) to filter by. Used by the cadence
              Cloud Scheduler jobs (e.g. ``--tier 1`` for the 6h ticketing
              job). When set, source_ids and crawl_frequency are ignored —
              every source at this tier is processed.
    """
    print(f"{'=' * 60}")
    print("EVENT PROCESSING PIPELINE")
    if source_ids:
        print(f"  Filtering to source IDs: {', '.join(map(str, source_ids))}")
    print(f"{'=' * 60}\n")

    # Connect to database
    connection = db.create_connection()
    if not connection:
        print("Failed to connect to database")
        return False

    cursor = connection.cursor()

    # Per-hostname throttle — shared across all streaming workers in this run
    # so they coordinate per-domain request pacing. Honors per-source override
    # and tier defaults.
    throttle = crawler.HostnameThrottle()

    try:
        # Check for incomplete crawl results first
        print(f"{'=' * 60}")
        print("STEP 0: Checking for Incomplete Crawl Results")
        print(f"{'=' * 60}")

        incomplete_results = db.get_incomplete_crawl_results(cursor)
        incomplete_crawled = [r for r in incomplete_results if r["status"] == "crawled"]

        def print_incomplete_status(results, action_needed):
            """Print status summary for a list of incomplete results."""
            retry_count = sum(
                1 for r in results if r.get("original_status") == "failed"
            )
            incomplete_count = len(results) - retry_count
            status_parts = []
            if incomplete_count:
                status_parts.append(f"{incomplete_count} incomplete")
            if retry_count:
                status_parts.append(f"{retry_count} failed retries")
            print(
                f"  - {len(results)} need {action_needed} ({', '.join(status_parts)})"
            )
            for r in results:
                suffix = " [retry]" if r.get("original_status") == "failed" else ""
                print(f"      {r['name']} (job: {r['started_at']}){suffix}")

        if incomplete_results:
            print(f"Found {len(incomplete_results)} crawl result(s) to process:")
            if incomplete_crawled:
                print_incomplete_status(incomplete_crawled, "extraction")
        else:
            print("No incomplete crawl results found.")

        # STEP 1: Get sources due for crawling
        print(f"\n{'=' * 60}")
        print("STEP 1: Finding Sources Due for Crawling")
        print(f"{'=' * 60}")

        sources = db.get_sources_due_for_crawling(cursor, source_ids, tier=tier)
        if limit and len(sources) > limit:
            print(f"Found {len(sources)} source(s) due, limiting to {limit}")
            sources = sources[:limit]
        elif source_ids:
            print(f"Found {len(sources)} source(s) matching specified IDs")
        else:
            print(f"Found {len(sources)} source(s) due for crawling")

        # Check if there's any work to do
        has_work = len(sources) > 0 or len(incomplete_results) > 0

        if not has_work:
            print("\nNo sources need crawling and no incomplete results to process.")
            print("Pipeline completed (no work to do).")
            return True

        # Split sources by crawl mode
        json_api_sources = [s for s in sources if s.get("crawl_mode") == "json_api"]
        instagram_sources = [s for s in sources if s.get("crawl_mode") == "instagram"]
        browser_sources = [
            s for s in sources if s.get("crawl_mode", "browser") == "browser"
        ]

        for s in sources:
            mode = s.get("crawl_mode", "browser")
            url_count = len(s.get("urls", []))
            if mode == "instagram":
                ig_user = (s.get("json_api_config") or {}).get("username", "?")
                print(f"  - {s['name']} (@{ig_user}, mode=instagram)")
            else:
                print(f"  - {s['name']} ({url_count} URL(s), mode={mode})")

        # Create crawl job
        crawl_job_id = db.create_crawl_job(cursor, connection)
        print(f"\nCrawl job ID: {crawl_job_id}")

        # STEP 2: Streaming Processing (crawl → extract per source)
        print(f"\n{'=' * 60}")
        print("STEP 2: Processing Sources (crawl + extract)")
        print(f"{'=' * 60}")

        num_workers = int(os.getenv("PIPELINE_CONCURRENCY", "2"))

        def get_browser_key(s):
            """Group key from browser-level settings (defaults: text=True, light=True, stealth=False)."""
            return (
                s.get("text_mode") if s.get("text_mode") is not None else True,
                s.get("light_mode") if s.get("light_mode") is not None else True,
                s.get("use_stealth") if s.get("use_stealth") is not None else False,
            )

        # Unified queue — JSON API first (fast), then Instagram, then browser, then extract-only
        queue = asyncio.Queue()
        for source in json_api_sources:
            await queue.put({"type": "json_api", "source": source})
        for source in instagram_sources:
            await queue.put({"type": "instagram", "source": source})
        for source in browser_sources:
            await queue.put({"type": "browser", "source": source})
        for r in incomplete_crawled:
            await queue.put(
                {
                    "type": "extract_only",
                    "crawl_result_id": r["crawl_result_id"],
                    "name": r["name"],
                    "notes": r.get("notes", ""),
                    "started_at": r.get("started_at"),
                }
            )

        queued = (
            len(json_api_sources)
            + len(instagram_sources)
            + len(browser_sources)
            + len(incomplete_crawled)
        )
        print(
            f"  Queued {queued} items ({len(json_api_sources)} JSON API, "
            f"{len(instagram_sources)} Instagram, "
            f"{len(browser_sources)} browser, "
            f"{len(incomplete_crawled)} extract-only)\n"
        )

        crawl_results = []
        extracted_results = []
        job_tracker = TokenTracker()

        async def stream_worker():
            """Streaming worker: crawl then immediately extract one source at a time."""
            results = []
            tracker = TokenTracker()
            browser = None
            browser_key = None

            try:
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    try:
                        type_ = item["type"]

                        if type_ == "json_api":
                            source = item["source"]
                            conn = db.create_connection()
                            if not conn:
                                continue
                            cur = conn.cursor()
                            try:
                                result_id, _raw_data = await crawler.crawl_json_api(
                                    source, cur, conn, crawl_job_id, throttle=throttle
                                )
                                if result_id:
                                    results.append((result_id, source))
                            except Exception as e:
                                print(f"    - Error crawling {source['name']}: {e}")
                            finally:
                                cur.close()
                                conn.close()

                        elif type_ == "instagram":
                            source = item["source"]
                            conn = db.create_connection()
                            if not conn:
                                continue
                            cur = conn.cursor()
                            try:
                                result_id = await crawler.crawl_instagram(
                                    source, cur, conn, crawl_job_id
                                )
                            except Exception as e:
                                print(
                                    f"    - Error crawling {source['name']}: {e}"
                                )
                                result_id = None
                            finally:
                                cur.close()
                                conn.close()

                            if result_id:
                                crawl_results.append((result_id, source))

                                # Extract immediately — same LLM pipeline
                                conn = db.create_connection()
                                if conn:
                                    cur = conn.cursor()
                                    try:
                                        success, t = await extractor.extract_events(
                                            cur,
                                            conn,
                                            result_id,
                                            source["name"],
                                            source.get("notes", ""),
                                        )
                                        conn.commit()
                                        tracker.merge(t)
                                        if success:
                                            results.append((result_id, source))
                                    except Exception as e:
                                        print(
                                            f"    - Error extracting {source['name']}: {e}"
                                        )
                                    finally:
                                        cur.close()
                                        conn.close()

                        elif type_ == "browser":
                            source = item["source"]
                            key = get_browser_key(source)

                            # Recreate browser if config changed
                            if browser is None or key != browser_key:
                                if browser is not None:
                                    await browser.__aexit__(None, None, None)
                                config = crawler.get_browser_config(
                                    text_mode=key[0],
                                    light_mode=key[1],
                                    use_stealth=key[2],
                                )
                                browser = AsyncWebCrawler(config=config)
                                await browser.__aenter__()
                                browser_key = key

                            # Crawl
                            conn = db.create_connection()
                            if not conn:
                                continue
                            cur = conn.cursor()
                            try:
                                result_id = await crawler.crawl_source(
                                    browser, source, cur, conn, crawl_job_id,
                                    throttle=throttle,
                                )
                            except Exception as e:
                                print(f"    - Error crawling {source['name']}: {e}")
                                result_id = None
                            finally:
                                cur.close()
                                conn.close()

                            if result_id:
                                crawl_results.append((result_id, source))

                                # Extract immediately — overlap crawl of other sources
                                conn = db.create_connection()
                                if conn:
                                    cur = conn.cursor()
                                    try:
                                        success, t = await extractor.extract_events(
                                            cur,
                                            conn,
                                            result_id,
                                            source["name"],
                                            source.get("notes", ""),
                                            use_vision=source.get("process_images") == 1,
                                            base_url=source.get("base_url", ""),
                                            max_batches=source.get("max_batches"),
                                        )
                                        conn.commit()
                                        tracker.merge(t)
                                        if success:
                                            results.append((result_id, source))
                                    except Exception as e:
                                        print(
                                            f"    - Error extracting {source['name']}: {e}"
                                        )
                                    finally:
                                        cur.close()
                                        conn.close()

                        elif type_ == "extract_only":
                            conn = db.create_connection()
                            if not conn:
                                continue
                            cur = conn.cursor()
                            try:
                                success, t = await extractor.extract_events(
                                    cur,
                                    conn,
                                    item["crawl_result_id"],
                                    item["name"],
                                    item.get("notes", ""),
                                )
                                conn.commit()
                                tracker.merge(t)
                                if success:
                                    results.append(
                                        (
                                            item["crawl_result_id"],
                                            {
                                                "name": item["name"],
                                                "notes": item.get("notes", ""),
                                                "started_at": item.get("started_at"),
                                            },
                                        )
                                    )
                            except Exception as e:
                                print(f"    - Error extracting {item['name']}: {e}")
                            finally:
                                cur.close()
                                conn.close()
                    finally:
                        queue.task_done()
            finally:
                if browser is not None:
                    await browser.__aexit__(None, None, None)

            return results, tracker

        # Launch workers and wait for all to complete
        worker_results = await asyncio.gather(
            *[stream_worker() for _ in range(num_workers)]
        )

        for wr_results, wr_tracker in worker_results:
            extracted_results.extend(wr_results)
            job_tracker.merge(wr_tracker)

        print(
            f"\nProcessed {len(extracted_results)} source(s) "
            f"({len(crawl_results)} crawled: "
            f"{len([r for r in crawl_results if r[1].get('crawl_mode') == 'browser'])} browser, "
            f"{len(json_api_sources)} JSON API, "
            f"{len(instagram_sources)} Instagram)\n"
        )

        # Save token usage summary, mark crawl job complete, publish to backend
        if job_tracker.api_calls > 0:
            # Aggregate rate-limit count from the live provider chain so we
            # can see how many calls hit 429 across the whole run.
            from extractor import PROVIDER_CHAIN

            total_rate_limited = sum(p.total_rate_limited for p in PROVIDER_CHAIN)
            if total_rate_limited:
                job_tracker._rate_limited_total = total_rate_limited
            db.save_crawl_summary(cursor, crawl_job_id, job_tracker)
        db.complete_crawl_job(cursor, connection, crawl_job_id)
        if os.getenv("USE_CELERY", "").lower() == "true":
            task_id = publish_process_crawl_job(crawl_job_id)
            print(f"  - task_id: {task_id}")
        elif os.getenv("API_BASE_URL"):
            # Call backend processing endpoint directly (no Celery needed)
            base = os.getenv("API_BASE_URL", "").rstrip("/")
            api_key = os.getenv("SYNC_API_KEY", "changeme")
            url = f"{base}/api/v1/admin/process-crawl-job/{crawl_job_id}"
            print(f"Calling processing endpoint: {url}")
            try:
                import urllib.request
                req = urllib.request.Request(url, data=b"", method="POST")
                req.add_header("Content-Length", "0")
                req.add_header("X-API-Key", api_key)
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = json.loads(resp.read().decode())
                    print(f"Processing result: {body}")
            except Exception as e:
                print(f"Warning: Processing endpoint call failed: {e}")
        else:
            print("Skipping Celery handoff (USE_CELERY not set)")

        print(f"{'=' * 60}")
        print("PIPELINE COMPLETED SUCCESSFULLY")
        print(f"{'=' * 60}\n")

        print("Summary:")
        total_crawled = (
            len(crawl_results)
            + len(json_api_sources)
            + len(instagram_sources)
        )
        print(f"  - Sources crawled: {total_crawled}")
        if incomplete_crawled:
            print(f"  - Resumed extractions: {len(incomplete_crawled)}")
        print(f"  - Events extracted: {len(extracted_results)}")
        print(f"  - crawl_job_id: {crawl_job_id}")

        if job_tracker.api_calls > 0:
            print(f"\n{'=' * 60}")
            print("AI API USAGE SUMMARY")
            print(f"{'=' * 60}")
            print(job_tracker.summary())

        return True

    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user.")
        return False
    except Exception as e:
        print(f"\n\nPipeline Error: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        cursor.close()
        connection.close()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Event Processing Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                     # Process all sources due for crawling
  python main.py --ids 941           # Process specific source ID
  python main.py --ids 941,942,943   # Process multiple source IDs
  python main.py --limit 5           # Only crawl first 5 sources due
  python main.py --tier 1            # Force-process every active tier-1 source
        """,
    )
    parser.add_argument(
        "--ids",
        "--source-ids",
        type=str,
        help="Comma-separated list of source IDs to process (ignores crawl_frequency)",
    )
    parser.add_argument(
        "--limit", "-n", type=int, help="Maximum number of sources to crawl"
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[1, 2, 3],
        help="Process every active source at this tier (ignores crawl_frequency). "
             "Used by cadence Cloud Scheduler jobs (e.g. 6h for tier 1).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    source_ids = None
    if args.ids:
        source_ids = [int(id.strip()) for id in args.ids.split(",")]

    success = asyncio.run(run_pipeline(source_ids, args.limit, tier=args.tier))
    sys.exit(0 if success else 1)
