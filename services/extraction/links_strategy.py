from collections import deque
from urllib.parse import urlparse, parse_qs, urljoin
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from services.utils.network import is_safe_external_url
from services.utils.extraction import extract_emails_from_text
from core.config import (
    CHANNEL_TIMEOUT_MS,
    CHANNEL_POST_LOAD_WAIT_MS,
    EXTERNAL_TIMEOUT_MS,
    EXTERNAL_POST_LOAD_WAIT_MS,
    DEEP_SCAN_LIMIT,
    RECURSIVE_EXTERNAL_SCAN
)

# Comprehensive list of link aggregators used by creators
AGGREGATORS = [
    "linktr.ee", "bio.link", "campsite.bio", "tap.link", "lnk.bio", 
    "beacons.ai", "solo.to", "allmylinks.com", "direct.me", "linkpop.com", 
    "taplink.at", "msha.ke", "shor.by", "biolinky.co", "instabio.cc"
]

# Priority keywords for sorting and recursive crawling
CONTACT_KEYWORDS = ["contact", "about", "reach", "info", "mail", "business", "connect", "imprint", "impressum", "legal"]

async def try_extract_from_links(page, channel_url: str, on_log=None) -> str | None:
    """
    Follow external links (website, social) with deep scanning.
    Implements Level 2 recursive crawling for priority pages.
    """
    current = page.url
    if "youtube.com" not in current or "consent" in current:
        try:
            if on_log: on_log(f"Fetching links from channel page: {channel_url}")
            await page.goto(channel_url, wait_until="commit", timeout=CHANNEL_TIMEOUT_MS)
            await page.wait_for_timeout(CHANNEL_POST_LOAD_WAIT_MS)
        except PlaywrightTimeoutError:
            if on_log: on_log("  [links] Channel page navigation timed out, attempting to extract links anyway...")
        except Exception as e:
            if on_log: on_log(f"  [links] Channel page navigation failed: {str(e)}")

    # 1. Gather all initial external links
    raw_links = await page.eval_on_selector_all('a[href]', "els => els.map(el => el.href)")
    
    external_queue = deque()
    seen_urls = set()
    
    for link in raw_links:
        # Handle YouTube redirects
        if "youtube.com/redirect" in link:
            try:
                parsed = urlparse(link)
                q = parse_qs(parsed.query).get("q", [None])[0]
                if q: link = q
            except Exception: pass
                
        lower = link.lower()
        if any(x in lower for x in ["youtube.com", "google.com", "accounts.google", "policies.google"]):
            continue
            
        if link not in seen_urls and is_safe_external_url(link):
            seen_urls.add(link)
            external_queue.append((link, 1)) # (URL, Depth)

    if not external_queue:
        if on_log: on_log("  [links] No valid external links found.")
        return None

    # Sort queue by priority keywords
    sorted_targets = sorted(list(external_queue), key=lambda x: _get_url_priority(x[0]))
    external_queue = deque(sorted_targets)

    scanned_count = 0
    
    while external_queue and scanned_count < DEEP_SCAN_LIMIT:
        target_url, depth = external_queue.popleft()
        
        try:
            if on_log: on_log(f"  [links] Deep scanning (L{depth}): {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=EXTERNAL_TIMEOUT_MS)
            await page.wait_for_timeout(EXTERNAL_POST_LOAD_WAIT_MS)
            scanned_count += 1
            
            # --- Aggregator Logic ---
            is_agg = any(agg in target_url.lower() for agg in AGGREGATORS)
            
            # --- Extract Emails from Page ---
            html_content = await page.content()
            found_emails = extract_emails_from_text(html_content)
            if found_emails:
                if on_log: on_log(f"  [links] SUCCESS: Found email on {target_url}: {found_emails[0]}")
                return found_emails[0]

            # --- Level 2: Sub-path & Link Exploration ---
            if RECURSIVE_EXTERNAL_SCAN and depth < 2:
                # A. Proactive Sub-path Guessing (only for root domains)
                parsed_target = urlparse(target_url)
                if not is_agg and (parsed_target.path in ["", "/"]):
                    base = f"{parsed_target.scheme}://{parsed_target.netloc}"
                    potential_subs = [
                        "/contact", "/about", "/contact-us", "/contactus", 
                        "/contact-me", "/reach-me", "/info", "/support",
                        "/imprint", "/impressum", "/legal", "/management",
                        "/privacy", "/press", "/media", "/advertising",
                        "/business", "/partnership", "/collab", "/collaboration"
                    ]
                    for sub in potential_subs:
                        sub_url = urljoin(base, sub)
                        if sub_url not in seen_urls:
                            seen_urls.add(sub_url)
                            external_queue.appendleft((sub_url, 2)) # Prioritize guessed sub-paths

                # B. Extract links FROM the current page that look like contact pages
                page_links = await page.eval_on_selector_all('a[href]', "els => els.map(el => el.href)")
                for p_link in page_links:
                    p_link_abs = urljoin(target_url, p_link)
                    if p_link_abs in seen_urls: continue
                    
                    p_low = p_link_abs.lower()
                    # Only follow links that look related to contact/about or are on the same domain
                    is_contact_link = any(kw in p_low for kw in CONTACT_KEYWORDS)
                    is_same_domain = urlparse(p_link_abs).netloc == parsed_target.netloc
                    
                    if (is_contact_link or is_same_domain) and is_safe_external_url(p_link_abs):
                        seen_urls.add(p_link_abs)
                        if is_contact_link:
                            external_queue.appendleft((p_link_abs, 2)) # Prioritize
                        else:
                            external_queue.append((p_link_abs, 2))

        except PlaywrightTimeoutError:
            if on_log: on_log(f"  [links] Timeout reaching {target_url}.")
        except Exception as e:
            if on_log: on_log(f"  [links] Error on {target_url}: {str(e)[:100]}")
            continue

    return None

def _get_url_priority(url: str) -> int:
    """Lower is higher priority."""
    u_low = url.lower()
    # High priority: contact pages & known aggregators
    if any(agg in u_low for agg in AGGREGATORS): return 0
    if "contact" in u_low: return 1
    if "about" in u_low: return 2
    if "reach" in u_low: return 3
    if "info" in u_low: return 4
    # Medium priority: Business homepages
    if urlparse(url).path in ["", "/"]: return 10
    return 100

