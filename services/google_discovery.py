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


def _scraper_api_url(target_url: str) -> str:
    """Route a URL through ScraperAPI."""
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_API_KEY}"
        f"&url={quote_plus(target_url)}"
        f"&render=false"
        f"&country_code=us"
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
            api_url = _scraper_api_url(google_url)

            try:
                resp = requests.get(api_url, timeout=30)
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

    if on_log:
        on_log(
            f"[google] Discovery complete. Total channels found: {len(all_results)}. "
            f"Channels with emails: {sum(1 for r in all_results if r['emails'])}"
        )

    return all_results
