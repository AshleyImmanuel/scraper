"""
Email Scraper — Prioritizes the "Protected" YouTube About Page email extraction.
Upgraded with Stealth driving and Human Pacing.
"""
import sys
import asyncio
import os
import random
import traceback

from core.config import (
    FAST_CHECK_VIDEO_COUNT, 
    USE_LOCAL_BROWSER, 
    PACING_DELAY_SECONDS
)
from services.utils.extraction import extract_emails_from_text, extract_urls_from_text
from services.external_scraper import scrape_multiple_urls
from services.utils.browser_manager import BrowserManager, PLAYWRIGHT_AVAILABLE

async def extract_emails(results: list[dict], on_progress=None, on_log=None, region="US") -> list[dict]:
    """
    Main extraction pipeline (Priority Re-ordered):
    1. YouTube About Page (Tier 1 — High Accuracy, Protected Emails)
    2. YouTube Descriptions (Tier 2)
    3. External Link Inspection (Tier 3)
    4. Google Dorking (Tier 4)
    """
    total = len(results)

    if on_log: on_log(f"Starting prioritized extraction for {total} candidates (About Page First)...")
    
    # Concurrency control
    from core.config import SCRAPER_CONCURRENCY
    sem = asyncio.Semaphore(SCRAPER_CONCURRENCY)

    async def process_channel(idx, row, region="US"):
        async with sem:
            channel_name = row["channelName"]
            channel_id = row["channelId"]
            
            # --- HUMAN-LIKE PACING ---
            # Increase jitter depth to desynchronize requests more effectively
            jitter = PACING_DELAY_SECONDS * random.uniform(0.7, 2.5)
            if idx > 0:
                await asyncio.sleep(jitter)
            
            # --- TIER 0: Pre-found Email ---
            if row.get("EMAIL") and row["EMAIL"] != "nil":
                if on_progress: on_progress(idx + 1, total, channel_name, row["EMAIL"])
                return
                
            # --- TIER 1 & 4 (PARALLEL MASTERY) ---
            # To maximize yield, we run the About Page (Browser) and Google Dorking (API) in parallel.
            if on_log: on_log(f"  [yield] Launching parallel extraction (About + Dorking) for {channel_name}...")
            
            async def run_about():
                if USE_LOCAL_BROWSER and PLAYWRIGHT_AVAILABLE:
                    try:
                        from services.youtube_about_scraper import extract_email_from_about_page
                        channel_url = row.get("channelUrl", "")
                        if channel_url:
                            # Pass region for Dynamic Identity Sync
                            return await extract_email_from_about_page(channel_url, on_log=on_log, region=region)
                    except: pass
                return None

            async def run_dorking():
                from services.google_discovery import dork_specific_channel
                try:
                    # Try name and handle dorking in one go
                    results = await asyncio.to_thread(dork_specific_channel, channel_name, on_log)
                    if not results:
                        handle = row.get("id")
                        if handle and handle.startswith("@"):
                            results = await asyncio.to_thread(dork_specific_channel, handle, on_log)
                    return results[0] if results else None
                except: pass
                return None

            # Execute both concurrently
            about_task = asyncio.create_task(run_about())
            dork_task = asyncio.create_task(run_dorking())
            
            # Wait for either to succeed, or both to finish
            # Note: We prefer About Page result usually as it's Tier 1, but any hit is a win.
            try:
                done, pending = await asyncio.wait(
                    [about_task, dork_task], 
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=180
                )
            except asyncio.CancelledError:
                for t in [about_task, dork_task]: t.cancel()
                raise

            email_hit = None
            for t in done:
                try:
                    res = t.result()
                    if res:
                        email_hit = res
                        break
                except: pass
            
            if email_hit:
                row["EMAIL"] = email_hit
                if on_progress: on_progress(idx + 1, total, channel_name, email_hit)
                # Cancel pending
                for p in pending: 
                    p.cancel()
                    # Give it a tiny bit of time to run its finally blocks
                await asyncio.sleep(0.1) 
                return

            # If first finished but no hit, wait for the rest
            if pending:
                try:
                    done2, _ = await asyncio.wait(pending, timeout=60)
                    for t in done2:
                        try:
                            res = t.result()
                            if res:
                                email_hit = res
                                break
                        except: pass
                except asyncio.CancelledError:
                    for p in pending: p.cancel()
                    raise
            
            if email_hit:
                row["EMAIL"] = email_hit
                if on_progress: on_progress(idx + 1, total, channel_name, email_hit)
                return

            # --- TIER 2: YouTube Descriptions Fallback ---
            full_context = f"{row.get('channelDescription','')} {row.get('videoDescription','')}"
            fast_check = extract_emails_from_text(full_context)
            if fast_check:
                row["EMAIL"] = fast_check[0]
                if on_progress: on_progress(idx + 1, total, channel_name, fast_check[0])
                return

            # --- TIER 3: External Link Inspection (Linktree, Instagram, etc.) ---
            urls = extract_urls_from_text(full_context)
            if urls:
                if on_log: on_log(f"  [yield] Scanning {len(urls)} external links for {channel_name}...")
                external_emails = await scrape_multiple_urls(urls, on_log=on_log, region=region)
                if external_emails:
                    email_hit = external_emails[0]
                    row["EMAIL"] = email_hit
                    if on_progress: on_progress(idx + 1, total, channel_name, email_hit)
                    return

            # Final Result: No email found
            if not row.get("EMAIL") or row["EMAIL"] == "nil":
                row["EMAIL"] = "nil"
                if on_progress: on_progress(idx + 1, total, channel_name, None)
            else:
                # We already have an email (likely from a partially successful phase or pre-found)
                # Ensure on_progress is called to confirm completion
                if on_progress: on_progress(idx + 1, total, channel_name, row["EMAIL"])

    # Process all channels concurrently (with semaphore and pacing)
    # We use a shuffled order to further desynchronize proxy sessions
    random.shuffle(results)
    
    async def process_with_timeout(idx, row):
        try:
            # Wrap the entire channel processing in a 5-minute timeout
            # This prevents the whole job from getting "stuck" if one channel hangs
            await asyncio.wait_for(process_channel(idx, row, region=region), timeout=300)
        except asyncio.TimeoutError:
            if on_log: on_log(f"  [FATAL] Timeout processing channel {row['channelName']}. Moving on...")
            row["EMAIL"] = "nil"
            if on_progress: on_progress(idx + 1, total, row["channelName"], None)
        except Exception as e:
            if on_log: on_log(f"  [FATAL] Error processing channel {row['channelName']}: {str(e)}")
            row["EMAIL"] = "nil"
            if on_progress: on_progress(idx + 1, total, row["channelName"], None)

    try:
        tasks = [process_with_timeout(idx, row) for idx, row in enumerate(results)]
        await asyncio.gather(*tasks)
    finally:
        # MISSION CRITICAL: Clear all zombie browser contexts once the job is done
        if on_log: on_log(f"\n[scraper] Finishing job — Closing all remaining browser sessions...")
        await BrowserManager.cleanup()

    if on_log:
        on_log(f"Extraction complete for {len(results)} candidates.")

    return results
