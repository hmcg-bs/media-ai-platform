"""Test landing page scraper on furniture ads."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.apify_client import run_ad_scrape
from ingestion.landing_page_scraper import scrape_landing_page, extract_product_page
from ingestion.normalize import normalize_ad
from pipeline.logger import get_logger

logger = get_logger(__name__)


def test_furniture_ads() -> None:
    """Scrape furniture ads and test landing page extraction with full pipeline."""
    print("Scraping furniture ads from Meta Ad Library...\n")
    try:
        raw_items = run_ad_scrape(search_query="furniture", count=15, country="US")
        print(f"✅ Scraped {len(raw_items)} furniture ads\n")
    except Exception as e:
        print(f"❌ Failed to scrape: {e}")
        return

    # Normalize
    ads = []
    for raw in raw_items:
        try:
            ad = normalize_ad(raw)
            ads.append(ad)
        except Exception as e:
            logger.warning("normalize_failed", error=str(e))

    print(f"✅ Normalized {len(ads)} ads\n")
    print("=" * 80)

    # Test product page scraping with full pipeline (4a + 4b + 4c)
    results = []
    urls_tested = 0
    successful_extractions = 0

    for i, ad in enumerate(ads, 1):
        url = ad.link_url
        if not url:
            print(f"[{i}] {ad.page_name or 'Unknown':<35} ⏭️  Skip (no link_url)")
            continue

        urls_tested += 1
        print(f"[{urls_tested}] {ad.page_name or 'Unknown':<35} → {url[:45]}...")

        # Stage 4a: Scrape HTML
        html = scrape_landing_page(url, timeout_s=8)
        if not html:
            print(f"    ❌ Failed to scrape")
            results.append({"url": url, "success": False, "ad_name": ad.page_name})
            continue

        # Stage 4b+4c: Extract product data (structured + optional LLM)
        product = extract_product_page(url, html, use_llm_enrichment=False)
        if not product:
            print(f"    ⚠️  Scraped but no structured data")
            results.append({"url": url, "success": False, "ad_name": ad.page_name})
            continue

        successful_extractions += 1
        print(f"    ✅ Extracted product data:")
        print(f"       Name: {product.product_name[:60]}")
        if product.product_category:
            print(f"       Category: {product.product_category}")
        if product.brand_name:
            print(f"       Brand: {product.brand_name}")
        if product.price:
            print(f"       Price: ${product.price} {product.price_currency}")
        if product.rating:
            print(f"       Rating: {product.rating}★ ({product.rating_count} reviews)")
        print(f"       Method: {product.extraction_method} | Confidence: {product.confidence}")

        results.append({
            "url": url,
            "success": True,
            "ad_name": ad.page_name,
            "product": product.model_dump(),
        })

    print(f"\n{'='*80}")
    print(f"Tested: {urls_tested}/{len(ads)} ads with link_url")
    print(f"Results: {successful_extractions}/{urls_tested} product pages extracted ({100*successful_extractions//urls_tested if urls_tested > 0 else 0}%)")

    # Save results
    results_file = Path("/tmp/furniture_scraper_results.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Detailed results saved to: {results_file}")


if __name__ == "__main__":
    test_furniture_ads()
