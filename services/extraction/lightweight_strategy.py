import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from services.utils.extraction import extract_emails_from_text
from services.utils.network import is_safe_external_url
from core.config import (
    EXTERNAL_TIMEOUT_MS,
    SCRAPER_LINK_AGGREGATORS as AGGREGATORS,
    SCRAPER_CONTACT_SUBPATHS as CONTACT_SUBPATHS
)

def try_extract_lightweight(url: str, on_log=None, depth=1) -> str | None:
    """
    Attempt to extract an email using simple HTTP requests (no browser).
    Follows a shallow recursive strategy for contact pages.
    """
    if not is_safe_external_url(url):
        return None

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        if on_log: on_log(f"  [lightweight] Fetching: {url}")
        response = requests.get(url, headers=headers, timeout=EXTERNAL_TIMEOUT_MS / 1000, verify=False)
        response.raise_for_status()
        
        html = response.text
        emails = extract_emails_from_text(html)
        if emails:
            if on_log: on_log(f"  [lightweight] SUCCESS: Found on {url}")
            return emails[0]

        # Level 2: If we didn't find an email on the root, check contact sub-paths
        if depth == 1:
            parsed = urlparse(url)
            is_agg = any(agg in url.lower() for agg in AGGREGATORS)
            
            # 1. Proactive sub-path guessing (not for aggregators)
            if not is_agg and parsed.path in ["", "/"]:
                base = f"{parsed.scheme}://{parsed.netloc}"
                for sub in CONTACT_SUBPATHS:
                    sub_url = urljoin(base, sub)
                    email = try_extract_lightweight(sub_url, on_log, depth=2)
                    if email: return email

            # 2. Extract links from current page that look like contact pages
            if not is_agg:
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    abs_url = urljoin(url, href)
                    # Only follow if it looks like a contact link and is on the same domain
                    if any(kw in href.lower() for kw in ["contact", "about", "info", "mail"]):
                        if urlparse(abs_url).netloc == parsed.netloc:
                            email = try_extract_lightweight(abs_url, on_log, depth=2)
                            if email: return email

    except Exception as e:
        if on_log: 
            err_msg = str(e)[:50]
            on_log(f"  [lightweight] Skip {url}: {err_msg}")
            
    return None
