"""Test furniture ads with ad context enhancement to Stage 4c LLM extraction."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.apify_client import run_ad_scrape
from ingestion.landing_page_scraper import scrape_landing_page, extract_product_page
from ingestion.normalize import normalize_ad
from pipeline.logger import get_logger

logger = get_logger(__name__)


def test_furniture_with_vs_without_ad_context() -> None:
    """Scrape furniture ads and extract with/without ad context to compare results."""
    print("Scraping furniture ads from Meta Ad Library...\n")
    try:
        raw_items = run_ad_scrape(search_query="furniture", count=10, country="US")
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

    # Test with and without ad context
    results = {"without_context": [], "with_context": []}
    urls_tested = 0
    success_count = {"without": 0, "with": 0}

    for i, ad in enumerate(ads, 1):
        url = ad.link_url
        if not url:
            print(f"[{i}] {ad.page_name or 'Unknown':<35} ⏭️  Skip (no link_url)")
            continue

        urls_tested += 1
        print(f"[{urls_tested}] {ad.page_name or 'Unknown':<35}")

        # Scrape HTML
        html = scrape_landing_page(url, timeout_s=8)
        if not html:
            print(f"    ❌ Failed to scrape")
            results["without_context"].append({"url": url, "success": False})
            results["with_context"].append({"url": url, "success": False})
            continue

        # Extract WITHOUT ad context
        print(f"    ⏳ Extracting WITHOUT ad context...")
        product_no_context = extract_product_page(
            url, html, use_llm_enrichment=True, ad_context=None
        )

        # Extract WITH ad context
        print(f"    ⏳ Extracting WITH ad context...")
        ad_context = {
            "title": ad.title,
            "body": ad.body[:200] if ad.body else "",
            "caption": ad.caption,
        }
        product_with_context = extract_product_page(
            url, html, use_llm_enrichment=True, ad_context=ad_context
        )

        if product_no_context:
            success_count["without"] += 1
            print(f"    ✅ WITHOUT context: {product_no_context.product_name[:50]}")
            if product_no_context.product_category:
                print(f"       Category: {product_no_context.product_category}")
            print(f"       Confidence: {product_no_context.confidence}")
            results["without_context"].append(
                {"url": url, "success": True, "product": product_no_context.model_dump()}
            )
        else:
            print(f"    ⚠️  WITHOUT context: Failed")
            results["without_context"].append({"url": url, "success": False})

        if product_with_context:
            success_count["with"] += 1
            print(f"    ✅ WITH context:    {product_with_context.product_name[:50]}")
            if product_with_context.product_category:
                print(f"       Category: {product_with_context.product_category}")
            if product_with_context.usp:
                print(f"       USP: {product_with_context.usp[:50]}")
            print(f"       Confidence: {product_with_context.confidence}")
            results["with_context"].append(
                {"url": url, "success": True, "product": product_with_context.model_dump()}
            )
        else:
            print(f"    ⚠️  WITH context:    Failed")
            results["with_context"].append({"url": url, "success": False})

        print()

    print(f"\n{'='*80}")
    print(f"Comparison Results:")
    print(f"  WITHOUT ad context: {success_count['without']}/{urls_tested} successful")
    print(f"  WITH ad context:    {success_count['with']}/{urls_tested} successful")
    improvement = success_count["with"] - success_count["without"]
    if improvement > 0:
        print(f"  Improvement: +{improvement} ({100*improvement//urls_tested}% better)")
    elif improvement < 0:
        print(f"  Regression: {improvement}")
    else:
        print(f"  No change")

    # Save results
    results_file = Path("/tmp/furniture_ad_context_comparison.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to: {results_file}")


if __name__ == "__main__":
    test_furniture_with_vs_without_ad_context()
