"""Fresh multi-query Apify ingestion for a full pipeline re-run -- the Step 1
half of what would become the recurring GCP pipeline. Scrapes several
supplement-subcategory search queries (a single keyword under-represents
the category's real diversity), dedupes by ad_archive_id, and keeps only
ads with at least one image_url (a hard requirement for Step 2's creative
pipeline downstream).

Deliberately produces a NEW corpus file rather than overwriting the
existing data/supplements_enriched.json -- the existing corpus stays
available as a fallback until this one is verified and promoted.

    uv run python -m ingestion.fresh_corpus_scrape --out data/supplements_fresh.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingestion.apify_client import ApifyClient
from ingestion.normalize import normalize_ad
from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)

# Diverse supplement subcategories -- a single "supplements" query clusters
# around whichever advertisers happen to rank highest for that one term;
# spreading across real subcategories gives a more representative corpus,
# closer to what a real recurring discovery pipeline would do.
DEFAULT_QUERIES = (
    "supplements",
    "vitamins",
    "protein powder",
    "creatine",
    "collagen",
    "probiotics",
    "multivitamin",
    "fish oil omega 3",
    "magnesium supplement",
    "weight loss supplement",
    "pre workout",
    "melatonin sleep",
)


def scrape_and_normalize(
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    count_per_query: int = 350,
    country: str = "US",
) -> list[dict]:
    """Runs one Apify actor call per query, normalizes every item, and
    dedupes by ad_archive_id across all queries (the same ad frequently
    surfaces under multiple keyword searches)."""
    settings = get_settings()
    if not settings.apify_api_token:
        raise ValueError("APIFY_API_TOKEN not configured. Set it in .env or environment.")

    client = ApifyClient(api_token=settings.apify_api_token, timeout_s=600)

    seen_ids: set[str] = set()
    normalized: list[dict] = []
    normalize_failed = 0

    for query in queries:
        logger.info("fresh_scrape_query_start", query=query, count=count_per_query)
        try:
            raw_items = client.run_ad_scrape(
                search_query=query, count=count_per_query, country=country
            )
        except Exception as e:  # noqa: BLE001 -- one bad query shouldn't kill the whole scrape
            logger.error("fresh_scrape_query_failed", query=query, error=str(e))
            continue
        logger.info("fresh_scrape_query_complete", query=query, raw_count=len(raw_items))

        for item in raw_items:
            try:
                ad = normalize_ad(item)
            except Exception as e:  # noqa: BLE001
                normalize_failed += 1
                logger.warning("fresh_scrape_normalize_failed", query=query, error=str(e))
                continue
            if not ad.ad_archive_id or ad.ad_archive_id in seen_ids:
                continue
            if not ad.image_urls:
                continue  # Step 2 needs at least one image; skip up front, not silently later.
            seen_ids.add(ad.ad_archive_id)
            normalized.append(ad.model_dump(mode="json"))

    logger.info(
        "fresh_scrape_complete",
        total_unique=len(normalized),
        normalize_failed=normalize_failed,
    )
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Fresh multi-query supplement-ad scrape.")
    parser.add_argument("--out", type=Path, default=Path("data/supplements_fresh.json"))
    parser.add_argument(
        "--queries", nargs="+", default=None, help="Override the default query list."
    )
    parser.add_argument("--count-per-query", type=int, default=350)
    parser.add_argument("--country", default="US")
    args = parser.parse_args()

    queries = tuple(args.queries) if args.queries else DEFAULT_QUERIES
    ads = scrape_and_normalize(queries, args.count_per_query, args.country)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ads, indent=2, default=str))

    print(f"✅ Fresh scrape complete: {len(ads)} unique, image-having ads -> {args.out}")


if __name__ == "__main__":
    main()
