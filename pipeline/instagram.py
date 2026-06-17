"""
Instagram scraping module for the event processing pipeline.

Shared by both the standalone CLI script (scripts/instagram_harvest.py)
and the pipeline crawler (pipeline/crawler.py).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any


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


def extract_shortcode(url_or_shortcode: str) -> str:
    """Extract Instagram shortcode from a post URL or return it as-is."""
    m = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?&]+)", url_or_shortcode)
    if m:
        return m.group(1)
    return url_or_shortcode.strip("/")


async def _dismiss_popups(page) -> None:
    """Dismiss common Instagram popups (notifications, cookie banners, etc.)."""
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


async def _scrape_post_via_overlay(
    page, thumbnail_index: int
) -> dict[str, Any] | None:
    """Click a post thumbnail on the profile grid to open the overlay,
    extract data, then close it."""

    thumbnails = page.locator('a[href*="/p/"], a[href*="/reel/"]')
    count = await thumbnails.count()
    if thumbnail_index >= count:
        return {"error": "thumbnail_not_found"}

    try:
        await thumbnails.nth(thumbnail_index).click(timeout=5000)
    except Exception:
        return {"error": "click_failed"}

    await asyncio.sleep(2)

    result: dict[str, Any] = {
        "url": None,
        "shortcode": None,
        "caption": None,
        "posted_at": None,
        "location_name": None,
        "media_type": "photo",
        "media_count": 1,
        "is_video": False,
        "like_count": None,
        "comment_count": None,
    }

    # Shortcode from overlay URL
    try:
        url_el = page.locator('div[role="dialog"] a[href*="/p/"]')
        if await url_el.count() == 0:
            url_el = page.locator('article a[href*="/p/"]')
        if await url_el.count() > 0:
            href = await url_el.first.get_attribute("href")
            if href:
                m = re.search(r"(?:instagram\.com)?/p/([^/?&]+)", href)
                if m:
                    result["shortcode"] = m.group(1)
                else:
                    result["shortcode"] = extract_shortcode(href)
                result["url"] = f"https://www.instagram.com/p/{result['shortcode']}/"
    except Exception:
        pass

    # Caption
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
                    if (
                        not t.strip().isdigit()
                        and not t.strip().startswith("Like")
                        and not t.strip().startswith("View")
                        and "comments" not in t.strip().lower()
                    ):
                        result["caption"] = t.strip()
                        break
            if result["caption"]:
                break
        except Exception:
            continue

    # Expand "more" link
    if result["caption"]:
        try:
            more = (
                page.locator('span:has-text("more")')
                .or_(page.locator('button:has-text("more")'))
                .or_(page.locator('span:has-text("... more")'))
            )
            if await more.count() > 0:
                await more.first.click(timeout=3000)
                await asyncio.sleep(0.5)
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
            page.locator("article time")
        )
        if await time_el.count() > 0:
            result["posted_at"] = await time_el.first.get_attribute("datetime")
    except Exception:
        pass

    # Location tag
    try:
        loc_link = (
            page.locator(
                'div[role="dialog"] a[href*="/locations/"], '
                'div[role="dialog"] a[href*="/explore/locations/"]'
            )
            .or_(page.locator('article a[href*="/locations/"]'))
        )
        if await loc_link.count() > 0:
            result["location_name"] = await loc_link.first.text_content()
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
        nav = page.locator('div[role="dialog"] button[aria-label="Next"]')
        if await nav.count() > 0:
            result["media_type"] = "carousel"
            dots = page.locator(
                'div[role="dialog"] [role="tablist"] [role="tab"]'
            )
            dot_count = await dots.count()
            if dot_count > 1:
                result["media_count"] = dot_count
    except Exception:
        pass

    # Close overlay
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await asyncio.sleep(0.5)

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

    return (
        result
        if result.get("caption") or result.get("shortcode")
        else {"error": "no_data_extracted"}
    )


async def harvest_profile(
    username: str,
    max_posts: int = 20,
    cookies_path: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Harvest posts from an Instagram profile.

    Returns (profile_info, posts_list).

    Loads cookies for authentication if cookies_path is provided.
    """
    from playwright.async_api import async_playwright

    profile_info: dict[str, Any] = {
        "username": username,
        "full_name": None,
        "bio": None,
        "website": None,
        "post_count": None,
        "is_private": False,
        "error": None,
    }

    posts_data: list[dict[str, Any]] = []

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

        # Load cookies if available
        if cookies_path:
            path = Path(cookies_path)
            if path.exists():
                try:
                    cookies = load_cookies_netscape(str(path))
                    if cookies:
                        await context.add_cookies(cookies)
                except Exception:
                    pass

        page = await context.new_page()
        await page.goto(
            "https://www.instagram.com/", wait_until="domcontentloaded"
        )
        await asyncio.sleep(2)
        await _dismiss_popups(page)

        url = f"https://www.instagram.com/{username}/"
        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
        except Exception:
            profile_info["error"] = "profile_load_timeout"
            await browser.close()
            return profile_info, posts_data

        await asyncio.sleep(3)
        await _dismiss_popups(page)

        # Check for private / not found
        try:
            body_text = await page.text_content("body") or ""
        except Exception:
            body_text = ""

        if "This Account is Private" in body_text:
            profile_info["is_private"] = True
            profile_info["error"] = "private_account"
            await browser.close()
            return profile_info, posts_data

        if "Sorry, this page isn't available" in body_text:
            profile_info["error"] = "not_found"
            await browser.close()
            return profile_info, posts_data

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

        # External link
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
            counts = page.locator("header li").or_(
                page.locator("header span[class]")
            )
            for i in range(await counts.count()):
                t = await counts.nth(i).text_content()
                if t and re.search(r"\bposts?\b", t):
                    m = re.search(r"(\d[\d,.]*)", t)
                    if m:
                        profile_info["post_count"] = int(
                            m.group(1).replace(",", "").replace(".", "")
                        )
                    break
        except Exception:
            pass

        # Collect post links from the grid
        seen_shortcodes: set[str] = set()
        scroll_attempts = 0
        max_scrolls = max(10, max_posts // 3)

        while len(seen_shortcodes) < max_posts and scroll_attempts < max_scrolls:
            links = page.locator('a[href*="/p/"], a[href*="/reel/"]')
            for i in range(await links.count()):
                href = await links.nth(i).get_attribute("href")
                if not href:
                    continue
                sc = extract_shortcode(href)
                if sc in seen_shortcodes:
                    continue
                seen_shortcodes.add(sc)

            try:
                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:
                pass
            await asyncio.sleep(2.0)
            await _dismiss_popups(page)
            scroll_attempts += 1

        # Harvest each post via overlay click
        shortcodes_list = list(seen_shortcodes)[:max_posts]
        for idx in range(min(max_posts, len(shortcodes_list))):
            post = await _scrape_post_via_overlay(page, idx)
            if post and "error" not in post:
                posts_data.append(post)
            await asyncio.sleep(3.0)

        await browser.close()

    return profile_info, posts_data
