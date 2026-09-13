"""Poll tracked Meta Ad Library IDs and append lifecycle observations."""

from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ingestion.ad_lifecycle import build_poll_observations, resolve_lifecycle
from ingestion.apify_client import ApifyClient
from ingestion.gcp_warehouse import BigQueryWarehouse
from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.config import get_settings


def _read_ads(path: Path | None, warehouse: BigQueryWarehouse) -> list[dict[str, Any]]:
    return json.loads(path.read_text()) if path else warehouse.fetch_tracked_ads()


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll tracked public Meta ad IDs via Apify.")
    parser.add_argument("--ads", type=Path, help="Optional local corpus; default is BigQuery")
    parser.add_argument("--out", type=Path, default=Path("data/ad_lifecycle_poll.json"))
    parser.add_argument("--dataset")
    parser.add_argument("--country", default="US")
    parser.add_argument("--batch-size", type=int, default=10, help="Pages per actor run")
    parser.add_argument("--required-misses", type=int, default=2)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.required_misses < 2:
        parser.error("--required-misses must be at least 2 to avoid false stop events")

    settings = get_settings()
    if not settings.apify_api_token:
        parser.error("APIFY_API_TOKEN is not configured")
    warehouse = BigQueryWarehouse(dataset_id=args.dataset)
    warehouse.ensure_schema()
    ads = _read_ads(args.ads, warehouse)
    tracked = [ad for ad in ads if ad.get("ad_archive_id") and not ad.get("end_date")]
    ads_by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ad in tracked:
        if ad.get("page_id"):
            ads_by_page[str(ad["page_id"])].append(ad)
    page_ids = sorted(ads_by_page)
    run_id = f"poll-{datetime.now(tz=UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {"run_id": run_id, "observations": []}

    with exclusive_output(args.out):
        client = ApifyClient(settings.apify_api_token)
        for offset in range(0, len(page_ids), args.batch_size):
            batch_pages = page_ids[offset : offset + args.batch_size]
            batch = [ad for page_id in batch_pages for ad in ads_by_page[page_id]]
            # Allow headroom for newly launched page ads so tracked IDs are
            # not excluded merely because the actor reaches its result cap.
            actor_result = client.poll_ad_pages(
                batch_pages, count=max(10, len(batch) * 2), country=args.country
            )
            raw = actor_result.items
            returned_page_ids = {str(row.get("page_id") or row.get("pageId") or "") for row in raw}
            if not actor_result.sources_exhausted:
                returned_page_ids = set()
            observed_at = datetime.now(tz=UTC).isoformat()
            observations = build_poll_observations(
                batch,
                raw,
                run_id=run_id,
                observed_at=observed_at,
                complete_page_ids=returned_page_ids,
            )
            # Save paid API results locally before the remote write. A failed
            # BigQuery insert can then be retried without repeating Apify.
            state["observations"].extend(observations)
            atomic_write_json(args.out, state)
            warehouse.append_observations(observations)

    history = warehouse.fetch_observations()
    lifecycle = resolve_lifecycle(history, required_consecutive_misses=args.required_misses)
    counts: dict[str, int] = {}
    for row in lifecycle.values():
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    print(
        f"Poll complete: {len(tracked)} IDs across {len(page_ids)} pages; "
        f"lifecycle={counts}; run_id={run_id}"
    )


if __name__ == "__main__":
    main()
