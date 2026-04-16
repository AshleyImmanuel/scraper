import asyncio
import random
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from services.utils.extraction import extract_emails_from_text
from core.config import (
    ABOUT_TIMEOUT_MS,
    ABOUT_POST_LOAD_WAIT_MS,
    CONSENT_CLICK_TIMEOUT_MS,
    CONSENT_POST_CLICK_WAIT_MS,
    VIEW_EMAIL_CLICK_TIMEOUT_MS,
    VIEW_EMAIL_POST_CLICK_WAIT_MS,
    SCRAPER_MOUSE_JITTER,
    SCRAPER_MOBILE_FALLBACK
)

async def _human_click(page, element, on_log=None):
    """Click an element with optional mouse movement jitter."""
    try:
        if SCRAPER_MOUSE_JITTER:
            bbox = await element.bounding_box()
            if bbox:
                # Move to a random point within the element
                target_x = bbox['x'] + (bbox['width'] * random.uniform(0.2, 0.8))
                target_y = bbox['y'] + (bbox['height'] * random.uniform(0.2, 0.8))
                
                # Slower, multi-step movement
                steps = random.randint(5, 10)
                await page.mouse.move(target_x, target_y, steps=steps)
                await page.wait_for_timeout(random.randint(200, 500))
        
        await element.click(timeout=VIEW_EMAIL_CLICK_TIMEOUT_MS)
    except Exception as e:
        if on_log: on_log(f"  [jitter] Click failed: {str(e)}")
        # Fallback to direct click if jitter fails
        await element.click(timeout=VIEW_EMAIL_CLICK_TIMEOUT_MS)

