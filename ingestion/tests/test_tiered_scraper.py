"""Tests for ingestion/tiered_scraper.py — Tier 1 (Shopify .json) / Tier 2
(hardened HTML scrape) composition and request-count guarantees.

All tests use use_llm=False — this module tests network-layer tier
composition (how many requests, which tier wins), not LLM extraction
quality (covered in test_product_page_analyzer.py / test_shopify_json.py).
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from ingestion.rate_limiter import DomainRateLimiterRegistry
from ingestion.tiered_scraper import scrape_and_extract
from pipeline.config import Settings

_JSON_LD_HTML = """
<head>
<script type="application/ld+json">
{"@type": "Product", "name": "JSON-LD Product", "offers": {"price": "24.99", "priceCurrency": "USD"}}
</script>
</head>
"""


def _fast_registry() -> DomainRateLimiterRegistry:
    return DomainRateLimiterRegistry(
        global_rate=1000.0, global_capacity=1000.0, per_domain_rate=1000.0, per_domain_capacity=1000.0
    )


def _fast_settings() -> Settings:
    return Settings(scrape_max_attempts=3, scrape_backoff_min_seconds=0.01, scrape_backoff_max_seconds=0.02)


def _patched_settings():
    """Patch get_settings everywhere it's imported by the modules under test."""
    return (
        patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()),
        patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()),
    )


class TestLiteralProductPath:
    def test_tier1_success_single_request(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            assert str(request.url).endswith(".json")
            return httpx.Response(200, json={"product": {"title": "Direct Hit"}})

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://store.com/products/handle",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is not None
        assert result.extraction_method == "shopify_json"
        assert len(calls) == 1  # only the .json fetch, no HTML request at all

    def test_tier1_404_falls_through_to_tier2_with_fresh_request(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if str(request.url).endswith(".json"):
                return httpx.Response(404)
            return httpx.Response(200, text=_JSON_LD_HTML)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://headless-store.com/products/handle",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is not None
        assert result.extraction_method == "structured_data"
        assert result.product_name == "JSON-LD Product"
        assert len(calls) == 2  # .json (404) + fresh HTML GET


class TestRedirectResolution:
    def test_redirect_to_product_path_tier1_success(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            calls.append(url)
            if url == "https://tracker.example.com/click/abc123":
                return httpx.Response(302, headers={"Location": "https://store.com/products/real-handle"})
            if url.endswith(".json"):
                return httpx.Response(200, json={"product": {"title": "Resolved Product"}})
            return httpx.Response(200, text="<html>landing page</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://tracker.example.com/click/abc123",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is not None
        assert result.extraction_method == "shopify_json"
        assert result.product_name == "Resolved Product"
        # redirect hop + resolved landing page + .json fetch
        assert calls[-1].endswith(".json")

    def test_redirect_tier1_404_reuses_already_fetched_html_no_extra_request(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            calls.append(url)
            if url == "https://tracker.example.com/click/xyz":
                return httpx.Response(302, headers={"Location": "https://headless.com/products/real-handle"})
            if url.endswith(".json"):
                return httpx.Response(404)
            return httpx.Response(200, text=_JSON_LD_HTML)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://tracker.example.com/click/xyz",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is not None
        assert result.extraction_method == "structured_data"
        assert result.product_name == "JSON-LD Product"
        # redirect + landing page + .json(404) = 3 total; landing page NOT re-fetched for tier 2
        landing_page_fetches = [c for c in calls if not c.endswith(".json") and "tracker" not in c]
        assert len(landing_page_fetches) == 1

    def test_non_product_final_path_never_attempts_tier1(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            calls.append(url)
            assert not url.endswith(".json"), "Tier 1 should never be attempted for a non-product path"
            if url == "https://tracker.example.com/click/nowhere":
                return httpx.Response(302, headers={"Location": "https://store.com/pages/about-us"})
            return httpx.Response(200, text=_JSON_LD_HTML)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://tracker.example.com/click/nowhere",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is not None
        assert result.extraction_method == "structured_data"
        assert len(calls) == 2  # redirect + landing page only, no .json attempt


class TestBothTiersFail:
    def test_returns_none_when_both_tiers_fail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith(".json"):
                return httpx.Response(404)
            return httpx.Response(200, text="<html><body>nothing structured here</body></html>")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://empty-store.com/products/handle",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is None

    def test_returns_none_when_resolve_fetch_fails_entirely(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        p1, p2 = _patched_settings()
        with p1, p2:
            result = scrape_and_extract(
                "https://down-store.com/some-page",
                client=client,
                rate_limiter=_fast_registry(),
                use_llm=False,
            )
        assert result is None
