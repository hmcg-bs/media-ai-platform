"""Process-wide rate limiting for landing-page scraping.

Built after a full-corpus scrape run (2,736 requests, 15 concurrent workers)
tripped Shopify's shared, platform-level anti-abuse system: 1,669 HTTP 429s
and 127 403s, spread across many *unrelated* Shopify-hosted domains that
failed together in the same burst window. Plain ``curl`` succeeded against
the same URLs minutes later with zero special handling — a volume/burst
signature, not per-site or TLS-fingerprint blocking. A per-domain limiter
alone would not have prevented this (591 unique domains in the corpus, most
touched once or twice); the constraint is the shared platform budget.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    """Thread-safe token bucket. Blocking acquire, lazy refill (no background thread).

    `now`/`sleep` are injectable (default to time.monotonic/time.sleep) so
    tests can supply a fake clock without monkeypatching the global `time`
    module — patching `time.monotonic`/`time.sleep` process-wide is risky
    (other machinery, including pytest/threading internals, relies on real
    monotonic time) and caused a real test hang during development.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._rate = rate_per_sec
        self._capacity = capacity
        self._tokens = capacity
        self._now = now
        self._sleep = sleep
        self._last_refill = now()
        self._lock = threading.Lock()

    def acquire(self, amount: float = 1.0) -> None:
        """Block until `amount` tokens are available, then consume them."""
        while True:
            with self._lock:
                now = self._now()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last_refill = now

                if self._tokens >= amount:
                    self._tokens -= amount
                    return

                deficit = amount - self._tokens
                wait_s = deficit / self._rate

            self._sleep(wait_s)


class DomainRateLimiterRegistry:
    """Global + per-domain token buckets shared across all scraping workers.

    Every request pays into the global bucket by default — we can't know a
    domain is Shopify-hosted before touching it, and most domains in this
    corpus are touched only once or twice anyway, so pre-classification
    wouldn't help. A domain is exempted from the global bucket only after a
    *confirmed* non-Shopify response; failed/blocked responses carry no
    evidence either way and never demote.
    """

    def __init__(
        self,
        global_rate: float,
        global_capacity: float,
        per_domain_rate: float,
        per_domain_capacity: float,
    ):
        self._global_rate = global_rate
        self._global_capacity = global_capacity
        self._per_domain_rate = per_domain_rate
        self._per_domain_capacity = per_domain_capacity

        self._global_bucket = TokenBucket(global_rate, global_capacity)
        self._domain_buckets: dict[str, TokenBucket] = {}
        self._non_shopify_domains: set[str] = set()
        self._lock = threading.Lock()

    def _get_domain_bucket(self, domain: str) -> TokenBucket:
        with self._lock:
            bucket = self._domain_buckets.get(domain)
            if bucket is None:
                bucket = TokenBucket(self._per_domain_rate, self._per_domain_capacity)
                self._domain_buckets[domain] = bucket
            return bucket

    def acquire(self, domain: str) -> None:
        """Acquire rate-limit budget for a request to `domain`.

        Call once per outbound HTTP request, including retries — retries
        re-acquire so pacing and backoff compose instead of racing.
        """
        self._get_domain_bucket(domain).acquire()
        with self._lock:
            is_confirmed_non_shopify = domain in self._non_shopify_domains
        if not is_confirmed_non_shopify:
            self._global_bucket.acquire()

    def record_response(self, domain: str, headers: dict[str, str], body_snippet: str) -> None:
        """Classify `domain` as confirmed-non-Shopify if no Shopify signal is
        present in a *successful* response. Never call this for a
        failed/blocked response — those carry no evidence either way.
        """
        lower_headers = {k.lower(): v for k, v in headers.items()}
        has_shopify_header = any(
            key in lower_headers for key in ("x-shopid", "x-sorting-hat-podid", "x-shardid")
        )
        has_shopify_marker = "cdn.shopify.com" in body_snippet or "Shopify.theme" in body_snippet

        if not has_shopify_header and not has_shopify_marker:
            with self._lock:
                self._non_shopify_domains.add(domain)


_registry: DomainRateLimiterRegistry | None = None
_registry_lock = threading.Lock()


def get_rate_limiter_registry() -> DomainRateLimiterRegistry:
    """Process-wide singleton so every ThreadPoolExecutor worker shares the
    same buckets. NOTE: state is only shared within one process — if this is
    ever parallelized across processes, buckets would not be shared and the
    global limit would be violated per-process. Out of scope for the current
    single-process ThreadPoolExecutor runner.
    """
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                from pipeline.config import get_settings

                settings = get_settings()
                _registry = DomainRateLimiterRegistry(
                    global_rate=settings.shopify_global_rps,
                    global_capacity=settings.shopify_global_burst,
                    per_domain_rate=settings.per_domain_rps,
                    per_domain_capacity=settings.per_domain_burst,
                )
    return _registry


def reset_rate_limiter_registry() -> None:
    """Test helper: clear the singleton so tests get a fresh registry."""
    global _registry
    with _registry_lock:
        _registry = None
