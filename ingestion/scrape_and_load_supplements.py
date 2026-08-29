"""
Scrape 20k Supplements ads via Apify and load directly to BigQuery.

Usage:
  python -m ingestion.scrape_and_load_supplements \
    --search-query "supplements" \
    --count 20000 \
    --project-id "clean-patrol-496108-m9" \
    --dataset-id "20k_supplement_trial"
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ingestion.apify_client import ApifyClient
from ingestion.normalize import normalize_ad
from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


def scrape_ads(search_query: str, count: int = 20000, country: str = "US") -> list[dict]:
    """Scrape ads from Meta Ad Library via Apify."""
    settings = get_settings()

    if not settings.apify_api_token:
        raise ValueError("APIFY_API_TOKEN not configured. Set it in .env or environment.")

    client = ApifyClient(api_token=settings.apify_api_token)

    logger.info(
        "scrape_start",
        search_query=search_query,
        count=count,
        country=country,
    )

    raw_items = client.run_ad_scrape(
        search_query=search_query,
        count=count,
        country=country,
    )

    logger.info(
        "scrape_complete",
        count=len(raw_items),
    )

    return raw_items


def normalize_ads(raw_items: list[dict]) -> list[dict]:
    """Normalize raw Apify items to CompetitorAd schema."""
    normalized = []
    failed = 0

    for i, item in enumerate(raw_items):
        try:
            ad = normalize_ad(item)
            normalized.append(ad.model_dump(mode="json"))
        except Exception as e:
            failed += 1
            if failed <= 5:  # Log first 5 failures
                logger.warning(
                    "normalize_failed",
                    index=i,
                    error=str(e),
                )

    logger.info(
        "normalize_complete",
        normalized_count=len(normalized),
        failed_count=failed,
    )

    return normalized


def load_to_bigquery(
    records: list[dict],
    project_id: str,
    dataset_id: str,
    table_id: str = "ads_raw",
    run_id: str = "run_001",
) -> int:
    """Load normalized records to BigQuery."""
    # Lazy import to avoid pandas circular import
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id)
    table_id_full = f"{project_id}.{dataset_id}.{table_id}"

    # Add run_id and ingested_at to all records
    for record in records:
        record["run_id"] = run_id
        record["ingested_at"] = datetime.utcnow().isoformat() + "Z"

    logger.info(
        "bigquery_load_start",
        table_id=table_id_full,
        record_count=len(records),
    )

    try:
        job = client.insert_rows_json(table_id_full, records)
        errors = job.errors

        if errors:
            logger.error(
                "bigquery_load_errors",
                error_count=len(errors),
                errors=errors[:5],  # Log first 5 errors
            )
            return len(records) - len(errors)

        logger.info(
            "bigquery_load_complete",
            loaded_count=len(records),
        )
        return len(records)

    except Exception as e:
        logger.error(
            "bigquery_load_failed",
            error=str(e),
        )
        raise


def main(
    search_query: str = "supplements",
    count: int = 20000,
    country: str = "US",
    project_id: str = "clean-patrol-496108-m9",
    dataset_id: str = "20k_supplement_trial",
    table_id: str = "ads_raw",
    output_file: str | None = None,
) -> None:
    """Main pipeline: scrape → normalize → load to BigQuery."""
    logger.info(
        "pipeline_start",
        search_query=search_query,
        count=count,
        project_id=project_id,
        dataset_id=dataset_id,
    )

    # Step 1: Scrape
    raw_items = scrape_ads(search_query=search_query, count=count, country=country)

    # Step 2: Normalize
    normalized = normalize_ads(raw_items)

    # Step 3: Optionally save to file for inspection
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(normalized, f, indent=2)
        logger.info("output_file_saved", path=str(output_path))

    # Step 4: Load to BigQuery
    loaded_count = load_to_bigquery(
        records=normalized,
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
    )

    logger.info(
        "pipeline_complete",
        loaded_count=loaded_count,
        table_id=f"{project_id}.{dataset_id}.{table_id}",
    )

    print(f"\n✅ Pipeline complete!")
    print(f"   Scraped: {len(raw_items)} ads")
    print(f"   Normalized: {len(normalized)} ads")
    print(f"   Loaded to BigQuery: {loaded_count} rows")
    print(f"   Table: {project_id}.{dataset_id}.{table_id}")
    print(f"\n   Query: bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM `{project_id}.{dataset_id}.{table_id}`'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape Supplements ads and load to BigQuery"
    )
    parser.add_argument(
        "--search-query",
        default="supplements",
        help="Search query for Meta Ad Library (default: supplements)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20000,
        help="Number of ads to scrape (default: 20000)",
    )
    parser.add_argument(
        "--country",
        default="US",
        help="Country code (default: US)",
    )
    parser.add_argument(
        "--project-id",
        default="clean-patrol-496108-m9",
        help="GCP project ID",
    )
    parser.add_argument(
        "--dataset-id",
        default="20k_supplement_trial",
        help="BigQuery dataset ID",
    )
    parser.add_argument(
        "--table-id",
        default="ads_raw",
        help="BigQuery table ID (default: ads_raw)",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Optional: save normalized ads to JSON file (e.g., /tmp/ads.json)",
    )

    args = parser.parse_args()

    main(
        search_query=args.search_query,
        count=args.count,
        country=args.country,
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        table_id=args.table_id,
        output_file=args.output_file,
    )
