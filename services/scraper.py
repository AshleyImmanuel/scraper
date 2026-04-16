"""
Email Scraper - Extracts public emails from YouTube channel About pages
using Playwright routed through ScraperAPI proxy.
"""
import sys
import asyncio
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Windows loop policy enforcement for Playwright
if sys.platform == "win32":
    try:
        if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from core.config import (
    SCRAPER_API_KEY,
    SCRAPER_CONCURRENCY,
    SCRAPER_HEADLESS,
    FAST_CHECK_VIDEO_COUNT
)
from services.utils.extraction import extract_emails_from_text
from services.scraper_engine import extract_email_from_channel
from services.youtube import get_recent_videos
from services.extraction.lightweight_strategy import try_extract_lightweight

# Settings from Environment (with defaults)
SCRAPER_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
).strip()
SCRAPER_PROXY_SCHEME = os.getenv("SCRAPER_PROXY_SCHEME", "http").strip() or "http"
SCRAPER_PROXY_HOST = os.getenv("SCRAPER_PROXY_HOST", "proxy-server.scraperapi.com").strip() or "proxy-server.scraperapi.com"
SCRAPER_PROXY_PORT = int(os.getenv("SCRAPER_PROXY_PORT", "8001"))
SCRAPER_PROXY_USERNAME = os.getenv("SCRAPER_PROXY_USERNAME", "scraperapi").strip() or "scraperapi"


def _scraper_api_proxy_url() -> str:
    """Build the ScraperAPI proxy connection string for Playwright."""
    return (
        f"{SCRAPER_PROXY_SCHEME}://{SCRAPER_PROXY_USERNAME}:{SCRAPER_API_KEY}"
        f"@{SCRAPER_PROXY_HOST}:{SCRAPER_PROXY_PORT}"
    )


async def extract_emails(results: list[dict], on_progress=None, on_log=None) -> list[dict]:
    """
    Main extraction pipeline:
    1. YT Descriptions (Last 20 videos via API)
    2. Lightweight External Links (Requests/BS4)
    3. Aggregators & Homepages (Requests/BS4)
    4. YT About Modal (Playwright fallback)
    """
    total = len(results)
    pending_rows: list[tuple[int, dict]] = []

    if on_log: on_log(f"Starting Multi-Source extraction for {total} candidates...")
    
    for idx, row in enumerate(results):
        channel_name = row["channelName"]
        channel_id = row["channelId"]
        
        # --- TIER 1: YouTube API (Search Snippets + Full Desc) ---
        full_context = f"{row.get('channelDescription','')} {row.get('videoDescription','')}"
        fast_check = extract_emails_from_text(full_context)
        if fast_check:
            row["EMAIL"] = fast_check[0]
            if on_progress: on_progress(idx + 1, total, channel_name, fast_check[0])
            continue

        # --- TIER 2: YouTube API (Recent Video Descriptions) ---
        if FAST_CHECK_VIDEO_COUNT > 0:
            recent_vids = await asyncio.to_thread(get_recent_videos, channel_id, FAST_CHECK_VIDEO_COUNT)
            all_vids_text = ""
            for vid in recent_vids:
                all_vids_text += f" {vid['title']} {vid['description']}"
            
            v_emails = extract_emails_from_text(all_vids_text)
            if v_emails:
                row["EMAIL"] = v_emails[0]
                if on_log: on_log(f"  [api] SUCCESS: Found in video descriptions for {channel_name}")
                if on_progress: on_progress(idx + 1, total, channel_name, v_emails[0])
                continue

        # --- TIER 3: Lightweight External Links (Requests/BS4) ---
        # 1. Gather links from the channel page (we need to hit once to get the links)
        # Note: We still use Playwright for the initial link gathering to be 100% accurate with YT's JS redirects
        # BUT we prioritize this over the "View Email" button.
        pending_rows.append((idx, row))

    if not pending_rows:
        return results

    proxy_settings = {
        "server": f"{SCRAPER_PROXY_SCHEME}://{SCRAPER_PROXY_HOST}:{SCRAPER_PROXY_PORT}",
        "username": SCRAPER_PROXY_USERNAME,
        "password": SCRAPER_API_KEY
    }

    async with async_playwright() as pw:
        # Pre-check: Is ScraperAPI alive?
        is_sapi_dead = False
        try:
            browser_test = await pw.chromium.launch(headless=True, proxy=proxy_settings)
            test_context = await browser_test.new_context()
            test_page = await test_context.new_page()
            resp = await test_page.goto("http://httpbin.org/ip", timeout=10000)
            if resp.status == 403: is_sapi_dead = True
            await browser_test.close()
        except Exception: pass

        browser = await pw.chromium.launch(headless=SCRAPER_HEADLESS, proxy=proxy_settings)
        context = await browser.new_context(
            user_agent=SCRAPER_USER_AGENT,
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True
        )
        
        await context.route("**/*", lambda route: route.abort() if route.request.resource_type in {"image", "media", "font"} else route.continue_())
        sem = asyncio.Semaphore(SCRAPER_CONCURRENCY)

        async def process_channel(original_idx, row):
            async with sem:
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)
                try:
                    # Step 1: Visit channel and get links
                    if on_log: on_log(f"Analyzing {row['channelName']}...")
                    await page.goto(row["channelUrl"], wait_until="commit", timeout=30000)
                    await page.wait_for_timeout(2000)
                    
                    # Gather links
                    raw_links = await page.eval_on_selector_all('a[href]', "els => els.map(el => el.href)")
                    links = []
                    for l in raw_links:
                        if "youtube.com/redirect" in l:
                            from urllib.parse import urlparse, parse_qs
                            try: links.append(parse_qs(urlparse(l).query).get("q", [""])[0])
                            except Exception: links.append(l)
                        else: links.append(l)

                    # --- TIER 3: Lightweight Scan of Links ---
                    found_email = None
                    for l in links:
                        if "youtube.com" in l or "google.com" in l: continue
                        # Use ThreadPool to running the synchronous lightweight scraper
                        found_email = await asyncio.to_thread(try_extract_lightweight, l, on_log=on_log)
                        if found_email: break
                    
                    if found_email:
                        row["EMAIL"] = found_email
                        if on_progress: on_progress(original_idx + 1, total, row["channelName"], found_email)
                        return

                    # --- TIER 4: About Modal Fallback (Only if ScraperAPI is up) ---
                    if not is_sapi_dead:
                        email = await extract_email_from_channel(page, row["channelUrl"], on_log)
                        row["EMAIL"] = email or "nil"
                    else:
                        row["EMAIL"] = "nil"
                    
                    if on_progress: on_progress(original_idx + 1, total, row["channelName"], row["EMAIL"] if row["EMAIL"] != "nil" else None)
                except Exception as e:
                    if on_log: on_log(f"  [error] {row['channelName']}: {str(e)[:50]}")
                    row["EMAIL"] = "nil"
                finally:
                    await page.close()

        tasks = [process_channel(idx, row) for idx, row in pending_rows]
        await asyncio.gather(*tasks)

        await context.close()
        await browser.close()

    return results
