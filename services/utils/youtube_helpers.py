import re
from datetime import datetime, timedelta
from core.config import (
    YOUTUBE_DEFAULT_REGION,
    YOUTUBE_REGION_MAP as REGION_MAP
)

def normalize_region_code(region: str) -> str:
    """Map UI region values to YouTube API-compatible region codes."""
    return REGION_MAP.get((region or "").upper(), YOUTUBE_DEFAULT_REGION)


def date_filter_to_rfc3339(date_filter: str) -> str:
    """Convert a human-friendly date filter to an RFC 3339 timestamp."""
    now = datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week = start_of_day - timedelta(days=start_of_day.weekday())
    if start_of_day.month == 1:
        start_of_last_month = start_of_day.replace(year=start_of_day.year - 1, month=12, day=1)
    else:
        start_of_last_month = start_of_day.replace(month=start_of_day.month - 1, day=1)

    mapping = {
        "Today": start_of_day,
        "This Week": start_of_week,
        "Last Month": start_of_last_month,
        "This Year": start_of_day.replace(month=1, day=1),
    }
    if date_filter not in mapping:
        raise ValueError(f"Unsupported date filter: {date_filter!r}")
    dt = mapping[date_filter]
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration(iso_duration: str) -> str:
    """Convert ISO 8601 duration (PT12M34S) to human readable (12:34)."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not match:
        return "0:00"
    h, m, s = (int(x) if x else 0 for x in match.groups())
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_duration_seconds(iso_duration: str) -> int:
    """Convert ISO 8601 duration to total seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not match:
        return 0
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s
