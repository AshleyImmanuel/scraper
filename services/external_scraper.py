
import asyncio
import requests
from urllib.parse import quote_plus
from core.config import (
    SCRAPER_API_KEY, 
    USE_LOCAL_BROWSER, 
    BROWSER_TIMEOUT_MS
)
from services.utils.extraction import extract_emails_from_text
from services.utils.browser_manager import BrowserManager, PLAYWRIGHT_AVAILABLE

def _scraper_api_url(target_url: str, render: bool = True) -> str:
    """Wrap a URL with ScraperAPI proxy. Use JS rendering for social sites."""
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_API_KEY}"
        f"&url={quote_plus(target_url)}"
        f"&render={'true' if render else 'false'}"
        f"&antibot=true"
        f"&premium=true"
    )

async def scrape_external_url(url: str, on_log=None, region="US") -> list[str]:
    """Fetch an external URL and extract emails (via local browser or ScraperAPI)."""
    
    if USE_LOCAL_BROWSER and PLAYWRIGHT_AVAILABLE:
        return await _scrape_with_local_browser(url, on_log, region=region)
    
    # Fallback to ScraperAPI
    if not SCRAPER_API_KEY:
        if on_log: on_log(f"  [external] Skipping {url}: No API key or local browser available.")
        return []

    needs_render = any(domain in url.lower() for domain in ["linktr.ee", "beacons", "instagram", "facebook", "twitter.com", "x.com"])
    api_url = _scraper_api_url(url, render=needs_render)

    if on_log: on_log(f"  [external] Scraping (ScraperAPI): {url} (render={needs_render})")

    max_retries = 2
    for attempt in range(max_retries):
        try:
            resp = await asyncio.to_thread(requests.get, api_url, timeout=60)
            if resp.status_code == 200:
                found = extract_emails_from_text(resp.text)
                if found:
                    if on_log: on_log(f"  [external] SUCCESS: Found {len(found)} email(s) on {url}")
                    return found
            elif resp.status_code in [403, 429]:
                if on_log: on_log(f"  [external] Rate limited (403/429) on {url}, retrying in 3s...")
                await asyncio.sleep(3)
                continue
            else:
                if on_log: on_log(f"  [external] FAILED: HTTP {resp.status_code} on {url}")
        except Exception as e:
            if on_log: on_log(f"  [external] ERROR on {url} (ScraperAPI): {str(e)[:50]}")
        break
    
    return []

async def _scrape_with_local_browser(url: str, on_log=None, region="US") -> list[str]:
    """Use local Playwright browser to scrape a URL."""
    for attempt in range(2):
        try:
            # We use an optimized page (no media) for fast external scraping too
            context, page = await BrowserManager.get_page(optimize=True, region=region)
            if not page: return []

            try:
                # Navigate and wait for some content or a timeout
                await page.goto(url, wait_until="networkidle", timeout=BROWSER_TIMEOUT_MS)
                
                # Slightly faster sleep
                await asyncio.sleep(1)
                
                content = await page.content()
                found = extract_emails_from_text(content)
                
                if found:
                    if on_log: on_log(f"  [external] SUCCESS: Found {len(found)} email(s) on {url}")
                    return found
            finally:
                # We close the page/context but keep the browser instance alive per user preference
                await context.close()
            
            break # No email found but page loaded fine
        except Exception as e:
            if on_log: on_log(f"  [external] Attempt {attempt+1} failed for {url}: {str(e)[:50]}")
            if attempt == 0:
                await asyncio.sleep(2)
                continue
    return []

async def scrape_multiple_urls(urls: list[str], on_log=None, region="US") -> list[str]:
    """Process a list of URLs and return the first valid email found."""
    for url in urls:
        # Filter out junk URLs before scraping
        if any(junk in url.lower() for junk in ["youtube.com", "google.com", "facebook.com/sharer"]):
            continue
            
        emails = await scrape_external_url(url, on_log, region=region)
        if emails:
            return emails
    return []
