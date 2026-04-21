"""
Scaper for YouTube About Page — Prioritized Tier 1 extraction.
Handles modern modals, CAPTCHAs, and hidden email buttons.
Optimized for Python 3.11 + Automated solving.
"""

import asyncio
import re
import time
try:
    import winsound  # Windows-only audio alerts (optional)
except ImportError:
    winsound = None  # Graceful no-op on Linux/hosted servers
from services.utils.browser_manager import BrowserManager
from services.utils.stealth_utils import human_click, human_scroll, human_delay

# Robust import for Recaptcha Solver
try:
    from playwright_recaptcha import recaptchav2
except (ImportError, ModuleNotFoundError):
    recaptchav2 = None

async def extract_email_from_about_page(channel_url, on_log=None, session_id=None, region="US"):
    """
    Tier 1: Open the channel About page in a browser, 
    navigates the 'View email address' modal, solves CAPTCHA, and reveals email.
    """
    from core.config import BROWSER_TIMEOUT_MS, ENABLE_ADVANCED_STEALTH
    if "youtube.com" not in channel_url:
        return None

    # Normalizing URL
    if not channel_url.endswith("/about"):
        if "/@" in channel_url:
            channel_url = channel_url.split("?")[0].rstrip("/") + "/about"

    # Use a unique session ID for this specific channel to prevent "shared" proxy blocks
    import uuid
    current_session = session_id if session_id else str(uuid.uuid4())[:12]
    
    # Enable 'optimize=True' but with the new refined blocking (allows fonts/icons)
    context, page = await BrowserManager.get_page(optimize=True, session_id=current_session, region=region)
    if not page:
        return None

    try:
        if on_log: on_log(f"[about] Opening channel page: {channel_url}")
        
        await page.bring_to_front()
        
        # Apply Zoom via CSS (Resilient to proxy lag)
        from core.config import BROWSER_ZOOM
        if BROWSER_ZOOM and BROWSER_ZOOM != 1.0:
            try:
                # Convert 0.8 to 80% etc
                zoom_pct = int(BROWSER_ZOOM * 100)
                await page.add_init_script(f"document.documentElement.style.zoom = '{zoom_pct}%'")
            except: pass

        # Navigation (Aggressive timeout for proxy stability)
        NAV_TIMEOUT = 60000  # Increased to 60s
        for attempt in range(3):
            try:
                await page.goto(channel_url, wait_until="load", timeout=NAV_TIMEOUT)
                
                # Check for "Offline" / Connect to internet error (Proxy failure)
                content = await page.content()
                if "Connect to the internet" in content or "You're offline" in content or "This site can’t be reached" in content:
                    if on_log: on_log(f"  [about] Proxy Session Blocked (Offline) on attempt {attempt+1}")
                    # Change identity immediately instead of a hard restart
                    new_session = str(uuid.uuid4())[:12]
                    if on_log: on_log(f"  [about] Swapping to fresh proxy session: {new_session}")
                    try: await context.close()
                    except: pass
                    context, page = await BrowserManager.get_page(optimize=True, session_id=new_session, region=region)
                    raise Exception("Browser shows Offline — Session blacklisted by YouTube.")
                break
            except Exception as e:
                err_msg = str(e).lower()
                if "target closed" in err_msg or "protocol error" in err_msg:
                    if on_log: on_log(f"  [about] Browser Crash detected: {err_msg[:50]}. Closing context...")
                    await context.close()
                    # Re-acquire context for next attempt
                    context, page = await BrowserManager.get_page(region=region)
                
                if attempt == 2: raise e
                await asyncio.sleep(6) # Longer wait between retries

        # 1. Identify the 'View email address' button
        button_selectors = [
            "yt-button-view-model button",
            "tp-yt-paper-button#button",
            "button[aria-label*='email']",
            "text='View email address'",
            "ytd-channel-about-metadata-renderer button"
        ]

        button = None
        for selector in button_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=5000)
                if button: break
            except: continue

        if not button:
            if on_log: on_log("  [about] 'View email address' button not found.")
            return None

        # Focus and scroll into view (Realistic movement)
        if ENABLE_ADVANCED_STEALTH:
            if on_log: on_log(f"[about] Found 'View email address' button (selector: {selector}) — using human-like click...")
            # FIX: Use the 'selector' that actually worked, not always button_selectors[0]
            await human_click(page, selector) 
        else:
            await button.scroll_into_view_if_needed()
            if on_log: on_log("[about] Found 'View email address' button — clicking...")
            await button.click()
        
        # Wait for the modal/reveal to actually happen
        await page.wait_for_load_state("networkidle", timeout=10000)
        await asyncio.sleep(2)

        # 2. Handle CAPTCHA
        content = await page.content()
        captcha_indicators = ["recaptcha", "g-recaptcha", "captcha", "verify you are a human"]
        has_captcha = any(indicator in content.lower() for indicator in captcha_indicators)

        if has_captcha:
            if on_log: on_log("  [about] [CAPTCHA] DETECTED — Launching automated audio solver...")
            
            # Brief audio alert
            try:
                if winsound: winsound.Beep(1000, 200)
            except: pass

            # Inject a status banner
            await page.evaluate("""
                const banner = document.createElement('div');
                banner.id = 'bot-status-banner';
                banner.style.position = 'fixed';
                banner.style.top = '0';
                banner.style.left = '0';
                banner.style.width = '100%';
                banner.style.backgroundColor = '#2c3e50';
                banner.style.color = 'white';
                banner.style.textAlign = 'center';
                banner.style.padding = '10px';
                banner.style.zIndex = '9999';
                banner.style.fontSize = '18px';
                banner.textContent = 'ROBOT STATUS: Solving CAPTCHA...';
                document.body.prepend(banner);
            """)
            await page.bring_to_front()

            # Automation attempt (Multi-Retry Loop)
            if recaptchav2:
                for solve_attempt in range(1, 4):
                    if on_log: on_log(f"  [about] [CAPTCHA] Solver Attempt {solve_attempt}/3...")
                    
                    # Update status banner
                    await page.evaluate(f"document.getElementById('bot-status-banner').textContent = 'ROBOT STATUS: Solving CAPTCHA (Attempt {solve_attempt}/3)...'")
                    
                    try:
                        async with recaptchav2.AsyncSolver(page) as solver:
                            await solver.solve_recaptcha()
                        if on_log: on_log(f"  [about] [CAPTCHA] [SUCCESS] Solved automatically on attempt {solve_attempt}.")
                        break # Success!
                    except Exception as e:
                        if solve_attempt < 3:
                            if on_log: on_log(f"  [about] [CAPTCHA] Attempt {solve_attempt} failed. Refreshing and retrying...")
                            # Refresh the captcha by clicking the 'Reload' button in the recaptcha iframe if possible
                            # Or just continue to next loop which will try again with a fresh capture
                            await asyncio.sleep(2)
                        else:
                            if on_log: on_log(f"  [about] [CAPTCHA] All automated attempts failed. Switching to manual focus.")
                            await _wait_for_manual_solve(page, on_log)
            else:
                if on_log: on_log("  [about] Solver library missing. Please solve manually.")
                await _wait_for_manual_solve(page, on_log)

            # 3. Click 'Submit' inside the modal after solving
            submit_selectors = [
                "button#submit-btn", 
                "yt-button-renderer#submit-button", 
                "button:has-text('Submit')",
                "button[aria-label*='Submit']"
            ]
            submit_btn = None
            for sel in submit_selectors:
                try:
                    submit_btn = await page.wait_for_selector(sel, timeout=5000)
                    if submit_btn: break
                except: continue

            if submit_btn:
                await submit_btn.scroll_into_view_if_needed()
                if on_log: on_log("  [about] Clicking 'Submit' button...")
                await submit_btn.click()
                await asyncio.sleep(4) # Slower wait for reveal
            else:
                if on_log: on_log("  [about] Submit button not found. Checking if email revealed anyway.")

        # 4. Extract revealed email
        await asyncio.sleep(2)
        post_content = await page.content()
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        
        email = None
        containers = [
            "yt-formatted-string[id='email']", 
            "yt-formatted-string#email",
            "a[href^='mailto:']"
        ]
        for c in containers:
            try:
                el = await page.query_selector(c)
                if el:
                    text = await el.inner_text()
                    matches = re.findall(email_pattern, text)
                    if matches:
                        email = matches[0]
                        break
            except: continue

        if not email:
            matches = re.findall(email_pattern, post_content)
            matches = [e for e in matches if "sentry.io" not in e and "google.com" not in e and "youtube-ui" not in e]
            if matches: email = matches[0]

        if email:
            if on_log:
                if has_captcha:
                    on_log(f"[about] [CAPTCHA-REVEAL] SUCCESS: Email revealed after solving: {email}")
                else:
                    on_log(f"[about] SUCCESS: Email revealed (Clean Reveal): {email}")
            return email

        if on_log: on_log("[about] Extraction failed or no email listed.")
        return None

    except Exception as e:
        if "blacklisted" in str(e):
             # This specific error already handled the session swap, so we just pass it up
             raise e
        if on_log: on_log(f"  [about] Fatal Error: {str(e)}")
        return None
    finally:
        # CRITICAL: Always close the context to free up proxy sessions and memory
        try:
            if on_log: on_log(f"  [about] Cleaning up browser context for {current_session}...")
            await context.close()
        except:
            pass

async def _wait_for_manual_solve(page, on_log):
    """Wait up to 5 minutes for the user to solve the captcha manually."""
    if on_log: on_log("  [about] BOT WAITING: Please solve the CAPTCHA manually in the browser window.")
    
    # Update banner
    await page.evaluate("""
        const b = document.getElementById('bot-status-banner');
        if(b) {
            b.style.backgroundColor = 'red';
            b.textContent = 'ACTION REQUIRED: Please solve CAPTCHA manually!';
        }
    """)
    
    start_time = time.time()
    while time.time() - start_time < 300:
        if int(time.time()) % 10 == 0:
            await page.bring_to_front()
        
        content = await page.content()
        captcha_indicators = ["recaptcha", "g-recaptcha", "captcha", "verify you are a human"]
        if not any(indicator in content.lower() for indicator in captcha_indicators):
            return True
        if await page.query_selector("button#submit-btn:not([disabled])"):
            return True
        await asyncio.sleep(2)
    return False
