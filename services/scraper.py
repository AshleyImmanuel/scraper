"""
Email Scraper - Extracts public emails from YouTube channels
using restricted metadata (descriptions) via YouTube API.
"""
import sys
import asyncio
import os
import traceback

from core.config import FAST_CHECK_VIDEO_COUNT
from services.utils.extraction import extract_emails_from_text
from services.youtube import get_recent_videos

async def extract_emails(results: list[dict], on_progress=None, on_log=None) -> list[dict]:
    """
    Main extraction pipeline:
    1. YouTube Channel and Video Descriptions (from Search results)
    2. Recent Video Descriptions (via recursive API lookup)
    """
    total = len(results)

    if on_log: on_log(f"Starting concurrent lightweight extraction for {total} candidates...")
    
    # Allow 20 channels to be processed concurrently, supercharging the speed
    sem = asyncio.Semaphore(20)

    async def process_channel(idx, row):
        async with sem:
            channel_name = row["channelName"]
            channel_id = row["channelId"]
            
            # --- TIER 0: Pre-found Email (e.g., via Google Discovery) ---
            if row.get("EMAIL") and row["EMAIL"] != "nil":
                if on_progress: on_progress(idx + 1, total, channel_name, row["EMAIL"])
                return
                
            # --- TIER 1: YouTube API (Search Snippets + Full Desc) ---
            full_context = f"{row.get('channelDescription','')} {row.get('videoDescription','')}"
            fast_check = extract_emails_from_text(full_context)
            if fast_check:
                row["EMAIL"] = fast_check[0]
                if on_progress: on_progress(idx + 1, total, channel_name, fast_check[0])
                return

            # --- TIER 2: YouTube API (Recent Video Descriptions) ---
            if FAST_CHECK_VIDEO_COUNT > 0:
                try:
                    recent_vids = await asyncio.to_thread(get_recent_videos, channel_id, FAST_CHECK_VIDEO_COUNT)
                    all_vids_text = "".join([f" {vid['title']} {vid['description']}" for vid in recent_vids])
                    
                    v_emails = extract_emails_from_text(all_vids_text)
                    if v_emails:
                        row["EMAIL"] = v_emails[0]
                        if on_log: on_log(f"  [api] SUCCESS: Found in video descriptions for {channel_name}")
                        if on_progress: on_progress(idx + 1, total, channel_name, v_emails[0])
                        return
                except Exception:
                    pass

            # If we reach here, we didn't find an email in any description.
            row["EMAIL"] = "nil"
            if on_progress: on_progress(idx + 1, total, channel_name, None)

    tasks = [process_channel(idx, row) for idx, row in enumerate(results)]
    await asyncio.gather(*tasks)

    # NEW: "ignore the rest" -> Filter out channels that didn't yield an email
    filtered_results = [r for r in results if r.get("EMAIL") and r["EMAIL"] != "nil"]
    
    if on_log:
        dropped = len(results) - len(filtered_results)
        on_log(f"Filtered out {dropped} channels that had no email in descriptions.")

    return filtered_results
