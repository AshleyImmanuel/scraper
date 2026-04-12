import asyncio
from collections import deque
from time import monotonic
from ipaddress import ip_address
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from core.config import (
    RATE_LIMIT_CLEANUP_INTERVAL_SECONDS,
    RATE_LIMIT_MAX_KEYS,
    RATE_LIMIT_EXTRACT_PER_MIN,
    RATE_LIMIT_STATUS_PER_MIN,
    RATE_LIMIT_DOWNLOAD_PER_MIN,
    TRUST_PROXY_HEADERS,
    TRUSTED_PROXY_IPS,
    MAX_EXTRACT_BODY_BYTES
)

# ---- Rate Limiting State ----
RATE_LIMIT_RULES = [
    {"key": "extract", "path_prefix": "/api/extract", "limit": RATE_LIMIT_EXTRACT_PER_MIN, "window_seconds": 60},
    {"key": "status", "path_prefix": "/api/status/", "limit": RATE_LIMIT_STATUS_PER_MIN, "window_seconds": 60},
    {"key": "download", "path_prefix": "/api/download/", "limit": RATE_LIMIT_DOWNLOAD_PER_MIN, "window_seconds": 60},
]

_rate_limit_hits: dict[tuple[str, str], deque[float]] = {}
_rate_limit_last_seen: dict[tuple[str, str], float] = {}
_last_rate_limit_cleanup_at = 0.0
_rate_limit_lock = asyncio.Lock()

class RequestBodyTooLarge(Exception):
    """Raised when an incoming extract request exceeds the configured body size."""
    pass

def _safe_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip()
    try:
        return str(ip_address(candidate))
    except ValueError:
        return None

def _is_trusted_proxy(client_host: str | None) -> bool:
    if not TRUST_PROXY_HEADERS:
        return False
    parsed = _safe_ip(client_host)
    if not parsed:
        return False
    if not TRUSTED_PROXY_IPS:
        return True
    return parsed in TRUSTED_PROXY_IPS

def get_client_ip(request: Request) -> str:
    client_host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    real_ip_header = request.headers.get("x-real-ip")

    if _is_trusted_proxy(client_host):
        if forwarded:
            forwarded_ip = _safe_ip(forwarded.split(",")[0])
            if forwarded_ip:
                return forwarded_ip
        real_ip = _safe_ip(real_ip_header)
        if real_ip:
            return real_ip

    parsed_client = _safe_ip(client_host)
    if parsed_client:
        return parsed_client
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

def _match_rate_limit_rule(path: str):
    for rule in RATE_LIMIT_RULES:
        prefix = rule["path_prefix"]
        if path == prefix or path.startswith(prefix):
            return rule
    return None

def apply_security_headers(response: Response, path: str) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'")
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
    return response

async def enforce_request_body_limit(request: Request, limit_bytes: int) -> None:
    original_receive = request._receive
    received_bytes = 0

    async def limited_receive():
        nonlocal received_bytes
        message = await original_receive()
        if message["type"] != "http.request":
            return message

        received_bytes += len(message.get("body", b""))
        if received_bytes > limit_bytes:
            raise RequestBodyTooLarge
        return message

    request._receive = limited_receive

def _prune_rate_limit_state(now: float):
    global _last_rate_limit_cleanup_at
    if (now - _last_rate_limit_cleanup_at) < RATE_LIMIT_CLEANUP_INTERVAL_SECONDS:
        return
    _last_rate_limit_cleanup_at = now

    max_window = max(int(rule["window_seconds"]) for rule in RATE_LIMIT_RULES)
    stale_cutoff = now - max_window

    for key, hits in list(_rate_limit_hits.items()):
        while hits and hits[0] <= stale_cutoff:
            hits.popleft()
        if not hits:
            _rate_limit_hits.pop(key, None)
            _rate_limit_last_seen.pop(key, None)

    if len(_rate_limit_hits) > RATE_LIMIT_MAX_KEYS:
        overflow = len(_rate_limit_hits) - RATE_LIMIT_MAX_KEYS
        oldest_keys = sorted(
            _rate_limit_last_seen.items(),
            key=lambda item: item[1],
        )[:overflow]
        for old_key, _ in oldest_keys:
            _rate_limit_hits.pop(old_key, None)
            _rate_limit_last_seen.pop(old_key, None)

async def rate_limit_middleware_logic(request: Request, call_next):
    path = request.url.path

    if path == "/api/extract" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_EXTRACT_BODY_BYTES:
            return JSONResponse(status_code=413, content={"error": "Request payload too large."})
        await enforce_request_body_limit(request, MAX_EXTRACT_BODY_BYTES)

    if request.method == "OPTIONS":
        response = await call_next(request)
        return apply_security_headers(response, path)

    rule = _match_rate_limit_rule(path)
    if not rule:
        response = await call_next(request)
        return apply_security_headers(response, path)

    now = monotonic()
    client_ip = get_client_ip(request)
    key = (client_ip, rule["key"])
    limit = int(rule["limit"])
    window_seconds = int(rule["window_seconds"])

    retry_after: int | None = None

    async with _rate_limit_lock:
        _prune_rate_limit_state(now)

        if key not in _rate_limit_hits and len(_rate_limit_hits) >= RATE_LIMIT_MAX_KEYS:
            retry_after = 5
        else:
            hits = _rate_limit_hits.setdefault(key, deque())
            _rate_limit_last_seen[key] = now
            window_start = now - window_seconds

            while hits and hits[0] <= window_start:
                hits.popleft()

            if len(hits) >= limit:
                oldest = hits[0]
                retry_after = max(1, int(window_seconds - (now - oldest)) + 1)
            else:
                hits.append(now)
                _rate_limit_last_seen[key] = now

    if retry_after is not None:
        return apply_security_headers(
            JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded. Please retry shortly."},
                headers={"Retry-After": str(retry_after)},
            ),
            path
        )

    try:
        response = await call_next(request)
    except RequestBodyTooLarge:
        return apply_security_headers(
            JSONResponse(status_code=413, content={"error": "Request payload too large."}),
            path
        )
    return apply_security_headers(response, path)