async def try_extract_from_about(page, channel_url: str, on_log=None) -> str | None:
    """Navigate to a YouTube channel's page and extract the first valid email by opening the About modal."""
    try:
        if on_log: on_log(f"Visiting channel page for {channel_url}...")
        await page.goto(channel_url, wait_until="domcontentloaded", timeout=ABOUT_TIMEOUT_MS)
        await page.wait_for_timeout(ABOUT_POST_LOAD_WAIT_MS + 1000)
        
        # Ensure base content has started rendering
        try:
            await page.wait_for_selector('ytd-app', timeout=5000)
        except Exception:
            if on_log: on_log("  [scraper] ytd-app not found within timeout, continuing anyway")
            pass
    except PlaywrightTimeoutError as te:
        if on_log: on_log(f"Main page goto timed out, attempting to parse partial page: {str(te)}")
    except Exception as e:
        if on_log: on_log(f"Main page goto failed: {str(e)}")

    if "consent." in page.url.lower():
        if on_log: on_log(f"CAPTCHA/Consent wall detected for {channel_url}. Attempting to bypass...")
        try:
            btn = page.locator('button:has-text("Accept all"), button:has-text("Agree")')
            if await btn.count() > 0:
                await btn.first.click(timeout=CONSENT_CLICK_TIMEOUT_MS)
                await page.wait_for_timeout(CONSENT_POST_CLICK_WAIT_MS)
        except Exception:
            pass

    page_html = (await page.content()).lower()
    if "recaptcha" in page_html or "unusual traffic" in page_html:
        if on_log: on_log(f"WARNING: Google reCAPTCHA blocked access for {channel_url}.")

    # Strategy: Expand the modern 'About' / 'More info' dialog
    try:
        # Modern YouTube often uses tagline or specific description links to toggle the about modal
        more_selectors = [
            'ytd-channel-tagline-renderer',
            'button.ytTruncatedTextAbsoluteButton',
            'button[aria-label*="tap for more"]',
            'button[aria-label*="More about"]',
            'button[aria-label*="About"]',
            'button[aria-label*="Channel info"]',
            '.yt-description-preview-view-model-anchor',
            '.yt-core-attributed-string--link',
            '.truncated-text-wiz-content-toggle',
            '#description-container',
            '#about-container',
            'ytd-expander.ytd-channel-about-metadata-renderer button',
            '.ytd-channel-tagline-renderer__more'
        ]
        
        opened = False
        for sel in more_selectors:
            more_link = page.locator(sel)
            if await more_link.count() > 0 and await more_link.first.is_visible():
                if on_log: on_log(f"  [scraper] Clicking 'About/More info' trigger: {sel}")
                try:
                    # Move to element and click to be more human-like
                    await more_link.first.scroll_into_view_if_needed()
                    await _human_click(page, more_link.first, on_log)
                    # Wait for modal to animate
                    await page.wait_for_timeout(1500 + random.randint(200, 800))
                    opened = True
                    break
                except Exception as click_err:
                    if on_log: on_log(f"  [scraper] Failed to click {sel}: {str(click_err)}")
        
        if not opened:
            if on_log: on_log("  [scraper] No 'More info' trigger found. Checking if about info is already visible.")

        # Look for the modern about modal/dialog or the expanded description
        dialog_selectors = [
            "ytd-about-channel-view-model",
            "tp-yt-paper-dialog",
            "ytd-popup-container",
            "ytd-engagement-panel-section-list-renderer[target-id='engagement-panel-about-channel']",
            "#dialog",
            "ytd-about-channel-renderer",
            "ytd-channel-about-metadata-renderer"
        ]
        
        dialog = None
        for dsel in dialog_selectors:
            d_found = page.locator(dsel)
            if await d_found.count() > 0 and await d_found.first.is_visible():
                dialog = d_found.first
                if on_log: on_log(f"  [scraper] Found channel info container: {dsel}")
                break

        if dialog:
            dialog_text = await dialog.inner_text()
            
            # --- Detection for Sign-in / Blockage ---
            d_low = dialog_text.lower()
            if "sign in" in d_low and "view email address" not in d_low:
                if on_log: on_log("  [scraper] Sign-in required for this channel's email address. Skipping YouTube extraction.")
                return None
            
            # --- NEW: Pre-click scan ---
            if on_log: on_log("  [scraper] Performing pre-click scan on modal text...")
            pre_click_valid = extract_emails_from_text(dialog_text)
            if pre_click_valid:
                if on_log: on_log(f"  [scraper] SUCCESS: Found email in pre-click scan: {pre_click_valid[0]}")
                return pre_click_valid[0]

            # Check for the button inside the dialog
            btn_selectors = [
                'button:has-text("View email address")',
                '#view-email-button',
                'ytd-button-renderer:has-text("View email address")',
                'button[aria-label*="View email"]',
                '.ytd-about-channel-view-model button:not([aria-hidden="true"])'
            ]
            
            email_btn = None
            for bsel in btn_selectors:
                b_found = dialog.locator(bsel)
                if await b_found.count() > 0 and await b_found.first.is_visible():
                    email_btn = b_found.first
                    if on_log: on_log(f"  [scraper] Found 'View email address' button: {bsel}")
                    break

            if email_btn:
                if on_log: on_log("  [scraper] Clicking 'View email address'...")
                # Human-like jitter
                await page.wait_for_timeout(random.randint(1500, 3000))
                await _human_click(page, email_btn, on_log)
                
                # Intelligent Revelation Wait: Wait for the @ symbol to appear in the dialog text
                revelation_start = asyncio.get_event_loop().time()
                revealed = False
                while (asyncio.get_event_loop().time() - revelation_start) < (VIEW_EMAIL_POST_CLICK_WAIT_MS / 1000 + 5):
                    new_text = await dialog.inner_text()
                    if "@" in new_text:
                        revealed = True
                        break
                    # Also check for reCAPTCHA surfacing
                    if "recaptcha" in new_text.lower():
                        break
                    await asyncio.sleep(0.5)
                
                if revealed:
                    if on_log: on_log(f"  [scraper] Revelation detected after {round(asyncio.get_event_loop().time() - revelation_start, 1)}s")
                else:
                    if on_log: on_log("  [scraper] Timed out waiting for @ after first click. Retrying interaction...")
                    # Small scroll and retry click
                    await page.mouse.wheel(0, 100)
                    await page.wait_for_timeout(1000)
                    await _human_click(page, email_btn, on_log)
                    await page.wait_for_timeout(VIEW_EMAIL_POST_CLICK_WAIT_MS)
                
                # Final Check
                new_text = await dialog.inner_text()
                valid = extract_emails_from_text(new_text)
                if valid:
                    if on_log: on_log(f"  [scraper] Successfully extracted email: {valid[0]}")
                    return valid[0]

                # Check HTML if text failed (sometimes emails are in title attributes or hidden elements)
                revealed_html = await dialog.inner_html()
                valid = extract_emails_from_text(revealed_html)
                if valid:
                    return valid[0]

                if "recaptcha" in revealed_html.lower() or "g-recaptcha" in revealed_html.lower():
                    if on_log: on_log("  [scraper] reCAPTCHA block detected after clicking View Email.")
            else:
                if on_log and opened: on_log("  [scraper] 'View email address' button not found in modal.")
        elif opened:
            if on_log: on_log("  [scraper] Modal was opened but no content container found.")
    except Exception as e:
        if on_log: on_log(f"Could not interact with 'More info' dialog: {str(e)}")


    # Fallback to general page text
    if on_log: on_log("  [scraper] Performing fallback extraction from page body...")
    try:
        page_text = await page.inner_text("body", timeout=5000)
        valid = extract_emails_from_text(page_text)
        if valid:
            if on_log: on_log(f"  [scraper] Found email in page text: {valid[0]}")
            return valid[0]
    except Exception as ex:
        if on_log: on_log(f"  [scraper] Body inner_text failed: {str(ex)}")

    try:
        html = await page.content()
        valid = extract_emails_from_text(html)
        if valid:
            return valid[0]
        
        if on_log:
            snippet = (await page.inner_text("body"))[:500].replace("\n", " ").strip()
            on_log(f"  [scraper] No email found. Page snippet: {snippet}...")
    except Exception:
        pass

    # --- MOBILE FALLBACK ---
    if SCRAPER_MOBILE_FALLBACK and "m.youtube.com" not in page.url:
        if on_log: on_log(f"  [scraper] Desktop extraction failed for {channel_url}. Trying mobile fallback...")
        mobile_email = await try_extract_from_mobile_about(page, channel_url, on_log)
        if mobile_email:
            return mobile_email

    return None

