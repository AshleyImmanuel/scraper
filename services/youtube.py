"""
YouTube Data API v3 — Search & Filter Service
"""
import re
from googleapiclient.discovery import build
from core.config import (
    YOUTUBE_API_KEY,
    YOUTUBE_RELEVANCE_LANGUAGE,
    ALLOWED_COUNTRIES_US,
    ALLOWED_COUNTRIES_UK,
    YOUTUBE_EXCLUSION_KEYWORDS as EXCLUSION_KEYWORDS,
    YOUTUBE_STRICT_EXCLUSIONS as STRICT_EXCLUSIONS,
    YOUTUBE_PRIORITY_KEYWORDS as PRIORITY_KEYWORDS,
    YOUTUBE_CHANNEL_EXCLUSION_KEYWORDS as CHANNEL_EXCLUSION_KEYWORDS,
    YOUTUBE_AUTHORITY_KEYWORDS as AUTHORITY_KEYWORDS,
    YOUTUBE_AUTHORITY_MIN_DURATION as AUTHORITY_MIN_DUR,
    YOUTUBE_LONG_MIN_DURATION as LONG_MIN_DUR
)
from services.utils.youtube_helpers import (
    normalize_region_code,
    date_filter_to_rfc3339,
    parse_duration,
    parse_duration_seconds
)

ALLOWED_COUNTRIES_BY_REGION = {
    "US": ALLOWED_COUNTRIES_US,
    "UK": ALLOWED_COUNTRIES_UK,
}


def _build_client():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def search_videos(keyword: str, region: str, date_filter: str, max_results: int = 50, page_token: str = None, video_type: str = "All"):
    """Search YouTube for videos matching the keyword."""
    client = _build_client()
    published_after = date_filter_to_rfc3339(date_filter)
    reg = normalize_region_code(region)

    search_params = dict(
        part="snippet",
        q=keyword,
        type="video",
        regionCode=reg,
        relevanceLanguage=YOUTUBE_RELEVANCE_LANGUAGE,
        publishedAfter=published_after,
        maxResults=min(50, max_results),
        pageToken=page_token,
        order="relevance",
    )
    if video_type == "Shorts":
        search_params["videoDuration"] = "short"
    elif video_type == "Long":
        search_params["videoDuration"] = "long"

    request = client.search().list(**search_params)
    response = request.execute()
    all_video_ids = []

    for item in response.get("items", []):
        if item.get("snippet", {}).get("liveBroadcastContent") in ("live", "upcoming"):
            continue

        all_video_ids.append({
            "videoId": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "description": item["snippet"].get("description", ""),
            "channelId": item["snippet"]["channelId"],
            "channelTitle": item["snippet"]["channelTitle"],
            "publishedAt": item["snippet"]["publishedAt"][:10],
            "region": "UK" if reg == "GB" else reg,
        })

    return all_video_ids, response.get("nextPageToken")


def get_video_details(video_ids: list[str]):
    """Fetch video statistics (viewCount, likeCount, duration) for a batch of IDs."""
    client = _build_client()
    details = {}

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        request = client.videos().list(
            part="snippet,statistics,contentDetails,liveStreamingDetails",
            id=",".join(batch),
        )
        response = request.execute()

        for item in response.get("items", []):
            vid = item["id"]
            stats = item.get("statistics", {})
            raw_duration = item["contentDetails"].get("duration", "")
            details[vid] = {
                "title": item.get("snippet", {}).get("title", ""),
                "viewCount": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "duration": parse_duration(raw_duration),
                "duration_seconds": parse_duration_seconds(raw_duration),
                "date": item.get("snippet", {}).get("publishedAt", "")[:10],
                "isLive": "liveStreamingDetails" in item,
                "description": item.get("snippet", {}).get("description", ""),
                "audioLanguage": item.get("snippet", {}).get("defaultAudioLanguage", ""),
                "defaultLanguage": item.get("snippet", {}).get("defaultLanguage", ""),
            }

    return details


