"""
Shared Browser Manager — Persistent or session-based Playwright browser.
Includes Stealth Mode to bypass bot detection.
"""

import asyncio
import os
from playwright_stealth import Stealth
from core.config import BROWSER_HEADLESS, BROWSER_TIMEOUT_MS, SCRAPER_API_KEY, BROWSER_USER_DATA_DIR, USE_BROWSER_PROXY, BROWSER_ZOOM, BROWSER_PROXY_SESSION, YOUTUBE_COOKIES

# Lazy import so the module doesn't crash when Playwright isn't installed
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class BrowserManager:
    """Manages a shared Playwright browser instance or persistent context across services."""
    _playwright = None
    _browser = None
    _context = None  # Stores the persistent context if applicable
    _last_ephemeral_context = None # Track ephemeral if persistent is not used
    _active_contexts = [] # Tracks ephemeral contexts for cleanup
    _cookies_injected = False  # Track if hosted cookies have been injected
    _lock = asyncio.Lock()

    @classmethod
    async def get_browser(cls, headless: bool | None = None):
        """
        Internal: Returns the browser instance. 
        If using persistent context, returns the browser associated with it.
        """
        if not PLAYWRIGHT_AVAILABLE:
            return None

        async with cls._lock:
            if cls._playwright is None:
                cls._playwright = await async_playwright().start()

            # Health Check
            is_healthy = False
            if cls._context:
                try:
                    # Check if the context is still "alive" by attempting a lightweight call
                    if cls._context.browser and cls._context.browser.is_connected():
                        is_healthy = True
                except Exception:
                    is_healthy = False
            elif cls._browser and cls._browser.is_connected():
                is_healthy = True

            if is_healthy: return cls._browser

            if cls._context or cls._browser:
                await cls.close()

            # Launch fresh
            use_headless = headless if headless is not None else BROWSER_HEADLESS
            
            import uuid
            # Use a STABLE session ID for persistent context to keep the same proxy IP
            # This is critical for maintaining Google sign-in across restarts
            if BROWSER_PROXY_SESSION:
                session_id = BROWSER_PROXY_SESSION
            else:
                session_id = str(uuid.uuid4())[:12]

            # Revert to Stable High-Authority UA for better compatibility
            stable_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

            launch_common_args = {
                "headless": use_headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--start-maximized",
                    "--disable-features=Translate,OptimizationHints,DialMediaRouteProvider",
                    "--no-first-run",
                ],
                "ignore_default_args": ["--enable-automation"]
            }
            # Strategy: Use persistent context if User Data Dir is specified
            if BROWSER_USER_DATA_DIR:
                # Ensure the directory exists
                os.makedirs(BROWSER_USER_DATA_DIR, exist_ok=True)
                
                # For persistent contexts, we MUST include proxy and locale/timezone here
                # since the context is created at launch.
                persist_args = {**launch_common_args}
                if SCRAPER_API_KEY and USE_BROWSER_PROXY:
                    persist_args["proxy"] = {
                        "server": "http://proxy-server.scraperapi.com:8001",
                        "username": f"scraperapi.session_id={session_id}",
                        "password": SCRAPER_API_KEY
                    }

                cls._context = await cls._playwright.chromium.launch_persistent_context(
                    user_data_dir=BROWSER_USER_DATA_DIR,
                    viewport=None, # Maximized
                    user_agent=stable_ua,
                    ignore_https_errors=True,
                    **persist_args
                )
                cls._browser = cls._context.browser
            else:
                # Ephemeral browser - CLEAN LAUNCH (No proxy here, it goes in new_context)
                cls._browser = await cls._playwright.chromium.launch(**launch_common_args)
                cls._context = None
                
            return cls._browser

    @classmethod
    async def get_page(cls, headless: bool | None = None, optimize: bool = False, session_id: str | None = None, region: str = "US"):
        """
        Returns (context, page) with Stealth Mode applied.
        If optimize=True, images and media are blocked.
        If region is provided, locale and timezone are synced to that region.
        """
        browser = await cls.get_browser(headless=headless)
        if not browser and not cls._context:
            return None, None

        import uuid
        import random
        # Generate a unique session if requested, otherwise use global/shared
        actual_session = session_id if session_id else str(uuid.uuid4())[:12]
        
        # --- Dynamic Identity Mapping ---
        # Map region to (locale, timezone) to prevent local IP/Header leaks
        identity_map = {
            "US": {"locale": "en-US", "timezone": "America/New_York"},
            "UK": {"locale": "en-GB", "timezone": "Europe/London"},
            "GB": {"locale": "en-GB", "timezone": "Europe/London"},
            "Both": {"locale": "en-US", "timezone": "America/New_York"},
            "IN": {"locale": "en-IN", "timezone": "Asia/Kolkata"},
        }
        identity = identity_map.get(region, identity_map["US"])

        stable_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

        context_args = {
            "viewport": None, # Maximized
            "user_agent": stable_ua,
            "ignore_https_errors": True,
            "locale": identity["locale"],
            "timezone_id": identity["timezone"],
            "extra_http_headers": {
                "Accept-Language": f"{identity['locale']},en;q=0.9"
            }
        }

        # If we need a specific session, we MUST create a new context
        # because proxy settings are context-level.
        if session_id or not cls._context:
            if SCRAPER_API_KEY and USE_BROWSER_PROXY:
                context_args["proxy"] = {
                    "server": "http://proxy-server.scraperapi.com:8001",
                    "username": f"scraperapi.session_id={actual_session}",
                    "password": SCRAPER_API_KEY
                }
            context = await browser.new_context(**context_args)
            cls._active_contexts.append(context) # Track for global cleanup
            page = await context.new_page()
            cls._last_ephemeral_context = context
        else:
            # Use shared persistent context
            page = await cls._context.new_page()
            context = cls._context

        # Apply Stealth
        await Stealth().apply_stealth_async(page)

        # Inject hosted cookies if YOUTUBE_COOKIES env var is set
        # (for server deployments where manual sign-in is impossible)
        await cls._inject_cookies(context)

        # Apply Refined Optimization (Block large media, allow UI essentials)
        if optimize:
            await page.route("**/*", lambda route: 
                route.abort() if route.request.resource_type in ["image", "media"] 
                # Note: 'font' and 'stylesheet' are ALLOWED now for YouTube UI
                else route.continue_()
            )

        return (context or cls._last_ephemeral_context), page

    @classmethod
    async def _inject_cookies(cls, context):
        """
        Inject YouTube auth cookies from YOUTUBE_COOKIES env var.
        Used on hosted servers (Render, etc.) where manual sign-in is impossible.
        Cookies are exported locally via scratch/export_cookies.py.
        """
        if not YOUTUBE_COOKIES or cls._cookies_injected:
            return
        
        try:
            import json
            import base64
            cookies_json = base64.b64decode(YOUTUBE_COOKIES).decode()
            cookies = json.loads(cookies_json)
            
            # Playwright expects 'sameSite' values to be capitalized
            for cookie in cookies:
                if 'sameSite' in cookie:
                    val = cookie['sameSite']
                    if val in ('strict', 'Strict'):
                        cookie['sameSite'] = 'Strict'
                    elif val in ('lax', 'Lax'):
                        cookie['sameSite'] = 'Lax'
                    elif val in ('none', 'None'):
                        cookie['sameSite'] = 'None'
                    else:
                        cookie['sameSite'] = 'Lax'
            
            await context.add_cookies(cookies)
            cls._cookies_injected = True
            print(f"[BrowserManager] Injected {len(cookies)} auth cookies from YOUTUBE_COOKIES env var")
        except Exception as e:
            print(f"[BrowserManager] WARNING: Failed to inject cookies from YOUTUBE_COOKIES: {e}")

    @classmethod
    async def cleanup(cls):
        """Closes all orphaned ephemeral contexts to free up proxy sessions and memory."""
        async with cls._lock:
            if not cls._active_contexts:
                return
            
            for context in cls._active_contexts:
                try:
                    await context.close()
                except:
                    pass
            cls._active_contexts = []
            cls._last_ephemeral_context = None

    @classmethod
    async def force_restart(cls):
        """Forcefully kills the browser and playwright instance to clear hung proxy sessions."""
        await cls.cleanup() # Clean orphans first
        await cls.close()
        cls._playwright = None
        cls._browser = None
        cls._context = None

    @classmethod
    async def close(cls):
        """Shut down the browser/context and Playwright runtime."""
        async with cls._lock:
            await cls.cleanup() # Clean ephemeral contexts
            if cls._context:
                try:
                    await cls._context.close()
                except Exception:
                    pass
                cls._context = None

            if cls._browser:
                try:
                    await cls._browser.close()
                except Exception:
                    pass
                cls._browser = None

            if cls._playwright:
                try:
                    await cls._playwright.stop()
                except Exception:
                    pass
                cls._playwright = None
