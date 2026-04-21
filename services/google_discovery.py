"""
Google Dork Discovery - Find YouTube channels mentioning emails via Google Search.
Uses ScraperAPI proxy for reliable Google access.
"""
import re
import requests
from urllib.parse import urlparse, parse_qs, quote_plus
from bs4 import BeautifulSoup
from services.utils.extraction import extract_emails_from_text
from core.config import (
    SCRAPER_API_KEY,
    GOOGLE_DISCOVERY_ENABLED,
    GOOGLE_DISCOVERY_MAX_PAGES,
    GOOGLE_DISCOVERY_QUERIES,
)


def _build_google_url(query: str, start: int = 0) -> str:
    """Build a Google Search URL for the given query."""
    encoded = quote_plus(query)
    return f"https://www.google.com/search?q={encoded}&start={start}&num=10"

def _scraper_api_url(target_url: str, region: str = "US") -> str:
    """Wrap a URL with ScraperAPI proxy and advanced anti-bot flags."""
    # Mapping region to ScraperAPI country codes
    country_map = {"US": "us", "UK": "gb", "GB": "gb", "Both": "us"}
    country_code = country_map.get(region, "us")
    
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_API_KEY}"
        f"&url={quote_plus(target_url)}"
        f"&render=true"
        f"&antibot=true"
        f"&premium=true"
        f"&country_code={country_code}"
    )


def _extract_youtube_ids_from_results(html: str) -> list[dict]:
    """
    Parse Google search results HTML and extract YouTube channel info + emails.
    Returns a list of dicts with keys: channelUrl, channelId, snippet, emails
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_channels = set()

    # Pattern to extract YouTube channel/user URLs from Google result links
    yt_channel_patterns = [
        re.compile(r"youtube\.com/channel/([\w-]+)"),
        re.compile(r"youtube\.com/@([\w.-]+)"),
        re.compile(r"youtube\.com/c/([\w.-]+)"),
        re.compile(r"youtube\.com/user/([\w.-]+)"),
    ]

    # Find all links in the search results
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")

        # Google wraps links in redirects — extract the actual URL
        actual_url = href
        if "/url?q=" in href:
            try:
                actual_url = parse_qs(urlparse(href).query).get("q", [""])[0]
            except Exception:
                continue

        if "youtube.com" not in actual_url:
            continue

        # Try to extract channel identifier
        channel_id = None
        channel_url = None
        for pattern in yt_channel_patterns:
            match = pattern.search(actual_url)
            if match:
                identifier = match.group(1)
                channel_id = identifier
                channel_url = actual_url.split("?")[0]  # Clean URL
                break

        if not channel_id or channel_id in seen_channels:
            continue

        seen_channels.add(channel_id)

        # Try to extract snippet text near this link (Google shows description text)
        snippet = ""
        parent = a_tag.find_parent()
        if parent:
            # Walk up a few levels to find the result container
            for _ in range(5):
                if parent.parent:
                    parent = parent.parent
                else:
                    break
            snippet = parent.get_text(separator=" ", strip=True)

        # Extract emails from the snippet
        emails = extract_emails_from_text(snippet)

        results.append({
            "channelUrl": channel_url,
            "channelId": channel_id,
            "snippet": snippet[:500],
            "emails": emails,
        })

    return results


def discover_channels_via_google(
    keyword: str,
    region: str = "US",
    on_log=None,
) -> list[dict]:
    """
    Use Google dorks to find YouTube channels that publicly mention email addresses.
    
    Returns a list of dicts: { channelUrl, channelId, emails }
    """
    if not GOOGLE_DISCOVERY_ENABLED:
        return []

    if not SCRAPER_API_KEY:
        if on_log:
            on_log("[google] Skipping Google discovery: No ScraperAPI key configured.")
        return []

    all_results = []
    seen_ids = set()

    # Build queries from configured templates
    queries = []
    for template in GOOGLE_DISCOVERY_QUERIES:
        query = template.replace("{keyword}", keyword)
        if "{region}" in query:
            query = query.replace("{region}", region)
        queries.append(query)

    for query in queries:
        if on_log:
            on_log(f"[google] Searching: {query}")

        for page in range(GOOGLE_DISCOVERY_MAX_PAGES):
            start = page * 10
            google_url = _build_google_url(query, start)
            api_url = _scraper_api_url(google_url, region=region)

            try:
                resp = requests.get(api_url, timeout=60)
                if resp.status_code != 200:
                    if on_log:
                        on_log(f"[google] Got HTTP {resp.status_code} on page {page + 1}")
                    break

                results = _extract_youtube_ids_from_results(resp.text)

                if not results:
                    if on_log:
                        on_log(f"[google] No more results on page {page + 1}")
                    break

                new_count = 0
                for r in results:
                    if r["channelId"] not in seen_ids:
                        seen_ids.add(r["channelId"])
                        all_results.append(r)
                        new_count += 1

                if on_log:
                    on_log(
                        f"[google] Page {page + 1}: Found {len(results)} channels, "
                        f"{new_count} new. Emails in snippets: "
                        f"{sum(1 for r in results if r['emails'])}"
                    )

            except Exception as e:
                if on_log:
                    on_log(f"[google] Error on page {page + 1}: {str(e)[:80]}")
                break

    return all_results


def dork_specific_channel(channel_name_or_handle, on_log=None):
    """
    Tier 4/5: Search Google specifically for this channel's email.
    Uses ScraperAPI credits.
    Now includes social media dorks for Instagram, Twitter, and LinkedIn bio inspection.
    """
    if not SCRAPER_API_KEY:
        return []

    # Prepare search queries
    # If it starts with @, it's a handle, otherwise a name
    clean_name = channel_name_or_handle.lstrip("@")
    
    queries = [
        f'site:youtube.com "{channel_name_or_handle}" "email"',
        f'"{channel_name_or_handle}" "business inquiries" email',
        f'"{channel_name_or_handle}" contact email',
        f'site:instagram.com "{clean_name}" email',
        f'site:twitter.com "{clean_name}" email'
    ]
    
    found_emails = []
    import time
    import random
    
    if on_log: on_log(f"  [dork] Searching for {channel_name_or_handle} via {len(queries)} variations...")

    for query in queries:
        try:
            # Rotate session for each dork query too
            import uuid
            dork_session = str(uuid.uuid4())[:12]
            
            payload = {
                'api_key': SCRAPER_API_KEY,
                'url': f'https://www.google.com/search?q={quote_plus(query)}',
                'render': 'false',
                'session_id': dork_session
            }
            resp = requests.get('https://api.scraperapi.com/', params=payload, timeout=60)
            if resp.status_code == 200:
                emails = extract_emails_from_text(resp.text)
                if emails:
                    for e in emails:
                        if e not in found_emails:
                            found_emails.append(e)
                    if on_log: on_log(f"    [dork] Found {len(emails)} emails for query detail: {query[:30]}...")
            
            # If we found emails, we can stop early to save credits
            if found_emails:
                break
                
            # Short sleep between dork queries to avoid being flagged by proxy
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            if on_log: on_log(f"    [dork] Query failed: {query[:30]}... ({str(e)[:50]})")
            
    return found_emails
