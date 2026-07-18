"""Step 1 ingestion CLI: scrape ads via Apify, normalize, download, write corpus.

Usage:
    python -m ingestion.ingest --url "<Ad Library page URL>" --count N \\
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
        "--url",
        type=str,
        required=True,
        help="Ad Library page or search URL to scrape.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Max ads to scrape (default 100).",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output directory for the run (ads.json, query.json, media/).",
    )

    args = parser.parse_args()

    settings = get_settings()
    if not settings.apify_api_token:
        logger.error("ingest_no_token", msg="APIFY_API_TOKEN not set in .env or environment.")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir = out_dir / "media"

    logger.info(
        "ingest_start",
        url=args.url,
        count=args.count,
        out_dir=str(out_dir),
    )

    # Step 1: Scrape via Apify.
    try:
        raw_items = run_ad_scrape(urls=[args.url], count=args.count)
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
        "input_urls": [args.url],
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
