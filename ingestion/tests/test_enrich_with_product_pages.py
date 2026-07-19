"""Tests for Stage 4d enrichment utility."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ingestion.enrich_with_product_pages import enrich_corpus
from ingestion.models import CompetitorAd
from ingestion.product_page import ProductPage


def test_enrich_corpus_missing_file(tmp_path: Path) -> None:
    """Test handling of missing ads file."""
    ads_file = tmp_path / "nonexistent.json"
    out_file = tmp_path / "enriched.json"

    result = enrich_corpus(ads_file, out_file)
    assert result == 1


def test_enrich_corpus_invalid_json(tmp_path: Path) -> None:
    """Test handling of invalid JSON."""
    ads_file = tmp_path / "ads.json"
    ads_file.write_text("{invalid json}")

    out_file = tmp_path / "enriched.json"
    result = enrich_corpus(ads_file, out_file)
    assert result == 1


def test_enrich_corpus_skips_no_link_url(tmp_path: Path) -> None:
    """Test that ads without link_url are skipped."""
    ad = CompetitorAd(page_name="Test Page", link_url="")
    ads_file = tmp_path / "ads.json"
    ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

    out_file = tmp_path / "enriched.json"
    result = enrich_corpus(ads_file, out_file, use_llm=False)

    assert result == 0
    enriched = json.loads(out_file.read_text())
    assert len(enriched) == 1
    assert enriched[0]["product_page"] is None


def test_enrich_corpus_enriches_with_product_page(tmp_path: Path) -> None:
    """Test successful enrichment of ads with product data."""
    ad = CompetitorAd(page_name="Test Page", link_url="https://example.com/product")
    ads_file = tmp_path / "ads.json"
    ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

    mock_product = ProductPage(
        product_name="Test Product",
        brand_name="Test Brand",
        price=29.99,
        extraction_method="structured_data",
        confidence=0.9,
        url="https://example.com/product",
    )

    with patch("ingestion.enrich_with_product_pages.scrape_landing_page") as mock_scrape:
        with patch("ingestion.enrich_with_product_pages.extract_product_page") as mock_extract:
            mock_scrape.return_value = "<html>test</html>"
            mock_extract.return_value = mock_product

            out_file = tmp_path / "enriched.json"
            result = enrich_corpus(ads_file, out_file, use_llm=False)

    assert result == 0
    enriched = json.loads(out_file.read_text())
    assert len(enriched) == 1
    assert enriched[0]["product_page"] is not None
    assert enriched[0]["product_page"]["product_name"] == "Test Product"
    assert enriched[0]["product_page"]["brand_name"] == "Test Brand"


def test_enrich_corpus_continues_on_scrape_failure(tmp_path: Path) -> None:
    """Test that corpus enrichment continues if scraping fails for one ad."""
    ad1 = CompetitorAd(page_name="Page 1", link_url="https://example.com/1")
    ad2 = CompetitorAd(page_name="Page 2", link_url="https://example.com/2")
    ads_file = tmp_path / "ads.json"
    ads_file.write_text(
        json.dumps([ad1.model_dump(mode="json"), ad2.model_dump(mode="json")])
    )

    mock_product = ProductPage(
        product_name="Product 2",
        extraction_method="structured_data",
        url="https://example.com/2",
    )

    with patch("ingestion.enrich_with_product_pages.scrape_landing_page") as mock_scrape:
        with patch("ingestion.enrich_with_product_pages.extract_product_page") as mock_extract:
            # First ad fails to scrape, second succeeds
            mock_scrape.side_effect = [None, "<html>test</html>"]
            mock_extract.return_value = mock_product

            out_file = tmp_path / "enriched.json"
            result = enrich_corpus(ads_file, out_file, use_llm=False)

    assert result == 0
    enriched = json.loads(out_file.read_text())
    assert len(enriched) == 2
    # First ad not enriched
    assert enriched[0]["product_page"] is None
    # Second ad enriched
    assert enriched[1]["product_page"]["product_name"] == "Product 2"
