"""Tests for ingestion/shopify_json.py (Tier 1: Shopify .json product API)."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from ingestion.rate_limiter import DomainRateLimiterRegistry
from ingestion.shopify_json import (
    build_json_url,
    fetch_shopify_json,
    has_product_path,
    parse_shopify_product,
)
from pipeline.config import Settings


def _fast_registry() -> DomainRateLimiterRegistry:
    return DomainRateLimiterRegistry(
        global_rate=1000.0, global_capacity=1000.0, per_domain_rate=1000.0, per_domain_capacity=1000.0
    )


def _fast_settings(**overrides) -> Settings:
    defaults = dict(scrape_max_attempts=3, scrape_backoff_min_seconds=0.01, scrape_backoff_max_seconds=0.02)
    defaults.update(overrides)
    return Settings(**defaults)


class TestHasProductPath:
    def test_literal_products_path(self) -> None:
        assert has_product_path("https://store.com/products/handle")

    def test_products_path_with_query(self) -> None:
        assert has_product_path("https://store.com/products/handle?variant=123")

    def test_no_products_path(self) -> None:
        assert not has_product_path("https://store.com/pages/about")

    def test_root_domain(self) -> None:
        assert not has_product_path("https://store.com/")


class TestBuildJsonUrl:
    def test_strips_query_string(self) -> None:
        assert build_json_url("https://store.com/products/handle?variant=1") == "https://store.com/products/handle.json"

    def test_strips_trailing_slash(self) -> None:
        assert build_json_url("https://store.com/products/handle/") == "https://store.com/products/handle.json"

    def test_idempotent_if_already_json(self) -> None:
        assert build_json_url("https://store.com/products/handle.json") == "https://store.com/products/handle.json"


class TestParseShopifyProduct:
    def test_maps_full_product(self) -> None:
        data = {
            "product": {
                "title": "CalmAxis Postbiotic Chews",
                "vendor": "Canis Labs",
                "product_type": "Intelligems Testing v2",
                "tags": "calming, dog-supplement, postbiotic",
                "body_html": "<p>A <b>gut-first</b> calming formula.</p>",
                "variants": [
                    {"title": "30 count", "price": "39.99"},
                    {"title": "60 count", "price": "69.99"},
                ],
            }
        }
        page = parse_shopify_product(data, "https://canislabs.com/products/calmaxis-chews")
        assert page is not None
        assert page.product_name == "CalmAxis Postbiotic Chews"
        assert page.brand_name == "Canis Labs"
        assert page.cultural_branding == ["calming", "dog-supplement", "postbiotic"]
        assert page.price == 39.99
        assert page.price_range == "$39.99-$69.99"
        assert "gut-first calming formula" in page.marketing_copy
        assert page.shows_all_variants is True
        assert page.extraction_method == "shopify_json"
        assert page.confidence == 0.9

    def test_single_variant_no_price_range(self) -> None:
        data = {"product": {"title": "Solo Product", "variants": [{"price": "19.99"}]}}
        page = parse_shopify_product(data, "https://store.com/products/solo")
        assert page is not None
        assert page.price == 19.99
        assert page.price_range == ""
        assert page.shows_all_variants is False

    def test_missing_title_returns_none(self) -> None:
        data = {"product": {"vendor": "NoName"}}
        assert parse_shopify_product(data, "https://store.com/products/x") is None

    def test_missing_product_key_returns_none(self) -> None:
        assert parse_shopify_product({}, "https://store.com/products/x") is None

    def test_malformed_variant_prices_ignored(self) -> None:
        data = {
            "product": {
                "title": "Product",
                "variants": [{"price": "not-a-number"}, {"price": "9.99"}],
            }
        }
        page = parse_shopify_product(data, "https://store.com/products/x")
        assert page is not None
        assert page.price == 9.99

    def test_default_title_variant_excluded_from_variants_featured(self) -> None:
        data = {"product": {"title": "Product", "variants": [{"title": "Default Title", "price": "9.99"}]}}
        page = parse_shopify_product(data, "https://store.com/products/x")
        assert page is not None
        assert page.variants_featured == []


class TestFetchShopifyJson:
    def test_success_returns_parsed_dict(self) -> None:
        payload = {"product": {"title": "Test Product"}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload, headers={"X-ShopId": "1"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()):
            result = fetch_shopify_json(
                "https://store.com/products/x.json", client, _fast_registry()
            )
        assert result == payload

    def test_404_returns_none_without_retry(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()):
            result = fetch_shopify_json(
                "https://headless-store.com/products/x.json", client, _fast_registry()
            )
        assert result is None
        assert call_count == 1  # not retried

    def test_retries_on_429_then_succeeds(self) -> None:
        payload = {"product": {"title": "Retried Product"}}
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()):
            result = fetch_shopify_json(
                "https://flaky-store.com/products/x.json", client, _fast_registry()
            )
        assert result == payload
        assert call_count == 2

    def test_gives_up_after_max_attempts_on_persistent_429(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings(scrape_max_attempts=2)):
            result = fetch_shopify_json(
                "https://always-throttled.com/products/x.json", client, _fast_registry()
            )
        assert result is None

    def test_non_json_response_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()):
            result = fetch_shopify_json(
                "https://weird-store.com/products/x.json", client, _fast_registry()
            )
        assert result is None

    def test_unexpected_json_shape_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()):
            result = fetch_shopify_json(
                "https://odd-store.com/products/x.json", client, _fast_registry()
            )
        assert result is None

    def test_rate_limiter_acquired_before_request(self) -> None:
        payload = {"product": {"title": "Test"}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        registry = _fast_registry()
        with patch.object(registry, "acquire", wraps=registry.acquire) as spy_acquire:
            with patch("ingestion.shopify_json.get_settings", return_value=_fast_settings()):
                fetch_shopify_json("https://store.com/products/x.json", client, registry)
            spy_acquire.assert_called_once_with("store.com")
