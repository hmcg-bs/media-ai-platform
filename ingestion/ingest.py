"""Step 1 ingestion CLI: scrape ads via Apify, normalize, download, write corpus.

Usage:
    python -m ingestion.ingest --query "linkedin" --count 10 \\
      --out creatives/apify/<brand>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ingestion.apify_client import ApifyClientError, run_ad_scrape
from ingestion.download import download_creatives
from ingestion.models import CompetitorAd
from ingestion.normalize import normalize_ad
from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scrape Facebook ads via Apify, normalize, download, write corpus."
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="Facebook page name or URL (e.g., 'apple', 'linkedin', or full URL).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Max ads to scrape (minimum 10, default 100).",
    )
    parser.add_argument(
        "--country",
        type=str,
        default="US",
        help="Country code (default US).",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output directory for the run (ads.json, query.json, media/).",
    )

    args = parser.parse_args()

    # Validate count minimum
    if args.count < 10:
        logger.error(
            "ingest_invalid_count",
            msg="Count must be at least 10 (Apify actor requirement).",
        )
        return 1

    settings = get_settings()
    if not settings.apify_api_token:
        logger.error("ingest_no_token", msg="APIFY_API_TOKEN not set in .env or environment.")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir = out_dir / "media"

    logger.info(
        "ingest_start",
        query=args.query,
        country=args.country,
        count=args.count,
        out_dir=str(out_dir),
    )

    # Step 1: Scrape via Apify.
    try:
        raw_items = run_ad_scrape(
            search_query=args.query,
            count=args.count,
            country=args.country,
        )
    except ApifyClientError as e:
        logger.error("ingest_scrape_failed", exc_str=str(e))
        return 1

    logger.info("ingest_scraped", item_count=len(raw_items))

    # Step 2: Normalize.
    ads: list[CompetitorAd] = []
    for raw in raw_items:
        try:
            ad = normalize_ad(raw)
            ads.append(ad)
        except Exception as e:
            logger.warning(
                "ingest_normalize_failed",
                exc_str=str(e),
                raw_keys=list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
            )
            # Do NOT drop on normalize error; log and continue (defensive).

    logger.info("ingest_normalized", ad_count=len(ads))

    # Step 3: Download creatives.
    download_creatives(ads, media_dir)

    # Step 4: Write corpus to disk.
    ads_json = out_dir / "ads.json"
    ads_json.write_text(
        json.dumps(
            [ad.model_dump(mode="json") for ad in ads],
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("ingest_wrote_ads", path=str(ads_json), count=len(ads))

    # Step 5: Write query provenance (token REDACTED).
    query_data: dict[str, Any] = {
        "actor_id": settings.apify_actor_id,
        "search_query": args.query,
        "country": args.country,
        "limit": args.count,
        "result_count": len(ads),
        "apify_token_redacted": "***",
    }
    query_json = out_dir / "query.json"
    query_json.write_text(
        json.dumps(query_data, indent=2),
        encoding="utf-8",
    )
    logger.info("ingest_wrote_query", path=str(query_json))

    logger.info(
        "ingest_complete",
        ads_count=len(ads),
        media_count=sum(1 for _ in media_dir.glob("*") if _.is_file()),
        out_dir=str(out_dir),
    )

    return 0


if __name__ == "__main__":
    exit(main())