def get_channel_details(channel_ids: list[str]):
    """Fetch channel statistics and metadata for a batch of IDs and handles."""
    client = _build_client()
    details = {}

    # Separate IDs (UC...) from handles (@...)
    ids = [cid for cid in channel_ids if cid.startswith("UC")]
    handles = [cid for cid in channel_ids if not cid.startswith("UC")]

    # 1. Fetch IDs in batches
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        request = client.channels().list(part="statistics,snippet", id=",".join(batch))
        response = request.execute()
        for item in response.get("items", []):
            cid = item["id"]
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            details[cid] = {
                "title": snippet.get("title", ""),
                "subscriberCount": int(stats.get("subscriberCount", 0)),
                "viewCount": int(stats.get("viewCount", 0)), # Total channel views
                "channelUrl": f"https://www.youtube.com/channel/{cid}",
                "description": snippet.get("description", ""),
                "country": snippet.get("country", ""), 
            }

    # 2. Fetch Handles one by one (API doesn't support batch forHandle)
    # We use a small amount of concurrency to speed this up if there are many handles
    for handle in handles:
        clean_handle = handle[1:] if handle.startswith("@") else handle
        try:
            request = client.channels().list(part="statistics,snippet", forHandle=clean_handle)
            response = request.execute()
            items = response.get("items", [])
            if items:
                item = items[0]
                cid = item["id"]
                stats = item.get("statistics", {})
                snippet = item.get("snippet", {})
                details[handle] = {
                    "id": cid, # Real ID
                    "title": snippet.get("title", ""),
                    "subscriberCount": int(stats.get("subscriberCount", 0)),
                    "viewCount": int(stats.get("viewCount", 0)),
                    "channelUrl": f"https://www.youtube.com/channel/{cid}",
                    "description": snippet.get("description", ""),
                    "country": snippet.get("country", ""), 
                }
        except Exception:
            continue

    return details


def get_recent_videos(channel_id: str, max_results: int = 20):
    """Fetch descriptions of the most recent videos for a channel."""
    client = _build_client()
    try:
        # First get the 'uploads' playlist ID
        ch_request = client.channels().list(part="contentDetails,snippet", id=channel_id)
        ch_response = ch_request.execute()
        
        items = ch_response.get("items", [])
        if not items:
            return []
            
        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        full_description = items[0]["snippet"].get("description", "")
        
        # Then get the items in that playlist
        pl_request = client.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=max_results
        )
        pl_response = pl_request.execute()
        
        videos = []
        # First "video" is actually a container for the channel description to ensure it's scanned
        videos.append({
            "title": "CHANNEL_DESCRIPTION",
            "description": full_description,
            "publishedAt": "",
            "videoId": "CD"
        })

        for item in pl_response.get("items", []):
            snippet = item.get("snippet", {})
            videos.append({
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "publishedAt": snippet.get("publishedAt", ""),
                "videoId": snippet.get("resourceId", {}).get("videoId")
            })
        return videos
    except Exception:
        return []

def get_full_channel_description(channel_id: str) -> str:
    """Fetch the full channel description via API if not provided."""
    client = _build_client()
    try:
        request = client.channels().list(part="snippet", id=channel_id)
        response = request.execute()
        items = response.get("items", [])
        return items[0]["snippet"].get("description", "") if items else ""
    except Exception:
        return ""


def is_strictly_rejected(title: str, description: str, channel_title: str, audio_lang: str = "", default_lang: str = "") -> bool:
    """
    Quality Check: Returns True if the content should be UNCONDITIONALLY REJECTED 
    based on language codes, scripts (Devanagari), or strict exclusion keywords.
    """
    full_text = f"{title} {description} {channel_title}".upper()
    
    # 1. Language Code Check (e.g., Hindi, Urdu, etc.)
    audio_lang = (audio_lang or "").lower()
    def_lang = (default_lang or "").lower()
    if any(lang in (audio_lang, def_lang) for lang in ["hi", "ur", "te", "ta", "mr", "bn"]):
        return True
            
    # 2. Script Check (Devanagari for Hindi/Marathi/etc.)
    import re
    if re.search(r"[\u0900-\u097F]", full_text):
        return True

    # 3. Strict Keyword check (HINDI, URDU, etc. from configuration)
    from core.config import YOUTUBE_STRICT_EXCLUSIONS as STRICT_EXCLUSIONS
    for kw in STRICT_EXCLUSIONS:
        kw_up = kw.upper()
        if len(kw_up) <= 4:
            if re.search(rf"\b{re.escape(kw_up)}\b", full_text):
                return True
        elif kw_up in full_text:
            return True

    return False


