"""Test full pipeline (Stages 4a-4c) on real product pages from ads corpus."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.landing_page_scraper import scrape_landing_page, extract_product_page
from pipeline.logger import get_logger

logger = get_logger(__name__)


def test_real_pages_with_full_pipeline() -> None:
    """Test full extraction pipeline (4a + 4b + 4c) on real product pages.

    Stages:
    - 4a: HTML scraping
    - 4b: Structured data extraction (JSON-LD, OG tags)
    - 4c: LLM semantic enrichment (category, USP, branding, variants)
    """
    ads_json = Path("/tmp/apify_test_run/ads.json")

    if not ads_json.exists():
        print("❌ ads.json not found at /tmp/apify_test_run/ads.json")
        print("   Run ingestion first:")
        print("   uv run python -m ingestion.ingest --query google --count 10 --out /tmp/apify_test_run")
        return

    with open(ads_json) as f:
        ads = json.load(f)

    print(f"Testing full pipeline on {len(ads)} ads from corpus\n")
    print("=" * 70)

    results = {"structured_only": [], "with_llm": []}
    urls_tested = 0

    for i, ad in enumerate(ads[:5], 1):  # Test first 5
        url = ad.get("link_url", "")
        if not url:
            print(f"[{i}] ⏭️  Skip (no link_url)")
            continue

        urls_tested += 1
        print(f"[{urls_tested}] {url[:60]}...")

        # Stage 4a: Scrape HTML
        html = scrape_landing_page(url, timeout_s=5)
        if not html:
            print(f"    ❌ Failed to scrape")
            results["structured_only"].append({"url": url, "success": False})
            continue

        # Stage 4b + 4c (with LLM disabled first)
        product_structured = extract_product_page(url, html, use_llm_enrichment=False)
        if not product_structured:
            print(f"    ⚠️  Scraped but no structured data")
            results["structured_only"].append({"url": url, "success": False})
            continue

        print(f"    ✅ Stage 4a-4b (structured):")
        print(f"       Name: {product_structured.product_name[:50]}")
        if product_structured.product_category:
            print(f"       Category: {product_structured.product_category}")
        if product_structured.brand_name:
            print(f"       Brand: {product_structured.brand_name}")
        if product_structured.price:
            print(f"       Price: ${product_structured.price} {product_structured.price_currency}")
        print(f"       Confidence: {product_structured.confidence}")

        results["structured_only"].append({
            "url": url,
            "success": True,
            "product": product_structured.model_dump(),
        })

    print(f"\n{'='*70}")
    print(f"Tested: {urls_tested}/{len(ads)} ads with link_url")
    print(f"Structured only: {sum(1 for r in results['structured_only'] if r['success'])}/{urls_tested} successful")

    # Save results for review
    results_file = Path("/tmp/scraper_pipeline_results.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to: {results_file}")


if __name__ == "__main__":
    test_real_pages_with_full_pipeline()
