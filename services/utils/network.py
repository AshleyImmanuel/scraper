import socket
import threading
from urllib.parse import urlparse
from ipaddress import ip_address
from time import monotonic
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import os

# Configuration from environment (or defaults)
DNS_RESOLVE_TIMEOUT_MS = int(os.getenv("SCRAPER_DNS_RESOLVE_TIMEOUT_MS", "750"))
DNS_CACHE_TTL_SECONDS = int(os.getenv("SCRAPER_DNS_CACHE_TTL_SECONDS", "300"))
DNS_FAILURE_CACHE_TTL_SECONDS = int(os.getenv("SCRAPER_DNS_FAILURE_CACHE_TTL_SECONDS", "30"))

_DNS_SAFETY_CACHE: dict[str, tuple[float, bool]] = {}
_DNS_SAFETY_CACHE_LOCK = threading.Lock()
_DNS_RESOLVER = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scraper-dns")


def _resolve_host_addresses(host: str) -> set[str]:
    """Helper to resolve host addresses using socket."""
    try:
        infos = socket.getaddrinfo(
            host,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
        return {
            sockaddr[0]
            for _, _, _, _, sockaddr in infos
            if sockaddr and sockaddr[0]
        }
    except Exception:
        return set()


def _is_public_hostname(host: str) -> bool:
    """Check if a hostname resolves to public IP addresses with caching."""
    now = monotonic()
    with _DNS_SAFETY_CACHE_LOCK:
        cached = _DNS_SAFETY_CACHE.get(host)
        if cached and cached[0] > now:
            return cached[1]
        if cached:
            _DNS_SAFETY_CACHE.pop(host, None)

    try:
        future = _DNS_RESOLVER.submit(_resolve_host_addresses, host)
        addresses = future.result(timeout=DNS_RESOLVE_TIMEOUT_MS / 1000)
        is_safe = bool(addresses) and all(ip_address(addr).is_global for addr in addresses)
        ttl_seconds = DNS_CACHE_TTL_SECONDS if is_safe else DNS_FAILURE_CACHE_TTL_SECONDS
    except (FutureTimeoutError, OSError, ValueError):
        is_safe = False
        ttl_seconds = DNS_FAILURE_CACHE_TTL_SECONDS
        if "future" in locals():
            future.cancel()

    with _DNS_SAFETY_CACHE_LOCK:
        _DNS_SAFETY_CACHE[host] = (now + ttl_seconds, is_safe)
    return is_safe


def is_safe_external_url(url: str) -> bool:
    """Allow only public http(s) URLs for external-link scraping."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False

    if host == "localhost" or host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan"):
        return False

    try:
        addr = ip_address(host)
        return addr.is_global
    except ValueError:
        return _is_public_hostname(host)
