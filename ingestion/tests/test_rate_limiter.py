"""Tests for ingestion/rate_limiter.py."""

from __future__ import annotations

import threading
from unittest.mock import patch

from ingestion.rate_limiter import DomainRateLimiterRegistry, TokenBucket


class FakeClock:
    """Deterministic, manually-advanced clock for TokenBucket tests. Passed
    directly into TokenBucket's injectable now/sleep params — never patches
    the global `time` module (that's risky: other machinery, including
    pytest/threading internals, relies on real monotonic time; a global
    patch caused a real test hang during development)."""

    def __init__(self, start: float = 0.0):
        self.now = start
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


class TestTokenBucket:
    def test_allows_burst_up_to_capacity_without_sleeping(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=1.0, capacity=3.0, now=clock.monotonic, sleep=clock.sleep)
        bucket.acquire()
        bucket.acquire()
        bucket.acquire()
        assert clock.sleep_calls == []

    def test_blocks_and_sleeps_when_empty(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=1.0, capacity=1.0, now=clock.monotonic, sleep=clock.sleep)
        bucket.acquire()  # drains the single token, no sleep
        bucket.acquire()  # must wait ~1s for refill
        assert clock.sleep_calls == [1.0]

    def test_refills_over_time(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate_per_sec=2.0, capacity=2.0, now=clock.monotonic, sleep=clock.sleep)
        bucket.acquire()
        bucket.acquire()  # bucket now empty
        clock.now += 1.0  # 1s at 2/s = 2 tokens refilled
        bucket.acquire()
        bucket.acquire()
        assert clock.sleep_calls == []


class TestDomainRateLimiterRegistry:
    def _fast_registry(self) -> DomainRateLimiterRegistry:
        # High rate/capacity so acquire() never actually blocks in tests
        # (real time.monotonic/time.sleep — fine at this scale, completes
        # in well under a second).
        return DomainRateLimiterRegistry(
            global_rate=1000.0, global_capacity=1000.0, per_domain_rate=1000.0, per_domain_capacity=1000.0
        )

    def test_acquire_hits_both_global_and_domain_bucket_by_default(self) -> None:
        registry = self._fast_registry()
        with patch.object(
            registry, "_global_bucket", wraps=registry._global_bucket
        ) as spy_global:
            registry.acquire("unknown-store.com")
            spy_global.acquire.assert_called_once()

    def test_confirmed_non_shopify_domain_skips_global_bucket(self) -> None:
        registry = self._fast_registry()
        registry.record_response(
            "independent-store.com", headers={"content-type": "text/html"}, body_snippet="<html>hi</html>"
        )
        with patch.object(
            registry, "_global_bucket", wraps=registry._global_bucket
        ) as spy_global:
            registry.acquire("independent-store.com")
            spy_global.acquire.assert_not_called()

    def test_shopify_header_prevents_demotion(self) -> None:
        registry = self._fast_registry()
        registry.record_response(
            "shopify-store.com", headers={"X-ShopId": "12345"}, body_snippet="<html>hi</html>"
        )
        with patch.object(
            registry, "_global_bucket", wraps=registry._global_bucket
        ) as spy_global:
            registry.acquire("shopify-store.com")
            spy_global.acquire.assert_called_once()

    def test_shopify_cdn_marker_in_body_prevents_demotion(self) -> None:
        registry = self._fast_registry()
        registry.record_response(
            "shopify-store.com",
            headers={"content-type": "text/html"},
            body_snippet='<link rel="preconnect" href="https://cdn.shopify.com">',
        )
        with patch.object(
            registry, "_global_bucket", wraps=registry._global_bucket
        ) as spy_global:
            registry.acquire("shopify-store.com")
            spy_global.acquire.assert_called_once()

    def test_never_touched_domain_still_pays_global_bucket(self) -> None:
        registry = self._fast_registry()
        with patch.object(
            registry, "_global_bucket", wraps=registry._global_bucket
        ) as spy_global:
            registry.acquire("never-touched.com")
            spy_global.acquire.assert_called_once()

    def test_per_domain_bucket_is_shared_across_calls_to_same_domain(self) -> None:
        registry = self._fast_registry()
        bucket1 = registry._get_domain_bucket("store.com")
        bucket2 = registry._get_domain_bucket("store.com")
        assert bucket1 is bucket2

    def test_concurrent_acquires_do_not_raise(self) -> None:
        """Thread-safety smoke test — real timing, tiny numbers, fast."""
        registry = DomainRateLimiterRegistry(
            global_rate=500.0, global_capacity=50.0, per_domain_rate=500.0, per_domain_capacity=50.0
        )
        errors: list[Exception] = []

        def worker(domain: str) -> None:
            try:
                for _ in range(5):
                    registry.acquire(domain)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"store{i % 3}.com",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
