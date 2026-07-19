"""Stage 4d: Optional enrichment utility.

Enriches a corpus of ads (ads.json) with ProductPage data by analyzing landing
pages linked in each ad.

Usage:
    python -m ingestion.enrich_with_product_pages \\
      --ads /path/to/ads.json \\
      --out /path/to/enriched_ads.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingestion.landing_page_scraper import extract_product_page, scrape_landing_page
from ingestion.models import CompetitorAd
from pipeline.logger import get_logger

logger = get_logger(__name__)


def enrich_corpus(ads_file: Path, output_file: Path, use_llm: bool = True) -> int:
    """Enrich ads corpus with landing page product analysis.

    Args:
        ads_file: Path to ads.json (array of CompetitorAd dicts).
        output_file: Path to write enriched ads.json.
        use_llm: If True, use LLM for semantic enrichment (Stage 4c).

    Returns:
        0 on success, 1 on error.
    """
    if not ads_file.exists():
        logger.error("enrich_file_not_found", path=str(ads_file))
        return 1

    try:
        with open(ads_file) as f:
            ads_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("enrich_invalid_json", path=str(ads_file), error=str(e))
        return 1

    if not isinstance(ads_data, list):
        logger.error("enrich_invalid_format", msg="ads.json must be an array")
        return 1

    enriched_ads = []
    for i, ad_dict in enumerate(ads_data, 1):
        try:
            ad = CompetitorAd.model_validate(ad_dict)
        except Exception as e:
            logger.warning("enrich_ad_invalid", index=i, error=str(e))
            enriched_ads.append(ad_dict)  # Keep original on validation error
            continue

        # Skip if no link_url
        if not ad.link_url:
            logger.debug("enrich_skipped_no_link", index=i, page=ad.page_name)
            enriched_ads.append(ad_dict)
            continue

        # Scrape landing page
        html = scrape_landing_page(ad.link_url, timeout_s=10)
        if not html:
            logger.warning(
                "enrich_scrape_failed",
                index=i,
                link_url=ad.link_url,
                page=ad.page_name,
            )
            enriched_ads.append(ad_dict)
            continue

        # Build ad context for Stage 4c (LLM enrichment)
        ad_context = None
        if use_llm and (ad.title or ad.body or ad.caption):
            ad_context = {
                "title": ad.title or "",
                "body": ad.body or "",
                "caption": ad.caption or "",
            }

        # Extract product page (with optional ad context for LLM enrichment)
        product_page = extract_product_page(
            ad.link_url, html, use_llm_enrichment=use_llm, ad_context=ad_context
        )
        if not product_page:
            logger.warning(
                "enrich_extraction_failed",
                index=i,
                link_url=ad.link_url,
                page=ad.page_name,
            )
            enriched_ads.append(ad_dict)
            continue

        # Enrich ad with product_page
        ad.product_page = product_page
        enriched_ads.append(ad.model_dump(mode="json"))
        logger.info(
            "enrich_success",
            index=i,
            product_name=product_page.product_name,
            confidence=product_page.confidence,
        )

    # Write enriched corpus
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(enriched_ads, f, indent=2, default=str)

    enriched_count = sum(
        1
        for ad in enriched_ads
        if isinstance(ad, dict) and ad.get("product_page") is not None
    )
    logger.info(
        "enrich_complete",
        input_count=len(ads_data),
        enriched_count=enriched_count,
        output_file=str(output_file),
    )

    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Enrich ads corpus with landing page product analysis (Stage 4)."
    )
    parser.add_argument(
        "--ads",
        type=str,
        required=True,
        help="Path to ads.json corpus to enrich.",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output path for enriched ads.json.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM enrichment (Stage 4c); use structured data only.",
    )

    args = parser.parse_args()
    return enrich_corpus(Path(args.ads), Path(args.out), use_llm=not args.no_llm)


if __name__ == "__main__":
    exit(main())
