import os
from dotenv import load_dotenv

# Load .env file at the root
# Since this file is in core/, we need to look up one level
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    value = default
    if raw is not None:
        try:
            value = int(raw.strip())
        except (TypeError, ValueError):
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value

def _env_csv(name: str, default_csv: str, convert_to_upper: bool = False) -> list[str]:
    raw = os.getenv(name, default_csv)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if convert_to_upper:
        return [item.upper() for item in items]
    return items

def _env_csv_set(name: str, default_csv: str) -> set[str]:
    raw = os.getenv(name, default_csv)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}

# ---- App Settings ----
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = _env_int("APP_PORT", 8000, minimum=1, maximum=65535)
ENABLE_API_DOCS = _env_flag("ENABLE_API_DOCS", default=False)
TRUST_PROXY_HEADERS = _env_flag("TRUST_PROXY_HEADERS", default=False)
TRUSTED_PROXY_IPS = {
    value.strip()
    for value in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
    if value.strip()
}

# ---- YouTube API Settings ----
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_DEFAULT_REGION = os.getenv("YOUTUBE_DEFAULT_REGION", "US").strip().upper() or "US"
YOUTUBE_RELEVANCE_LANGUAGE = os.getenv("YOUTUBE_RELEVANCE_LANGUAGE", "en").strip() or "en"

# Exclusion Keywords (For filtering edits/montages)
YOUTUBE_EXCLUSION_KEYWORDS = _env_csv(
    "YOUTUBE_EXCLUSION_KEYWORDS", 
    "montage,edit,amv,gmv,phonk,tribute,status,alight motion,capcut,velocity,lyrics,ffx",
    convert_to_upper=True
)

# Region/Country Mapping
# Format: KEY:VALUE,KEY:VALUE
def _env_region_map(name: str, default_map: dict[str, str]) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default_map
    parsed: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        key = key.strip().upper()
        value = value.strip().upper()
        if key and value:
            parsed[key] = value
    return parsed or default_map

YOUTUBE_REGION_MAP = _env_region_map("YOUTUBE_REGION_MAP", {"US": "US", "UK": "GB", "GB": "GB"})

ALLOWED_COUNTRIES_BOTH = _env_csv("YOUTUBE_ALLOWED_COUNTRIES_BOTH", "US,GB", convert_to_upper=True)
ALLOWED_COUNTRIES_US = _env_csv("YOUTUBE_ALLOWED_COUNTRIES_US", "US", convert_to_upper=True)
ALLOWED_COUNTRIES_UK = _env_csv("YOUTUBE_ALLOWED_COUNTRIES_UK", "GB", convert_to_upper=True)

# ---- Scraper Settings ----
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
SCRAPER_MAX_RETRIES = _env_int("SCRAPER_MAX_RETRIES", 2, minimum=1, maximum=6)
SCRAPER_RETRY_DELAY_MS = _env_int("SCRAPER_RETRY_DELAY_MS", 2000, minimum=250, maximum=60000)
SCRAPER_CONCURRENCY = _env_int("SCRAPER_CONCURRENCY", 5, minimum=1, maximum=20)
SCRAPER_EMAIL_BLACKLIST = _env_csv_set(
    "SCRAPER_EMAIL_BLACKLIST",
    "noreply@youtube.com,support@google.com,press@youtube.com,example@example.com,name@example.com,email@example.com,copyright@youtube.com,legal@google.com,abuse@youtube.com"
)

