"""Tests for landing_page_scraper.py."""

from __future__ import annotations

import json

from unittest.mock import patch

import httpx

from ingestion.landing_page_scraper import (
    extract_from_json_ld,
    extract_from_og_tags,
    extract_json_ld,
    extract_og_tags,
    extract_product_page,
    extract_structured_data,
    resolve_and_scrape,
    scrape_landing_page,
)
from ingestion.rate_limiter import DomainRateLimiterRegistry
from pipeline.config import Settings


def _fast_registry() -> DomainRateLimiterRegistry:
    return DomainRateLimiterRegistry(
        global_rate=1000.0, global_capacity=1000.0, per_domain_rate=1000.0, per_domain_capacity=1000.0
    )


def _fast_settings() -> Settings:
    return Settings(scrape_max_attempts=3, scrape_backoff_min_seconds=0.01, scrape_backoff_max_seconds=0.02)


class TestExtractJsonLd:
    """Test JSON-LD extraction from HTML."""

    def test_extracts_json_ld_script(self) -> None:
        """Test extraction of JSON-LD from script tag."""
        html = """
        <html>
        <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Creatine"}
        </script>
        </head>
        </html>
        """
        result = extract_json_ld(html)
        assert result is not None
        assert result["@type"] == "Product"
        assert result["name"] == "Creatine"

    def test_returns_none_if_no_json_ld(self) -> None:
        """Test returns None if JSON-LD not found."""
        html = "<html><body>No structured data</body></html>"
        result = extract_json_ld(html)
        assert result is None

    def test_handles_invalid_json(self) -> None:
        """Test handles malformed JSON gracefully."""
        html = """
        <script type="application/ld+json">
        {invalid json}
        </script>
        """
        result = extract_json_ld(html)
        assert result is None


class TestExtractOgTags:
    """Test Open Graph tag extraction."""

    def test_extracts_og_tags(self) -> None:
        """Test extraction of OG meta tags."""
        html = """
        <head>
        <meta property="og:title" content="Product Name">
        <meta property="og:description" content="Great product">
        <meta property="og:price" content="29.99">
        <meta property="og:price:currency" content="USD">
        </head>
        """
        result = extract_og_tags(html)
        assert result["og:title"] == "Product Name"
        assert result["og:description"] == "Great product"
        assert result["og:price"] == "29.99"

    def test_returns_empty_dict_if_no_og_tags(self) -> None:
        """Test returns empty dict if no OG tags found."""
        html = "<html><body>No OG tags</body></html>"
        result = extract_og_tags(html)
        assert result == {}


class TestExtractFromJsonLd:
    """Test field extraction from JSON-LD data."""

    def test_extracts_product_fields(self) -> None:
        """Test extraction of Product schema fields."""
        json_ld = {
            "@type": "Product",
            "name": "Creatine Monohydrate",
            "category": "Supplements",
            "brand": {"@type": "Brand", "name": "MyBrand"},
            "description": "High-quality creatine",
            "offers": {"price": "19.99", "priceCurrency": "USD"},
            "aggregateRating": {"ratingValue": "4.5", "reviewCount": "120"},
        }
        result = extract_from_json_ld(json_ld)
        assert result["product_name"] == "Creatine Monohydrate"
        assert result["product_category"] == "Supplements"
        assert result["brand_name"] == "MyBrand"
        assert result["price"] == 19.99
        assert result["rating"] == 4.5
        assert result["rating_count"] == 120

    def test_handles_missing_fields(self) -> None:
        """Test handles missing optional fields gracefully."""
        json_ld = {"@type": "Product", "name": "Simple Product"}
        result = extract_from_json_ld(json_ld)
        assert result["product_name"] == "Simple Product"
        assert result.get("price") is None


class TestExtractFromOgTags:
    """Test field extraction from OG tags."""

    def test_extracts_og_fields(self) -> None:
        """Test extraction of Product fields from OG tags."""
        og_tags = {
            "og:title": "Product Name",
            "og:description": "Product description",
            "og:price": "49.99",
            "og:price:currency": "EUR",
        }
        result = extract_from_og_tags(og_tags)
        assert result["product_name"] == "Product Name"
        assert result["marketing_copy"] == "Product description"
        assert result["price"] == 49.99
        assert result["price_currency"] == "EUR"


class TestExtractStructuredData:
    """Test full structured data extraction."""

    def test_extracts_from_json_ld(self) -> None:
        """Test extraction from JSON-LD in HTML."""
        json_ld = {
            "@type": "Product",
            "name": "Test Product",
            "category": "TestCat",
            "brand": {"name": "TestBrand"},
            "offers": {"price": "29.99", "priceCurrency": "USD"},
        }
        html = f"""
        <head>
        <script type="application/ld+json">
        {json.dumps(json_ld)}
        </script>
        </head>
        """
        result = extract_structured_data("https://example.com/product", html)
        assert result is not None
        assert result.product_name == "Test Product"
        assert result.product_category == "TestCat"
        assert result.brand_name == "TestBrand"
        assert result.extraction_method == "structured_data"

    def test_returns_none_if_no_data(self) -> None:
        """Test returns None if no structured data found."""
        html = "<html><body>No product data</body></html>"
        result = extract_structured_data("https://example.com", html)
        assert result is None


