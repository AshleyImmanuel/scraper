import asyncio
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from services.utils.extraction import format_exception
from core.config import (
    SCRAPER_MAX_RETRIES as MAX_RETRIES,
    SCRAPER_RETRY_DELAY_MS as RETRY_DELAY_MS
)
from services.extraction.about_strategy import try_extract_from_about
from services.extraction.links_strategy import try_extract_from_links

async def extract_email_from_channel(page, channel_url: str, on_log=None) -> str | None:
    """Full extraction pipeline for a single channel with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        had_error = False
        try:
            email = await try_extract_from_about(page, channel_url, on_log)
            if email: return email
        except PlaywrightTimeoutError as exc:
            had_error = True
            if on_log: on_log(f"Attempt {attempt}/{MAX_RETRIES} timeout for {channel_url}: {format_exception(exc)}")
        except Exception as exc:
            had_error = True
            if on_log: on_log(f"Attempt {attempt}/{MAX_RETRIES} error for {channel_url}: {format_exception(exc)}")

        try:
            email = await try_extract_from_links(page, channel_url, on_log=on_log)
            if email: return email
        except PlaywrightTimeoutError as exc:
            had_error = True
            if on_log: on_log(f"Attempt {attempt}/{MAX_RETRIES} links timeout: {format_exception(exc)}")
        except Exception as exc:
            had_error = True
            if on_log: on_log(f"Attempt {attempt}/{MAX_RETRIES} links error: {format_exception(exc)}")

        if not had_error: return None
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY_MS / 1000)
        else:
            if on_log: on_log(f"All retries exhausted for {channel_url}.")
            return None