# ---- Timeouts & Wait Times ----
SCRAPER_THROTTLE_MS = _env_int("SCRAPER_THROTTLE_MS", 0, minimum=0, maximum=15000)
ABOUT_TIMEOUT_MS = _env_int("SCRAPER_ABOUT_TIMEOUT_MS", 20000, minimum=5000, maximum=120000)
CHANNEL_TIMEOUT_MS = _env_int("SCRAPER_CHANNEL_TIMEOUT_MS", 15000, minimum=5000, maximum=120000)
EXTERNAL_TIMEOUT_MS = _env_int("SCRAPER_EXTERNAL_TIMEOUT_MS", 10000, minimum=3000, maximum=60000)
ABOUT_POST_LOAD_WAIT_MS = _env_int("SCRAPER_ABOUT_POST_LOAD_WAIT_MS", 2000, minimum=0, maximum=15000)
CONSENT_CLICK_TIMEOUT_MS = _env_int("SCRAPER_CONSENT_CLICK_TIMEOUT_MS", 3000, minimum=500, maximum=20000)
CONSENT_POST_CLICK_WAIT_MS = _env_int("SCRAPER_CONSENT_POST_CLICK_WAIT_MS", 2000, minimum=0, maximum=15000)
VIEW_EMAIL_CLICK_TIMEOUT_MS = _env_int("SCRAPER_VIEW_EMAIL_CLICK_TIMEOUT_MS", 3000, minimum=500, maximum=20000)
VIEW_EMAIL_POST_CLICK_WAIT_MS = _env_int("SCRAPER_VIEW_EMAIL_POST_CLICK_WAIT_MS", 2000, minimum=0, maximum=15000)
CHANNEL_POST_LOAD_WAIT_MS = _env_int("SCRAPER_CHANNEL_POST_LOAD_WAIT_MS", 1500, minimum=0, maximum=15000)
EXTERNAL_POST_LOAD_WAIT_MS = _env_int("SCRAPER_EXTERNAL_POST_LOAD_WAIT_MS", 1000, minimum=0, maximum=15000)

# ---- Extraction Job Constraints ----
MAX_EXTRACT_BODY_BYTES = _env_int("MAX_EXTRACT_BODY_BYTES", 10000, minimum=1000)
MAX_CONCURRENT_JOBS = _env_int("MAX_CONCURRENT_JOBS", 2, minimum=1)
JOB_RETENTION_SECONDS = _env_int("JOB_RETENTION_SECONDS", 21600, minimum=300)
MAX_STORED_JOBS = _env_int("MAX_STORED_JOBS", 200, minimum=20)
MAX_JOB_LOG_LINES = _env_int("MAX_JOB_LOG_LINES", 400, minimum=50)
MAX_KEYWORDS_PER_JOB = _env_int("MAX_KEYWORDS_PER_JOB", 10, minimum=1, maximum=50)
MAX_API_FETCHES = _env_int("MAX_API_FETCHES", 100, minimum=1, maximum=500)
MAX_STALE_BATCHES = _env_int("MAX_STALE_BATCHES", 8, minimum=1, maximum=100)
MIN_MATCH_TARGET_ABSOLUTE = _env_int("MIN_MATCH_TARGET_ABSOLUTE", 20, minimum=1)
MIN_MATCH_TARGET_DIVISOR = _env_int("MIN_MATCH_TARGET_DIVISOR", 10, minimum=1)

# ---- Rate Limiting Settings ----
RATE_LIMIT_CLEANUP_INTERVAL_SECONDS = _env_int("RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", 30, minimum=5)
RATE_LIMIT_MAX_KEYS = _env_int("RATE_LIMIT_MAX_KEYS", 5000, minimum=100)
RATE_LIMIT_EXTRACT_PER_MIN = _env_int("RATE_LIMIT_EXTRACT_PER_MIN", 6, minimum=1)
RATE_LIMIT_STATUS_PER_MIN = _env_int("RATE_LIMIT_STATUS_PER_MIN", 240, minimum=10)
RATE_LIMIT_DOWNLOAD_PER_MIN = _env_int("RATE_LIMIT_DOWNLOAD_PER_MIN", 30, minimum=1)

# ---- Region Logic ----
BOTH_REGION_SEQUENCE = _env_csv("BOTH_REGION_SEQUENCE", "US,UK", convert_to_upper=True)

# ---- Output Settings ----
EXCEL_OUTPUT_COLUMNS = _env_csv(
    "EXCEL_OUTPUT_COLUMNS",
    "title,id,viewCount,likes,duration,date,channelName,url,channelUrl,numberOfSubscribers,EMAIL,Country"
)