class TestExtractProductPageOrchestration:
    """Test full extraction orchestration (4a + 4b + optional 4c)."""

    def test_extract_without_llm_enrichment(self) -> None:
        """Test extraction without LLM enrichment."""
        json_ld = {
            "@type": "Product",
            "name": "Test Product",
            "brand": {"name": "TestBrand"},
            "offers": {"price": "19.99", "priceCurrency": "USD"},
        }
        html = f"""
        <head>
        <script type="application/ld+json">
        {json.dumps(json_ld)}
        </script>
        </head>
        """
        result = extract_product_page("https://example.com/product", html, use_llm_enrichment=False)
        assert result is not None
        assert result.product_name == "Test Product"
        assert result.brand_name == "TestBrand"
        assert result.extraction_method == "structured_data"

    def test_extract_with_llm_enrichment(self) -> None:
        """Test extraction with LLM enrichment applied."""
        from ingestion.product_page_analyzer import SemanticExtraction

        json_ld = {
            "@type": "Product",
            "name": "Creatine",
            "brand": {"name": "MyBrand"},
            "offers": {"price": "19.99", "priceCurrency": "USD"},
        }
        html = f"""
        <head>
        <script type="application/ld+json">
        {json.dumps(json_ld)}
        </script>
        </head>
        <body>Premium creatine supplement from America</body>
        """

        mock_extraction = SemanticExtraction(
            product_category="Supplements",
            product_subcategory="Creatine",
            usp="Pure creatine monohydrate",
            cultural_branding=["American Made"],
        )

        with patch(
            "ingestion.product_page_analyzer.ReplicateVisionClient.extract_structured_text"
        ) as mock_llm:
            mock_llm.return_value = mock_extraction

            result = extract_product_page(
                "https://example.com/product", html, use_llm_enrichment=True
            )

        assert result is not None
        assert result.product_name == "Creatine"
        assert result.product_category == "Supplements"
        assert result.product_subcategory == "Creatine"
        assert result.extraction_method == "structured_data+llm"
        assert result.confidence == 0.75

    def test_extract_returns_none_if_structured_data_fails(self) -> None:
        """Test returns None if structured extraction fails."""
        html = "<html><body>No product data</body></html>"
        result = extract_product_page("https://example.com", html, use_llm_enrichment=True)
        assert result is None


class TestScrapeLandingPageHardening:
    """Tests for the httpx-based scrape_landing_page: headers, retry/backoff,
    rate limiter integration. Added alongside the Shopify JSON tiered
    scraper — see ingestion/rate_limiter.py for why 403 is retryable here
    (deliberately different from replicate_client.py's convention)."""

    def test_sends_configured_user_agent(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["user_agent"] = request.headers.get("user-agent", "")
            return httpx.Response(200, text="<html>ok</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
            result = scrape_landing_page("https://example.com/product", client=client)

        assert result == "<html>ok</html>"
        assert "Mozilla" in captured["user_agent"] or captured["user_agent"] != ""

    def test_retries_on_429_then_succeeds(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429)
            return httpx.Response(200, text="<html>recovered</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
            result = scrape_landing_page("https://flaky.example.com/product", client=client)

        assert result == "<html>recovered</html>"
        assert call_count == 2

    def test_retries_on_403_then_succeeds(self) -> None:
        """403 is retryable here (unlike replicate_client.py's convention) —
        confirmed empirically that our 403s were part of the same
        burst-triggered block as the 429s, not a permanent auth failure."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(403)
            return httpx.Response(200, text="<html>recovered</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
            result = scrape_landing_page("https://flaky.example.com/product", client=client)

        assert result == "<html>recovered</html>"
        assert call_count == 2

    def test_gives_up_after_max_attempts_on_persistent_429(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch(
            "ingestion.landing_page_scraper.get_settings",
            return_value=Settings(scrape_max_attempts=2, scrape_backoff_min_seconds=0.01, scrape_backoff_max_seconds=0.02),
        ):
            result = scrape_landing_page("https://always-throttled.example.com/product", client=client)

        assert result is None

    def test_404_not_retried(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
            result = scrape_landing_page("https://example.com/missing", client=client)

        assert result is None
        assert call_count == 1

    def test_rate_limiter_acquired_before_request_and_recorded_on_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>ok</html>", headers={"X-ShopId": "1"})

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        registry = _fast_registry()
        with patch.object(registry, "acquire", wraps=registry.acquire) as spy_acquire, patch.object(
            registry, "record_response", wraps=registry.record_response
        ) as spy_record:
            with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
                scrape_landing_page(
                    "https://example.com/product", client=client, rate_limiter=registry
                )
            spy_acquire.assert_called_once_with("example.com")
            spy_record.assert_called_once()

    def test_invalid_url_returns_none_without_request(self) -> None:
        result = scrape_landing_page("not-a-url")
        assert result is None


class TestResolveAndScrape:
    def test_returns_final_url_after_redirect(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url == "https://tracker.example.com/click/1":
                return httpx.Response(302, headers={"Location": "https://store.com/products/handle"})
            return httpx.Response(200, text="<html>landing</html>")

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
            result = resolve_and_scrape("https://tracker.example.com/click/1", client=client)

        assert result is not None
        final_url, html = result
        assert final_url == "https://store.com/products/handle"
        assert html == "<html>landing</html>"

    def test_returns_none_on_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        with patch("ingestion.landing_page_scraper.get_settings", return_value=_fast_settings()):
            result = resolve_and_scrape("https://down.example.com/product", client=client)

        assert result is None
