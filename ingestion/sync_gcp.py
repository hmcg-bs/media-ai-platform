"""Persist local ad and extraction artifacts to the append-only GCP warehouse."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from ingestion.ad_lifecycle import build_initial_observations
from ingestion.gcp_warehouse import BigQueryWarehouse, load_local_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Meta ad metadata/features to BigQuery.")
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--step2-out", type=Path, required=True)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--dataset")
    parser.add_argument("--schema-version", default="master-output-v1")
    args = parser.parse_args()

    if not args.ads.exists():
        parser.error(f"ads corpus not found: {args.ads}")
    if not args.step2_out.is_dir():
        parser.error(f"Step 2 artifact directory not found: {args.step2_out}")

    ads, features, matrix = load_local_artifacts(args.ads, args.step2_out, args.matrix)
    warehouse = BigQueryWarehouse(dataset_id=args.dataset)
    warehouse.ensure_schema()
    observed_at = datetime.now(tz=UTC).isoformat()
    ads_written = warehouse.append_ads(ads, observed_at=observed_at)
    lifecycle_written = warehouse.append_observations(
        build_initial_observations(ads, run_id="initial-corpus", observed_at=observed_at)
    )
    features_written = warehouse.append_features(
        features,
        matrix_by_ad_id=matrix,
        schema_version=args.schema_version,
        extracted_at=observed_at,
    )
    print(
        f"Synced {ads_written} ad snapshots, {lifecycle_written} lifecycle observations, "
        f"and {features_written} feature snapshots to {warehouse.dataset_ref}"
    )


if __name__ == "__main__":
    main()
