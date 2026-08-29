"""Refreshes stale image_urls in the corpus.

Facebook's CDN image URLs are signed and time-limited (confirmed live: a
sampled URL's own embedded expiry decoded to several days in the past) —
every image_urls entry captured by the original Step 1 ingestion scrape has
since expired, corpus-wide. Individual ad-detail-page URLs
(facebook.com/ads/library/?id=<id>) were confirmed, via an isolated A/B test
against a freshly-reconfirmed-live ad, to NOT be supported as scraper input
by the curious_coder/facebook-ads-library-scraper Apify actor (0 items
returned even for an ad just reconfirmed live via search) — so a per-ad
re-fetch isn't possible. This re-runs the same "supplements" keyword search
used for the original ingestion; ads still active/highly-ranked tend to
resurface (confirmed live: the very first fresh result was already in our
existing corpus), giving substantial overlap without a full from-scratch
re-ingestion. Ads not resurfaced simply keep their (unusable) stale URLs and
are skipped later at the Step 2 fetch stage, same as any other fetch
failure — never dropped from the corpus.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ingestion.apify_client import run_ad_scrape
from ingestion.normalize import normalize_ad
from pipeline.config import get_settings
from pipeline.logger import configure_logging, get_logger

logger = get_logger(__name__)


def refresh_image_urls(
    ads_file: Path,
    output_file: Path,
    count: int,
    search_query: str,
    run_fn: Callable[[str, int, str | None, str], list[dict]] | None = None,
) -> tuple[int, int]:
    """Re-scrapes `search_query`, matches results back to the corpus by
    ad_archive_id, and overwrites `image_urls` wherever a fresh match is
    found. Returns (total_ads, ads_refreshed). `run_fn` is injectable for
    offline tests, same DI seam as ingestion.apify_client.run_ad_scrape
    already provides."""
    ads = json.loads(ads_file.read_text())

    logger.info("refresh_scrape_started", search_query=search_query, count=count)
    raw_items = run_ad_scrape(search_query=search_query, count=count, run_fn=run_fn)
    logger.info("refresh_scrape_completed", item_count=len(raw_items))

    fresh_by_id: dict[str, list[str]] = {}
    for raw in raw_items:
        normalized = normalize_ad(raw)
        if normalized.ad_archive_id and normalized.image_urls:
            fresh_by_id[normalized.ad_archive_id] = normalized.image_urls

    refreshed = 0
    for ad in ads:
        ad_id = ad.get("ad_archive_id")
        if ad_id in fresh_by_id:
            ad["image_urls"] = fresh_by_id[ad_id]
            refreshed += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(ads, indent=2, default=str))
    return len(ads), refreshed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh stale corpus image_urls via a fresh Apify scrape."
    )
    parser.add_argument("--ads", type=Path, default=Path("data/supplements_enriched.json"))
    parser.add_argument("--out", type=Path, default=Path("data/supplements_enriched.json"))
    parser.add_argument("--count", type=int, default=3000)
    parser.add_argument("--query", type=str, default="supplements")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    total, refreshed = refresh_image_urls(args.ads, args.out, args.count, args.query)
    print(f"Refreshed image_urls for {refreshed}/{total} ads -> {args.out}")


if __name__ == "__main__":
    main()
