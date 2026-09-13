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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ingestion.apify_client import ApifyClient
from ingestion.normalize import normalize_ad
from pipeline.artifacts import atomic_write_json, exclusive_output
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


def validate_resume_state(
    state: dict[str, Any], queries: tuple[str, ...], count_per_query: int, country: str
) -> None:
    """Reject a checkpoint created for a different corpus definition."""
    if not state:
        return
    expected = {
        "queries": list(queries),
        "count_per_query": count_per_query,
        "country": country.upper(),
    }
    mismatched = [key for key, value in expected.items() if state.get(key) != value]
    if mismatched:
        raise ValueError(
            "resume checkpoint does not match this invocation for "
            f"{', '.join(mismatched)}; use --no-resume or a different --out"
        )


def scrape_and_normalize(
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    count_per_query: int = 350,
    country: str = "US",
    client: Any | None = None,
    existing_ads: list[dict[str, Any]] | None = None,
    completed_queries: set[str] | None = None,
    checkpoint: Callable[[list[dict[str, Any]], set[str]], None] | None = None,
) -> list[dict]:
    """Runs one Apify actor call per query, normalizes every item, and
    dedupes by ad_archive_id across all queries (the same ad frequently
    surfaces under multiple keyword searches)."""
    if client is None:
        settings = get_settings()
        if not settings.apify_api_token:
            raise ValueError("APIFY_API_TOKEN not configured. Set it in .env or environment.")
        client = ApifyClient(api_token=settings.apify_api_token, timeout_s=600)

    normalized: list[dict[str, Any]] = list(existing_ads or [])
    ads_by_id: dict[str, dict[str, Any]] = {
        str(ad["ad_archive_id"]): ad for ad in normalized
    }
    completed_queries = completed_queries if completed_queries is not None else set()
    normalize_failed = 0

    for query in queries:
        if query in completed_queries:
            logger.info("fresh_scrape_query_resumed", query=query)
            continue
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
            if not ad.ad_archive_id:
                continue
            if ad.ad_archive_id in ads_by_id:
                existing = ads_by_id[ad.ad_archive_id]
                existing["search_queries"] = sorted(
                    set(existing.get("search_queries", [])) | {query}
                )
                continue
            if not ad.image_urls:
                continue  # Step 2 needs at least one image; skip up front, not silently later.
            record = ad.model_dump(mode="json")
            record["search_queries"] = [query]
            ads_by_id[ad.ad_archive_id] = record
            normalized.append(record)
        completed_queries.add(query)
        if checkpoint is not None:
            checkpoint(normalized, completed_queries)

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
    parser.add_argument(
        "--no-resume", action="store_true", help="Ignore an existing corpus/checkpoint."
    )
    args = parser.parse_args()

    queries = tuple(args.queries) if args.queries else DEFAULT_QUERIES
    state_path = args.out.with_suffix(args.out.suffix + ".state.json")
    with exclusive_output(args.out):
        existing_ads = (
            json.loads(args.out.read_text())
            if args.out.exists() and not args.no_resume
            else []
        )
        state = (
            json.loads(state_path.read_text())
            if state_path.exists() and not args.no_resume
            else {}
        )
        validate_resume_state(state, queries, args.count_per_query, args.country)
        completed_queries = set(state.get("completed_queries", []))

        def checkpoint(ads: list[dict[str, Any]], completed: set[str]) -> None:
            # Corpus first, then state: after a crash, re-running a completed
            # query is safe and deduped; skipping a query whose ads were never
            # published would be silent data loss.
            atomic_write_json(args.out, ads)
            atomic_write_json(state_path, {
                "queries": list(queries),
                "completed_queries": sorted(completed),
                "count_per_query": args.count_per_query,
                "country": args.country.upper(),
                "result_count": len(ads),
            })

        ads = scrape_and_normalize(
            queries,
            args.count_per_query,
            args.country,
            existing_ads=existing_ads,
            completed_queries=completed_queries,
            checkpoint=checkpoint,
        )
        checkpoint(ads, completed_queries)

    print(f"✅ Fresh scrape complete: {len(ads)} unique, image-having ads -> {args.out}")


if __name__ == "__main__":
    main()
