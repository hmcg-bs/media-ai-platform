#!/usr/bin/env python3
"""
Standalone script to load supplements ads from JSON to BigQuery.
Run locally: python load_to_bigquery.py
"""

import json
from pathlib import Path

# Minimal import to avoid pandas circular import
import sys
sys.path.insert(0, str(Path(__file__).parent))

from google.cloud import bigquery


def load_json_to_bigquery(
    json_file: str,
    project_id: str,
    dataset_id: str,
    table_id: str,
):
    """Load normalized ads from JSON to BigQuery."""
    print(f"Loading {json_file} to {project_id}.{dataset_id}.{table_id}...")

    # Read JSON
    with open(json_file) as f:
        records = json.load(f)

    print(f"✅ Loaded {len(records)} records from JSON")

    # Initialize BigQuery client
    client = bigquery.Client(project=project_id)
    table_id_full = f"{project_id}.{dataset_id}.{table_id}"

    # Add metadata
    for record in records:
        record["run_id"] = "run_001"
        from datetime import datetime
        record["ingested_at"] = datetime.utcnow().isoformat() + "Z"

    # Load to BigQuery
    print(f"⏳ Loading {len(records)} rows to BigQuery...")
    job = client.insert_rows_json(table_id_full, records)

    if job.errors:
        print(f"❌ {len(job.errors)} rows failed to load")
        for error in job.errors[:5]:
            print(f"   Error: {error}")
        return len(records) - len(job.errors)

    print(f"✅ Successfully loaded {len(records)} rows to BigQuery")
    print(f"   Table: {table_id_full}")
    print(f"   Query: bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM `{table_id_full}`'")
    return len(records)


if __name__ == "__main__":
    load_json_to_bigquery(
        json_file=str(Path(__file__).parent / "data" / "supplements_ads.json"),
        project_id="clean-patrol-496108-m9",
        dataset_id="20k_supplement_trial",
        table_id="ads_raw",
    )
