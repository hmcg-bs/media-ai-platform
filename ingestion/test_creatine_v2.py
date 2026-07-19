"""Re-test landing page scraper on creatine ads (v2 - with explicit count=10)."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.apify_client import run_ad_scrape
from ingestion.normalize import normalize_ad
from ingestion.landing_page_scraper import scrape_landing_page, extract_structured_data
from pipeline.logger import get_logger

logger = get_logger(__name__)


def test_creatine_ads_v2() -> None:
    """Scrape 10 creatine ads and test landing page scraper on each."""
    print("Scraping 10 creatine ads from Meta Ad Library (v2)...\n")
    try:
        raw_items = run_ad_scrape(search_query="creatine", count=10, country="US")
        print(f"✅ Scraped {len(raw_items)} creatine ads\n")
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
    print("=" * 70)

    # Test product page scraping
    results = []
    urls_tested = 0

    for i, ad in enumerate(ads, 1):
        url = ad.link_url
        if not url:
            print(f"[{i}] {ad.page_name or 'Unknown':<30} ⏭️  Skip (no link_url)")
            continue

        urls_tested += 1
        print(f"[{urls_tested}] {ad.page_name or 'Unknown':<30} → {url[:50]}...")

        html = scrape_landing_page(url, timeout_s=8)

        if not html:
            print(f"    ❌ Failed to scrape")
            results.append({"url": url, "success": False, "ad_name": ad.page_name})
            continue

        product = extract_structured_data(url, html)
        if not product:
            print(f"    ⚠️  Scraped but no structured data")
            results.append({"url": url, "success": False, "ad_name": ad.page_name})
            continue

        print(f"    ✅ Found product data")
        print(f"       Name: {product.product_name[:50]}")
        if product.product_category:
            print(f"       Category: {product.product_category}")
        if product.brand_name:
            print(f"       Brand: {product.brand_name}")
        if product.price:
            print(f"       Price: ${product.price} {product.price_currency}")
        if product.rating:
            print(f"       Rating: {product.rating}★ ({product.rating_count} reviews)")

        results.append({
            "url": url,
            "success": True,
            "ad_name": ad.page_name,
            "product": product.model_dump(),
        })

    print(f"\n{'='*70}")
    print(f"Tested: {urls_tested}/{len(ads)} ads with link_url")
    print(f"Results: {sum(1 for r in results if r['success'])}/{urls_tested} product pages extracted")

    # Save results
    results_file = Path("/tmp/creatine_scraper_results_v2.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Detailed results saved to: {results_file}")


if __name__ == "__main__":
    test_creatine_ads_v2()
