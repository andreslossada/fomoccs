"""
Instagram harvest script for FomoCCS.

Scrapes Instagram profiles or individual posts using Playwright.
Dumps structured JSON to stdout for downstream event parsing by a human/LLM.

Usage:
  python scripts/instagram_harvest.py --username elgallocinefilo --max-posts 20
  python scripts/instagram_harvest.py --post https://www.instagram.com/p/DKrm6iVR2vv/
  python scripts/instagram_harvest.py --post DKrm6iVR2vv

Requires:
  - Playwright installed: pip install playwright && playwright install chromium
  - Cookies file (Netscape format) for authentication.
    Export via a browser extension (e.g. "Export Cookies" for Chrome).
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

DEFAULT_COOKIES_PATH = Path.home() / ".config" / "fomoccs" / "instagram_cookies.txt"
SCROLL_PAUSE = 2.0  # seconds between scrolls
POST_DELAY = 3.0  # seconds between posts
NAVIGATION_TIMEOUT = 30_000  # ms


def load_cookies_netscape(path: str) -> list[dict[str, Any]]:
    """Load cookies from a Netscape-format file (exported by browser extensions)."""
    cookies: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies.append(
                    {
                        "name": parts[5],
                        "value": parts[6],
                        "domain": parts[0],
                        "path": parts[2],
                        "expires": int(parts[4]) if parts[4] != "0" else -1,
                        "httpOnly": False,
                        "secure": parts[3] == "TRUE",
                    }
                )
    return cookies


async def dismiss_popups(page) -> None:
    """Dismiss common Instagram popups (notifications, cookie banners, 'Save login')."""
    dismiss_texts = ["Not Now", "Not now", "Cancel", "Maybe Later"]
    dismiss_aria = ['svg[aria-label="Close"]', '[aria-label="Close"]']

    for text in dismiss_texts:
        try:
            btn = page.locator(f'button:has-text("{text}")')
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                await asyncio.sleep(0.8)
        except Exception:
            pass

    for selector in dismiss_aria:
        try:
            el = page.locator(selector)
            if await el.count() > 0:
                await el.first.click(timeout=3000)
                await asyncio.sleep(0.8)
        except Exception:
            pass

    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


def extract_shortcode(url_or_shortcode: str) -> str:
    """Extract Instagram shortcode from a post URL or return it as-is."""
    m = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?&]+)", url_or_shortcode)
    if m:
        return m.group(1)
    return url_or_shortcode.strip("/")


async def scrape_post_from_page(page, post_url: str) -> dict[str, Any] | None:
    """Navigate to a single post URL and extract all data."""
    try:
        await page.goto(post_url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT)
    except Exception:
        # Some posts may be unavailable; return None gracefully
        return None

    await asyncio.sleep(2)
    await dismiss_popups(page)

    shortcode = extract_shortcode(post_url)

    # ── check for error states ──────────────────────────────────
    try:
        unavailable = page.locator(
            'text="Sorry, this page isn\'t available."'
        ).or_(page.locator('text="This page isn\'t available."'))
        if await unavailable.count() > 0:
            return {
                "url": post_url,
                "shortcode": shortcode,
                "error": "unavailable",
            }
    except Exception:
        pass

    # ── caption ─────────────────────────────────────────────────
    caption: str | None = None
    caption_selectors = [
        'h1',
        '[data-testid="post-comment-root"] span',
        'div._a9zs span[dir="auto"]',
        'div[style*="word-break"] span[dir="auto"]',
        'article h1',
        'article span[dir="auto"]',
    ]
    for sel in caption_selectors:
        try:
            el = page.locator(sel)
            if await el.count() > 0:
                text = await el.first.text_content()
                if text and len(text.strip()) > 2:
                    # Like + comment counts leak into some selectors; filter noise
                    is_noise = text.strip().isdigit() or text.strip().startswith("Like")
                    if not is_noise:
                        caption = text.strip()
                        break
        except Exception:
            continue

    # Try clicking "more" to expand truncated captions
    if caption:
        try:
            more = page.locator('button:has-text("more")').or_(
                page.locator('span:has-text("more")')
            )
            if await more.count() > 0:
                await more.first.click(timeout=3000)
                await asyncio.sleep(0.5)
                # Re-extract caption
                el = page.locator('h1')
                if await el.count() > 0:
                    expanded = await el.first.text_content()
                    if expanded and len(expanded.strip()) > len(caption):
                        caption = expanded.strip()
        except Exception:
            pass

    # ── date ────────────────────────────────────────────────────
    posted_at: str | None = None
    try:
        time_el = page.locator("time")
        if await time_el.count() > 0:
            posted_at = await time_el.first.get_attribute("datetime")
    except Exception:
        pass

    # ── location tag ────────────────────────────────────────────
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

    # ── media type / carousel detection ─────────────────────────
    media_type: str = "photo"
    media_count: int = 1
    is_video: bool = False

    try:
        video = page.locator('video')
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

    # ── like / comment counts ───────────────────────────────────
    like_count: int | None = None
    comment_count: int | None = None

    try:
        # Likes usually in first section after the page loads
        like_spans = page.locator('section span')
        for i in range(await like_spans.count()):
            t = await like_spans.nth(i).text_content()
            if t and re.search(r'\d[\d,.]*\s*(?:likes?|Likes?)', t):
                m = re.search(r'(\d[\d,.]*)', t)
                if m:
                    like_count = int(m.group(1).replace(",", "").replace(".", ""))
                break
    except Exception:
        pass

    try:
        # Comments: look for "View all N comments"
        comment_spans = page.locator("span:has-text('comments')")
        if await comment_spans.count() > 0:
            t = await comment_spans.first.text_content()
            m = re.search(r'(\d[\d,.]*)', t) if t else None
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


async def scrape_profile(
    page, username: str, max_posts: int, cookies_file: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load a profile page and harvest posts from the overlay modal.

    Returns (profile_info, list_of_posts).
    """
    url = f"https://www.instagram.com/{username}/"
    await page.goto(url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT)
    await asyncio.sleep(3)
    await dismiss_popups(page)

    # ── profile info ────────────────────────────────────────────
    profile_info: dict[str, Any] = {
        "username": username,
        "full_name": None,
        "bio": None,
        "website": None,
        "post_count": None,
        "is_private": False,
        "is_verified": False,
    }

    # Check for private account / not found
    body_text = ""
    try:
        body_text = await page.text_content("body") or ""
    except Exception:
        pass

    if "This Account is Private" in body_text:
        profile_info["is_private"] = True
    if "Sorry, this page isn't available" in body_text:
        profile_info["error"] = "not_found"

    # Full name
    try:
        name_el = page.locator("header h2").or_(page.locator("header h1"))
        if await name_el.count() > 0:
            profile_info["full_name"] = await name_el.first.text_content()
    except Exception:
        pass

    # Bio
    try:
        bio_el = page.locator("header div[dir='auto']").or_(
            page.locator("header span[dir='auto']")
        )
        for i in range(min(5, await bio_el.count())):
            t = await bio_el.nth(i).text_content()
            if t and len(t) > 5 and "@" not in t:
                profile_info["bio"] = t.strip()
                break
    except Exception:
        pass

    # External link in bio
    try:
        ext_link = page.locator('header a[href*="://"]').or_(
            page.locator('a[href*="linktr.ee"]')
        )
        if await ext_link.count() > 0:
            profile_info["website"] = await ext_link.first.get_attribute("href")
    except Exception:
        pass

    # Post count
    try:
        counts = page.locator("header li").or_(page.locator("header span[class]"))
        for i in range(await counts.count()):
            t = await counts.nth(i).text_content()
            if t and re.search(r'\bposts?\b', t):
                m = re.search(r'(\d[\d,.]*)', t)
                if m:
                    profile_info["post_count"] = int(
                        m.group(1).replace(",", "").replace(".", "")
                    )
                break
    except Exception:
        pass

    # ── collect post links from the grid ────────────────────────
    posts_data: list[dict[str, Any]] = []
    seen_shortcodes: set[str] = set()
    scroll_attempts = 0
    max_scrolls = max(10, max_posts // 3)

    while len(posts_data) < max_posts and scroll_attempts < max_scrolls:
        # Gather visible post links
        links = page.locator('a[href*="/p/"], a[href*="/reel/"]')
        for i in range(await links.count()):
            href = await links.nth(i).get_attribute("href")
            if not href:
                continue
            sc = extract_shortcode(href)
            if sc in seen_shortcodes:
                continue
            seen_shortcodes.add(sc)

        # Scroll down to load more
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await asyncio.sleep(SCROLL_PAUSE)
        await dismiss_popups(page)
        scroll_attempts += 1

    # ── harvest each post via overlay click ─────────────────────
    print(f"  Found {len(seen_shortcodes)} post thumbnails, harvesting...",
          file=sys.stderr)

    shortcodes_list = list(seen_shortcodes)[:max_posts]
    for idx in range(min(max_posts, len(shortcodes_list))):
        post = await scrape_post_via_overlay(page, idx)
        if post and "error" not in post:
            posts_data.append(post)
            print(f"    [{idx + 1}/{min(max_posts, len(shortcodes_list))}] "
                  f"{post.get('shortcode', '?')}: "
                  f"{(post.get('caption') or '')[:80]}...",
                  file=sys.stderr)
        else:
            err = (post or {}).get("error", "no_data")
            print(f"    [{idx + 1}/{min(max_posts, len(shortcodes_list))}] "
                  f"#{idx + 1}: ({err})",
                  file=sys.stderr)

        await asyncio.sleep(POST_DELAY)

    return profile_info, posts_data


async def scrape_post_via_overlay(
    page, thumbnail_index: int
) -> dict[str, Any] | None:
    """Click a post thumbnail on the profile grid to open the overlay,
    extract data, then close it. Much stealthier than navigating to /p/{id}."""
    # Click the Nth visible thumbnail
    thumbnails = page.locator('a[href*="/p/"], a[href*="/reel/"]')
    count = await thumbnails.count()
    if thumbnail_index >= count:
        return {"error": "thumbnail_not_found"}

    try:
        await thumbnails.nth(thumbnail_index).click(timeout=5000)
    except Exception:
        return {"error": "click_failed"}

    await asyncio.sleep(2)

    # ── extract from overlay ────────────────────────────────────
    result: dict[str, Any] = {
        "url": None,
        "shortcode": None,
        "caption": None,
        "posted_at": None,
        "location_name": None,
        "location_url": None,
        "media_type": "photo",
        "media_count": 1,
        "is_video": False,
        "like_count": None,
        "comment_count": None,
    }

    # Shortcode from URL in overlay
    try:
        url_el = page.locator(
            'div[role="dialog"] a[href*="/p/"]'
        )
        if await url_el.count() == 0:
            url_el = page.locator('article a[href*="/p/"]')
        if await url_el.count() > 0:
            href = await url_el.first.get_attribute("href")
            if href:
                # Handle both full URLs and relative paths
                m = re.search(r"(?:instagram\.com)?/p/([^/?&]+)", href)
                if m:
                    sc = m.group(1)
                else:
                    sc = extract_shortcode(href)
                result["shortcode"] = sc
                result["url"] = f"https://www.instagram.com/p/{sc}/"
    except Exception:
        pass

    # Caption - the main text in the overlay sidebar
    caption_attempts = [
        'div[role="dialog"] h1',
        'article h1',
        'div[role="dialog"] li',
        'article span[dir="auto"]',
    ]
    for sel in caption_attempts:
        try:
            els = page.locator(sel)
            for i in range(min(20, await els.count())):
                t = await els.nth(i).text_content()
                if t and len(t.strip()) > 3:
                    if not t.strip().isdigit() and \
                       not t.strip().startswith("Like") and \
                       not t.strip().startswith("View") and \
                       "comments" not in t.strip().lower():
                        result["caption"] = t.strip()
                        break
            if result["caption"]:
                break
        except Exception:
            continue

    # Try clicking "more" to expand
    if result["caption"]:
        try:
            more = page.locator('span:has-text("more")').or_(
                page.locator('button:has-text("more")')
            ).or_(page.locator('span:has-text("... more")'))
            if await more.count() > 0:
                await more.first.click(timeout=3000)
                await asyncio.sleep(0.5)
                # Re-extract from h1
                h1 = page.locator('div[role="dialog"] h1')
                if await h1.count() > 0:
                    expanded = await h1.first.text_content()
                    if expanded and len(expanded.strip()) > len(result["caption"]):
                        result["caption"] = expanded.strip()
        except Exception:
            pass

    # Date
    try:
        time_el = page.locator('div[role="dialog"] time').or_(
            page.locator('article time')
        )
        if await time_el.count() > 0:
            result["posted_at"] = await time_el.first.get_attribute("datetime")
    except Exception:
        pass

    # Location tag
    try:
        loc_link = page.locator(
            'div[role="dialog"] a[href*="/locations/"], '
            'div[role="dialog"] a[href*="/explore/locations/"]'
        ).or_(page.locator('article a[href*="/locations/"]'))
        if await loc_link.count() > 0:
            result["location_name"] = await loc_link.first.text_content()
            result["location_url"] = await loc_link.first.get_attribute("href")
    except Exception:
        pass

    # Video / carousel detection
    try:
        video = page.locator('div[role="dialog"] video')
        if await video.count() > 0:
            result["is_video"] = True
            result["media_type"] = "video"
    except Exception:
        pass

    try:
        nav = page.locator(
            'div[role="dialog"] button[aria-label="Next"]'
        )
        if await nav.count() > 0:
            result["media_type"] = "carousel"
            # Count dots under the media
            dots = page.locator('div[role="dialog"] [role="tablist"] [role="tab"]')
            dot_count = await dots.count()
            if dot_count > 1:
                result["media_count"] = dot_count
    except Exception:
        pass

    # ── close overlay ───────────────────────────────────────────
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await asyncio.sleep(0.5)

    # Try clicking the close button if ESC didn't work
    close_selectors = [
        'div[role="dialog"] svg[aria-label="Close"]',
        'div[role="dialog"] [aria-label="Close"]',
        'article + div[role="presentation"] svg[aria-label="Close"]',
    ]
    for sel in close_selectors:
        try:
            btn = page.locator(sel)
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                await asyncio.sleep(0.5)
                break
        except Exception:
            continue

    return result if result.get("caption") or result.get("shortcode") else \
        {"error": "no_data_extracted"}


async def harvest(
    username: str | None,
    post_url_or_shortcode: str | None,
    max_posts: int,
    cookies_file: str,
) -> dict[str, Any]:
    """Main harvest routine."""
    if not Path(cookies_file).exists():
        print(f"ERROR: Cookies file not found: {cookies_file}", file=sys.stderr)
        print(
            "Export Instagram cookies using a browser extension "
            "('Export Cookies' for Chrome) and save as Netscape format.",
            file=sys.stderr,
        )
        sys.exit(1)

    cookies = load_cookies_netscape(cookies_file)
    if not cookies:
        print("ERROR: No cookies loaded from file.", file=sys.stderr)
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Instagram blocks headless
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
        await context.add_cookies(cookies)

        page = await context.new_page()

        # Visit Instagram first so cookies attach to the right domain
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await dismiss_popups(page)

        result: dict[str, Any] = {
            "harvested_at": datetime.now(UTC).isoformat(),
            "script_version": "1.0.0",
        }

        if post_url_or_shortcode:
            post_url = (
                f"https://www.instagram.com/p/{extract_shortcode(post_url_or_shortcode)}/"
            )
            post = await scrape_post_from_page(page, post_url)
            result["type"] = "post"
            result["post"] = post or {"error": "could_not_scrape"}
        elif username:
            profile_info, posts = await scrape_profile(
                page, username, max_posts, cookies_file
            )
            result["type"] = "profile"
            result["profile"] = profile_info
            result["posts"] = posts
        else:
            print("ERROR: Must specify --username or --post", file=sys.stderr)
            await browser.close()
            sys.exit(1)

        await browser.close()

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Harvest Instagram posts for FomoCCS event discovery."
    )
    parser.add_argument(
        "--username",
        "-u",
        help="Instagram username to scrape (e.g. elgallocinefilo)",
    )
    parser.add_argument(
        "--post",
        "-p",
        help="Single Instagram post URL or shortcode",
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
        help=f"Path to Netscape cookies file "
             f"(default: {DEFAULT_COOKIES_PATH})",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Save JSON to file instead of stdout",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )

    args = parser.parse_args()

    if not args.username and not args.post:
        parser.error("Either --username or --post is required")

    try:
        result = asyncio.run(
            harvest(
                username=args.username,
                post_url_or_shortcode=args.post,
                max_posts=args.max_posts,
                cookies_file=args.cookies,
            )
        )
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