async def try_extract_from_mobile_about(page, channel_url: str, on_log=None) -> str | None:
    """Fallback extraction using the mobile YouTube interface."""
    try:
        # Transform URL to mobile version
        mobile_url = channel_url.replace("www.youtube.com", "m.youtube.com")
        if "/about" not in mobile_url:
            mobile_url = mobile_url.rstrip("/") + "/about"
        
        if on_log: on_log(f"  [mobile] Visiting mobile about page: {mobile_url}")
        await page.goto(mobile_url, wait_until="domcontentloaded", timeout=ABOUT_TIMEOUT_MS)
        await page.wait_for_timeout(ABOUT_POST_LOAD_WAIT_MS)

        # Basic text scan first
        body_text = await page.inner_text("body")
        emails = extract_emails_from_text(body_text)
        if emails:
            if on_log: on_log(f"  [mobile] SUCCESS: Found email in text: {emails[0]}")
            return emails[0]

        # Look for mobile "View email address" button
        # Mobile specific selectors
        mobile_selectors = [
            'button:has-text("View email address")',
            'ytm-promoted-sparkles-web-renderer button',
            'button[aria-label*="email"]',
            '.yt-spec-button-shape-next--call-to-action'
        ]

        for sel in mobile_selectors:
            btn = page.locator(sel)
            if await btn.count() > 0 and await btn.first.is_visible():
                if on_log: on_log(f"  [mobile] Clicking button: {sel}")
                await _human_click(page, btn.first, on_log)
                await page.wait_for_timeout(VIEW_EMAIL_POST_CLICK_WAIT_MS)
                
                new_text = await page.inner_text("body")
                emails = extract_emails_from_text(new_text)
                if emails:
                    if on_log: on_log(f"  [mobile] SUCCESS: Found email after click: {emails[0]}")
                    return emails[0]
                    
    except Exception as e:
        if on_log: on_log(f"  [mobile] Error: {str(e)}")

    return None
