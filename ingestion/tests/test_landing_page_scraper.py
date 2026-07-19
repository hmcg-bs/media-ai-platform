"""Tests for landing_page_scraper.py."""

from __future__ import annotations

import json

from ingestion.landing_page_scraper import (
    extract_from_json_ld,
    extract_from_og_tags,
    extract_json_ld,
    extract_og_tags,
    extract_structured_data,
)


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