def filter_results(
    videos: list[dict],
    video_details: dict,
    channel_details: dict,
    min_views: int,
    max_views: int,
    min_subs: int,
    max_subs: int,
    region_req: str,
    video_type: str = "All",
    search_keyword: str = "",
    on_log = None,
) -> list[dict]:
    """Merge and filter video + channel data."""
    results = []
    seen_channels = set()

    # Track rejection reasons for logging
    rejections = {
        "isLive": 0,
        "videoType": 0,
        "exclusionKeyword": 0,
        "viewCount": 0,
        "subscriberCount": 0,
        "country": 0,
        "language": 0,
    }

    for v in videos:
        vid, cid = v["videoId"], v["channelId"]
        if cid in seen_channels: continue

        vd, cd = video_details.get(vid), channel_details.get(cid)
        if not vd or not cd: continue
        
        if vd.get("isLive"):
            rejections["isLive"] += 1
            continue

        dur_s = vd.get("duration_seconds", 0)
        
        # 1. Advanced Duration Policy
        # If user wants "Long" videos, we apply stricter thresholds for authority content
        if video_type == "Long":
            # Determine if this search is for "authority" content
            is_authority_related = any(kw in search_keyword.upper() for kw in AUTHORITY_KEYWORDS)
            
            min_dur = AUTHORITY_MIN_DUR if is_authority_related else LONG_MIN_DUR
            
            if dur_s < min_dur:
                rejections["videoType"] += 1
                continue
        elif video_type == "Shorts" and dur_s > 60:
            rejections["videoType"] += 1
            continue

        # 2. Broad Exclusion Filter
        # Use full details for better filtering if available
        full_title = vd.get("title", v["title"])
        full_desc = vd.get("description", v["description"])
        full_text = f"{full_title} {full_desc} {v['channelTitle']}".upper()
        channel_name_up = v['channelTitle'].upper()
        
        # QUALITY CHECK: Unconditional Strict Rejections (e.g., specific foreign languages)
        if is_strictly_rejected(
            vd.get("title", v["title"]),
            vd.get("description", v["description"]),
            v["channelTitle"],
            vd.get("audioLanguage"),
            vd.get("defaultLanguage")
        ):
            rejections["language"] += 1
            continue

        # QUALITY CHECK: Niche Relevance
        # Allow channels to bypass generic junk filters if they strongly match the user's explicit search keyword, or general authority keywords.
        kw_upper = search_keyword.upper()
        # Create tokens for the user's strict search string to act as priority keywords
        user_kws = [word for word in kw_upper.split() if len(word) > 3]
        is_priority_match = any(x in full_text for x in PRIORITY_KEYWORDS) or (
            any(ukw in full_text for ukw in user_kws) if user_kws else (kw_upper in full_text)
        )
        
        # QUALITY CHECK: Clip Channel Detection
        # If searching for "Long" form, we avoid channels that brand themselves as "CLIPS", "SHORTS", etc.
        if video_type == "Long" and not is_priority_match:
            if any(ckw in channel_name_up for ckw in CHANNEL_EXCLUSION_KEYWORDS):
                rejections["exclusionKeyword"] += 1
                continue

        # If it's not a priority match AND it contains 'SHORTS' in the text, we reject it
        if "SHORTS" in full_text and video_type == "Long" and not is_priority_match:
            rejections["exclusionKeyword"] += 1
            continue

        if not is_priority_match:
            # Only apply strict exclusions if it's not a direct priority match
            found_exclusion = False
            for kw in EXCLUSION_KEYWORDS:
                kw_up = kw.upper()
                # Check for word boundaries on short or ambiguous keywords (<= 4 chars)
                if len(kw_up) <= 4:
                    if re.search(rf"\b{re.escape(kw_up)}\b", full_text):
                        found_exclusion = True; break
                elif kw_up in full_text:
                    # Special case for "EDIT" to avoid "CREDIT" or "EDITOR"
                    if kw_up == "EDIT" and "CREDIT" in full_text and "EDIT" not in full_text.replace("CREDIT", ""):
                        continue
                    found_exclusion = True; break
            if found_exclusion:
                rejections["exclusionKeyword"] += 1
                continue

        # 2. View/Sub Counters
        views, subs = vd["viewCount"], cd["subscriberCount"]
        if views < min_views or (max_views and views > max_views):
            rejections["viewCount"] += 1
            continue
        if subs < min_subs or (max_subs and subs > max_subs):
            rejections["subscriberCount"] += 1
            continue

        # 3. Enhanced Region Safeguard
        target_allowed = ALLOWED_COUNTRIES_BY_REGION.get(region_req, ALLOWED_COUNTRIES_BY_REGION["US"])
        channel_country = (cd.get("country") or "").strip().upper()
        
        if channel_country:
            # If region_req is not "Both", we use the specific allowed list (US or UK).
            # If region_req is "Both", we use BOTH_REGION_SEQUENCE countries (US, GB).
            if channel_country not in target_allowed:
                rejections["country"] += 1
                continue
        # If no country is specified for the channel, we fallback to the search region's code
        # which is already captured in v["region"] and handled in the output dict.
        
        seen_channels.add(cid)
        results.append({
            "title": v["title"], "id": vid, "channelId": cid, "viewCount": views,
            "date": v["publishedAt"], "likes": vd["likes"], "duration": vd["duration"],
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channelName": v["channelTitle"], "channelUrl": cd["channelUrl"],
            "numberOfSubscribers": subs,
            "Country": "UK" if cd.get("country") == "GB" else (cd.get("country") or v["region"]),
            "channelDescription": cd.get("description", ""), 
            "videoDescription": vd.get("description", ""),
            "EMAIL": v.get("EMAIL", "nil"),
        })

    if on_log and any(rejections.values()):
        reason_strs = [f"{k}: {v}" for k, v in rejections.items() if v > 0]
        on_log(f"  [filter] Rejections in this batch: {', '.join(reason_strs)}")

    return results
