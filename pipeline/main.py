"""
Event Processing Pipeline

Orchestrates the complete event processing workflow:

1. Crawl - Query sources table, crawl due sites, store in crawl_results
2. Extract - Use Gemini AI to extract structured event data
3. Process - Parse responses, enrich with location data, store in extracted_events
4. Merge - Deduplicate extracted_events into final events table

Usage:
    python main.py                     # Process all sources due for crawling
    python main.py --ids 941           # Process specific source ID(s)
    python main.py --ids 941,942,943   # Process multiple source IDs
    python main.py --limit 5           # Only crawl first 5 sources due
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

import crawler
import db
import extractor
import location_resolver
import merger
import processor
from crawl4ai import AsyncWebCrawler
from extractor import TokenTracker

USE_CELERY = os.getenv("USE_CELERY", "false").lower() == "true"


async def run_pipeline(source_ids=None, limit=None):
    """Execute the complete event processing pipeline.

    Args:
        source_ids: Optional list of source IDs to process. If None, processes
                    all sources due for crawling based on crawl_frequency.
        limit: Optional maximum number of sources to crawl.
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

    try:
        # Check for incomplete crawl results first
        print(f"{'=' * 60}")
        print("STEP 0: Checking for Incomplete Crawl Results")
        print(f"{'=' * 60}")

        incomplete_results = db.get_incomplete_crawl_results(cursor)
        incomplete_crawled = [r for r in incomplete_results if r["status"] == "crawled"]
        incomplete_extracted = [
            r for r in incomplete_results if r["status"] == "extracted"
        ]

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
            if incomplete_extracted:
                print_incomplete_status(incomplete_extracted, "processing")
        else:
            print("No incomplete crawl results found.")

        # STEP 1: Get sources due for crawling
        print(f"\n{'=' * 60}")
        print("STEP 1: Finding Sources Due for Crawling")
        print(f"{'=' * 60}")

        sources = db.get_sources_due_for_crawling(cursor, source_ids)
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
        browser_sources = [
            s for s in sources if s.get("crawl_mode", "browser") == "browser"
        ]

        for s in sources:
            mode = s.get("crawl_mode", "browser")
            url_count = len(s.get("urls", []))
            print(f"  - {s['name']} ({url_count} URL(s), mode={mode})")

        # Create crawl job
        crawl_job_id = db.create_crawl_job(cursor, connection)
        print(f"\nCrawl job ID: {crawl_job_id}")

        # STEP 2: Crawl sources
        print(f"\n{'=' * 60}")
        print("STEP 2: Crawling Sources")
        print(f"{'=' * 60}")

        # Number of concurrent workers for crawling and extraction
        num_workers = 6

        crawl_results = []
        extracted_results = []
        resolver_locations_created = 0

        # Crawl JSON API sources first (fast, no browser needed)
        if json_api_sources:
            print(f"\n  JSON API crawling ({len(json_api_sources)} site(s))...")
            for source in json_api_sources:
                conn = db.create_connection()
                if not conn:
                    continue
                cur = conn.cursor()
                try:
                    result_id, raw_data = await crawler.crawl_json_api(
                        source, cur, conn, crawl_job_id
                    )
                    if result_id:
                        # Auto-create missing venues from structured API data
                        if raw_data and isinstance(raw_data, dict):
                            created = location_resolver.resolve_locations(
                                raw_data, source["id"], cur, conn
                            )
                            resolver_locations_created += created
                            if created > 0:
                                print(f"    - Auto-created {created} new location(s)")
                        # JSON API sources are directly mapped to extracted;
                        # route them past the Gemini extraction queue.
                        extracted_results.append((result_id, source))
                except Exception as e:
                    print(f"    - Error crawling {source['name']}: {e}")
                finally:
                    cur.close()
                    conn.close()

        # Group sources by browser settings (text_mode, light_mode, use_stealth)
        # These are browser-level settings, so sources with different settings
        # need separate browser instances
        def get_browser_key(s):
            """Group key from browser-level settings (defaults: text=True, light=True, stealth=False)."""
            return (
                s.get("text_mode") if s.get("text_mode") is not None else True,
                s.get("light_mode") if s.get("light_mode") is not None else True,
                s.get("use_stealth") if s.get("use_stealth") is not None else False,
            )

        source_batches = {}
        for source in browser_sources:
            key = get_browser_key(source)
            source_batches.setdefault(key, []).append(source)

        for (
            text_mode,
            light_mode,
            use_stealth,
        ), batch_sources in source_batches.items():
            if len(source_batches) > 1:
                stealth_str = ", stealth=True" if use_stealth else ""
                print(
                    f"\n  Batch: text_mode={text_mode}, light_mode={light_mode}{stealth_str} ({len(batch_sources)} sites)"
                )

            browser_config = crawler.get_browser_config(
                text_mode=text_mode, light_mode=light_mode, use_stealth=use_stealth
            )

            async with AsyncWebCrawler(config=browser_config) as web_crawler:
                # Worker pool pattern: maintain N concurrent crawlers at all times
                queue = asyncio.Queue()

                # Fill the queue with batch sources
                for source in batch_sources:
                    await queue.put(source)

                async def worker():
                    """Worker that continuously pulls from queue until empty."""
                    results = []
                    while True:
                        try:
                            source = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        conn = db.create_connection()
                        if not conn:
                            queue.task_done()
                            continue
                        cur = conn.cursor()
                        try:
                            result_id = await crawler.crawl_source(
                                web_crawler, source, cur, conn, crawl_job_id
                            )
                            if result_id:
                                results.append((result_id, source))
                        except Exception as e:
                            print(f"    - Error crawling {source['name']}: {e}")
                        finally:
                            cur.close()
                            conn.close()
                            queue.task_done()
                    return results

                # Start N workers and wait for all to complete
                worker_results = await asyncio.gather(
                    *[worker() for _ in range(num_workers)]
                )

                # Flatten results from all workers
                for results in worker_results:
                    crawl_results.extend(results)

        total_crawled = len(crawl_results) + len(extracted_results)
        print(
            f"\nCrawled {total_crawled} source(s) ({len(extracted_results)} pre-extracted via JSON API)\n"
        )

        # STEP 3: Extract events using Gemini AI
        print(f"{'=' * 60}")
        print("STEP 3: Extracting Events with Gemini AI")
        print(f"{'=' * 60}")

        # Build list of all items to extract
        extraction_queue = []

        # Add incomplete 'crawled' results from previous runs
        for r in incomplete_crawled:
            extraction_queue.append(
                {
                    "crawl_result_id": r["crawl_result_id"],
                    "name": r["name"],
                    "notes": r.get("notes", ""),
                    "started_at": r.get("started_at"),
                    "source": "incomplete",
                }
            )

        # Add newly crawled results
        for crawl_result_id, source in crawl_results:
            extraction_queue.append(
                {
                    "crawl_result_id": crawl_result_id,
                    "name": source["name"],
                    "notes": source.get("notes", ""),
                    "started_at": None,
                    "source": "new",
                    "source_data": source,
                    "use_vision": source.get("process_images") == 1,
                    "base_url": source.get("base_url", ""),
                    "max_batches": source.get("max_batches"),
                }
            )

        job_tracker = TokenTracker()

        if extraction_queue:
            print(
                f"\n  Extracting events from {len(extraction_queue)} source(s) with {num_workers} workers..."
            )

            # Worker pool pattern: maintain N concurrent extractors at all times
            extract_q = asyncio.Queue()
            for item in extraction_queue:
                await extract_q.put(item)

            async def extract_worker():
                """Worker that continuously pulls from queue until empty."""
                results = []
                worker_tracker = TokenTracker()
                while True:
                    try:
                        item = extract_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    # Each worker gets its own connection to see latest committed data
                    conn = db.create_connection()
                    if not conn:
                        extract_q.task_done()
                        continue
                    cur = conn.cursor()
                    try:
                        success, tracker = await extractor.extract_events(
                            cur,
                            conn,
                            item["crawl_result_id"],
                            item["name"],
                            item["notes"],
                            use_vision=item.get("use_vision", False),
                            base_url=item.get("base_url", ""),
                            max_batches=item.get("max_batches"),
                        )
                        worker_tracker.merge(tracker)
                        if success:
                            if item["source"] == "incomplete":
                                results.append(
                                    (
                                        item["crawl_result_id"],
                                        {
                                            "name": item["name"],
                                            "notes": item["notes"],
                                            "started_at": item["started_at"],
                                        },
                                    )
                                )
                            else:
                                results.append(
                                    (item["crawl_result_id"], item["source_data"])
                                )
                    except Exception as e:
                        print(f"    - Error extracting {item['name']}: {e}")
                    finally:
                        cur.close()
                        conn.close()
                        extract_q.task_done()
                return results, worker_tracker

            # Start N workers and wait for all to complete
            worker_results = await asyncio.gather(
                *[extract_worker() for _ in range(num_workers)]
            )

            # Flatten results from all workers and merge trackers
            for results, worker_tracker in worker_results:
                extracted_results.extend(results)
                job_tracker.merge(worker_tracker)

        print(f"\nExtracted events from {len(extracted_results)} source(s)\n")

        if USE_CELERY:
            from celery_publisher import publish_process_crawl_job

            if job_tracker.api_calls > 0:
                db.save_crawl_summary(cursor, crawl_job_id, job_tracker)
            db.complete_crawl_job(cursor, connection, crawl_job_id)
            task_id = publish_process_crawl_job(crawl_job_id)
            print(f"{'=' * 60}")
            print("CELERY BRIDGE MODE — handoff to backend")
            print(f"{'=' * 60}")
            print(f"  crawl_job_id={crawl_job_id} task_id={task_id}")
            return True

        # STEP 4: Process responses
        print(f"{'=' * 60}")
        print("STEP 4: Processing Responses")
        print(f"{'=' * 60}")

        # Refresh connection to see data committed by extract workers
        cursor.close()
        connection.close()
        connection = db.create_connection()
        if not connection:
            print("Failed to reconnect to database")
            return False
        cursor = connection.cursor()

        total_events = 0
        total_location_stats = processor.LocationStats()
        run_date_str = datetime.now().strftime("%Y%m%d")

        # First, process incomplete 'extracted' results from previous runs
        if incomplete_extracted:
            print(
                f"\n  Processing {len(incomplete_extracted)} incomplete 'extracted' result(s)..."
            )
            for r in incomplete_extracted:
                print(f"  Processing {r['name']} (from {r['started_at']})...")
                original_run_date_str = r["started_at"].strftime("%Y%m%d")
                event_count, loc_stats = processor.process_events(
                    cursor,
                    connection,
                    r["crawl_result_id"],
                    r["name"],
                    original_run_date_str,
                )
                total_events += event_count
                total_location_stats.merge(loc_stats)
                print(f"    - {event_count} events processed")

        # Then process newly extracted results
        for crawl_result_id, source in extracted_results:
            print(f"  Processing {source['name']}...")
            source_started_at = source.get("started_at")
            result_run_date_str = (
                source_started_at.strftime("%Y%m%d")
                if source_started_at
                else run_date_str
            )
            event_count, loc_stats = processor.process_events(
                cursor,
                connection,
                crawl_result_id,
                source["name"],
                result_run_date_str,
            )
            total_events += event_count
            total_location_stats.merge(loc_stats)
            print(f"    - {event_count} events processed")

        print(f"\nProcessed {total_events} total events\n")

        # Save token usage summary and mark crawl job as completed (single commit)
        if job_tracker.api_calls > 0:
            db.save_crawl_summary(cursor, crawl_job_id, job_tracker)
        db.complete_crawl_job(cursor, connection, crawl_job_id)

        # STEP 5: Merge extracted_events into final events table and archive outdated events
        print(f"{'=' * 60}")
        print("STEP 5: Merging Extracted Events and Archiving Outdated Events")
        print(f"{'=' * 60}")

        new_events, merged_events = merger.merge_extracted_events(cursor, connection)
        print(f"\nMerged events ({new_events} new, {merged_events} merged)\n")

        print(f"{'=' * 60}")
        print("PIPELINE COMPLETED SUCCESSFULLY")
        print(f"{'=' * 60}\n")

        # Show summary
        print("Summary:")
        print(f"  - Sources crawled: {len(crawl_results) + len(json_api_sources)}")
        if incomplete_crawled:
            print(f"  - Resumed extractions: {len(incomplete_crawled)}")
        if incomplete_extracted:
            print(f"  - Resumed processing: {len(incomplete_extracted)}")
        print(f"  - Events extracted: {len(extracted_results)}")
        print(f"  - Total events processed: {total_events}")

        has_location_activity = (
            resolver_locations_created > 0 or total_location_stats.created > 0
        )
        if has_location_activity:
            print(f"\n{'=' * 60}")
            print("LOCATION DISCOVERY")
            print(f"{'=' * 60}")
            if resolver_locations_created > 0:
                print(
                    f"  From JSON API sources: {resolver_locations_created} (with coordinates)"
                )
            if total_location_stats.created > 0:
                print(total_location_stats.summary())

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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    source_ids = None
    if args.ids:
        source_ids = [int(id.strip()) for id in args.ids.split(",")]

    success = asyncio.run(run_pipeline(source_ids, args.limit))
    sys.exit(0 if success else 1)
