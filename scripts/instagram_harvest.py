"""
Instagram harvest script for FomoCCS.

Scrapes Instagram profiles or individual posts using Playwright.
Dumps structured JSON to stdout for downstream event parsing.

Usage:
  python scripts/instagram_harvest.py --username elgallocinefilo --max-posts 20
  python scripts/instagram_harvest.py --post https://www.instagram.com/p/DKrm6iVR2vv/
  python scripts/instagram_harvest.py --post DKrm6iVR2vv

Requires:
  - Playwright installed: pip install playwright && playwright install chromium
  - Cookies file (Netscape format) for authentication.
    Export via a browser extension (e.g. "Export Cookies" for Chrome).

This script uses the shared Instagram scraping module in pipeline/instagram.py
for profile harvesting. Single-post scraping and CLI output formatting
remain here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from pipeline.instagram import (
    _dismiss_popups,
    extract_shortcode,
    harvest_profile,
    load_cookies_netscape,
)

DEFAULT_COOKIES_PATH = Path.home() / ".config" / "fomoccs" / "instagram_cookies.txt"
NAVIGATION_TIMEOUT = 30_000  # ms


async def scrape_post_from_page(page, post_url: str) -> dict[str, Any] | None:
    """Navigate to a single post URL and extract all data."""
    try:
        await page.goto(
            post_url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT
        )
    except Exception:
        return None

    await asyncio.sleep(2)
    await _dismiss_popups(page)

    shortcode = extract_shortcode(post_url)

    # Check for error states
    try:
        unavailable = page.locator(
            'text="Sorry, this page isn\'t available."'
        ).or_(page.locator('text="This page isn\'t available."'))
        if await unavailable.count() > 0:
            return {"url": post_url, "shortcode": shortcode, "error": "unavailable"}
    except Exception:
        pass

    # Caption
    caption: str | None = None
    caption_selectors = [
        "h1",
        '[data-testid="post-comment-root"] span',
        'div._a9zs span[dir="auto"]',
        'div[style*="word-break"] span[dir="auto"]',
        "article h1",
        'article span[dir="auto"]',
    ]
    for sel in caption_selectors:
        try:
            el = page.locator(sel)
            if await el.count() > 0:
                text = await el.first.text_content()
                if text and len(text.strip()) > 2:
                    is_noise = text.strip().isdigit() or text.strip().startswith(
                        "Like"
                    )
                    if not is_noise:
                        caption = text.strip()
                        break
        except Exception:
            continue

    if caption:
        try:
            more = page.locator('button:has-text("more")').or_(
                page.locator('span:has-text("more")')
            )
            if await more.count() > 0:
                await more.first.click(timeout=3000)
                await asyncio.sleep(0.5)
                el = page.locator("h1")
                if await el.count() > 0:
                    expanded = await el.first.text_content()
                    if expanded and len(expanded.strip()) > len(caption):
                        caption = expanded.strip()
        except Exception:
            pass

    # Date
    posted_at: str | None = None
    try:
        time_el = page.locator("time")
        if await time_el.count() > 0:
            posted_at = await time_el.first.get_attribute("datetime")
    except Exception:
        pass

    # Location tag
    loc_name: str | None = None
    loc_url: str | None = None
    try:
        loc_link = page.locator(
            'a[href*="/explore/locations/"], a[href*="/locations/"]'
        )
        if await loc_link.count() > 0:
            loc_name = await loc_link.first.text_content()
            loc_url = await loc_link.first.get_attribute("href")
    except Exception:
        pass

    # Media type / carousel
    media_type: str = "photo"
    media_count: int = 1
    is_video: bool = False

    try:
        video = page.locator("video")
        if await video.count() > 0:
            is_video = True
            media_type = "video"
    except Exception:
        pass

    try:
        carousel_indicators = page.locator('[aria-label*="carousel"]').or_(
            page.locator('button[aria-label="Next"]')
        )
        if await carousel_indicators.count() > 0:
            media_type = "carousel"
            try:
                indicator = page.locator(
                    'div[style*="translate"] button'
                ).or_(page.locator('li[class*="_"]'))
                c = await indicator.count()
                if c > 1:
                    media_count = c
            except Exception:
                pass
    except Exception:
        pass

    # Like / comment counts
    like_count: int | None = None
    comment_count: int | None = None

    try:
        like_spans = page.locator("section span")
        for i in range(await like_spans.count()):
            t = await like_spans.nth(i).text_content()
            if t and re.search(r"\d[\d,.]*\s*(?:likes?|Likes?)", t):
                m = re.search(r"(\d[\d,.]*)", t)
                if m:
                    like_count = int(m.group(1).replace(",", "").replace(".", ""))
                break
    except Exception:
        pass

    try:
        comment_spans = page.locator("span:has-text('comments')")
        if await comment_spans.count() > 0:
            t = await comment_spans.first.text_content()
            m = re.search(r"(\d[\d,.]*)", t) if t else None
            if m:
                comment_count = int(m.group(1).replace(",", "").replace(".", ""))
    except Exception:
        pass

    return {
        "url": post_url,
        "shortcode": shortcode,
        "caption": caption,
        "posted_at": posted_at,
        "location_name": loc_name,
        "location_url": loc_url,
        "media_type": media_type,
        "media_count": media_count,
        "is_video": is_video,
        "like_count": like_count,
        "comment_count": comment_count,
    }


async def harvest_single_post(post_url_or_shortcode: str) -> dict[str, Any]:
    """Harvest a single post (used by --post CLI flag)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(
            "https://www.instagram.com/", wait_until="domcontentloaded"
        )
        await asyncio.sleep(2)
        await _dismiss_popups(page)

        post_url = (
            f"https://www.instagram.com/p/{extract_shortcode(post_url_or_shortcode)}/"
        )
        post = await scrape_post_from_page(page, post_url)
        await browser.close()
        return {"type": "post", "post": post or {"error": "could_not_scrape"}}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest Instagram posts for FomoCCS event discovery."
    )
    parser.add_argument(
        "--username", "-u", help="Instagram username (e.g. elgallocinefilo)"
    )
    parser.add_argument(
        "--post", "-p", help="Single Instagram post URL or shortcode"
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=20,
        help="Maximum posts to harvest from a profile (default: 20)",
    )
    parser.add_argument(
        "--cookies",
        default=str(DEFAULT_COOKIES_PATH),
        help=f"Path to Netscape cookies file (default: {DEFAULT_COOKIES_PATH})",
    )
    parser.add_argument(
        "--output", "-o", help="Save JSON to file instead of stdout"
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON output"
    )

    args = parser.parse_args()

    if not args.username and not args.post:
        parser.error("Either --username or --post is required")

    try:
        if args.post:
            result = asyncio.run(harvest_single_post(args.post))
        else:
            profile_info, posts = asyncio.run(
                harvest_profile(
                    username=args.username,
                    max_posts=args.max_posts,
                    cookies_path=args.cookies
                    if Path(args.cookies).exists()
                    else None,
                )
            )
            result = {
                "harvested_at": datetime.now(UTC).isoformat(),
                "script_version": "1.0.0",
                "type": "profile",
                "profile": profile_info,
                "posts": posts,
            }
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    indent = 2 if args.pretty else None
    json_text = json.dumps(result, ensure_ascii=False, indent=indent, default=str)

    if args.output:
        Path(args.output).write_text(json_text, encoding="utf-8")
        print(f"Saved to {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json_text)


if __name__ == "__main__":
    main()
