"""Append-only GCP persistence for competitor ads, lifecycle, and features.

BigQuery is an event store here, not a mutable checkpoint. Immutable snapshots
avoid last-writer-wins corruption when scrape, extraction, and polling jobs run
concurrently. Deterministic insert IDs make client retries idempotent; queries
deduplicate defensively because BigQuery streaming de-duplication is best effort.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)

ADS_TABLE = "competitor_ad_snapshots"
FEATURES_TABLE = "ad_feature_snapshots"
OBSERVATIONS_TABLE = "ad_lifecycle_observations"
SURVIVAL_REPORTS_TABLE = "survival_validation_reports"
MAX_INSERT_ROWS = 500
MAX_INSERT_BYTES = 8 * 1024 * 1024


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _schema_fields() -> dict[str, list[tuple[str, str, str]]]:
    return {
        ADS_TABLE: [
            ("snapshot_id", "STRING", "REQUIRED"),
            ("ad_id", "STRING", "REQUIRED"),
            ("page_id", "STRING", "NULLABLE"),
            ("page_name", "STRING", "NULLABLE"),
            ("start_date", "DATE", "NULLABLE"),
            ("reported_stop_date", "DATE", "NULLABLE"),
            ("is_active", "BOOLEAN", "NULLABLE"),
            ("observed_at", "TIMESTAMP", "REQUIRED"),
            ("content_hash", "STRING", "REQUIRED"),
            ("search_queries", "STRING", "REPEATED"),
            ("ad_payload", "JSON", "REQUIRED"),
        ],
        FEATURES_TABLE: [
            ("feature_id", "STRING", "REQUIRED"),
            ("ad_id", "STRING", "REQUIRED"),
            ("extracted_at", "TIMESTAMP", "REQUIRED"),
            ("schema_version", "STRING", "REQUIRED"),
            ("content_hash", "STRING", "REQUIRED"),
            ("feature_payload", "JSON", "REQUIRED"),
            ("matrix_payload", "JSON", "NULLABLE"),
        ],
        OBSERVATIONS_TABLE: [
            ("observation_id", "STRING", "REQUIRED"),
            ("run_id", "STRING", "REQUIRED"),
            ("ad_id", "STRING", "REQUIRED"),
            ("page_id", "STRING", "NULLABLE"),
            ("observed_at", "TIMESTAMP", "REQUIRED"),
            ("observed_status", "STRING", "REQUIRED"),
            ("evidence", "STRING", "REQUIRED"),
            ("reported_start_date", "DATE", "NULLABLE"),
            ("reported_stop_date", "DATE", "NULLABLE"),
            ("poll_source", "STRING", "REQUIRED"),
            ("poll_complete", "BOOLEAN", "REQUIRED"),
            ("raw_payload", "JSON", "NULLABLE"),
        ],
        SURVIVAL_REPORTS_TABLE: [
            ("report_id", "STRING", "REQUIRED"),
            ("computed_at", "TIMESTAMP", "REQUIRED"),
            ("input_hash", "STRING", "REQUIRED"),
            ("report_payload", "JSON", "REQUIRED"),
        ],
    }


def _row_batches(
    rows: list[dict[str, Any]],
    max_rows: int = MAX_INSERT_ROWS,
    max_bytes: int = MAX_INSERT_BYTES,
) -> Iterable[list[dict[str, Any]]]:
    """Stay below BigQuery's 10 MiB insertAll request limit with headroom."""
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for row in rows:
        # Include conservative framing/row-ID overhead beyond JSON content.
        row_bytes = len(_canonical_json(row).encode()) + 256
        if batch and (len(batch) >= max_rows or batch_bytes + row_bytes > max_bytes):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        yield batch


class BigQueryWarehouse:
    """Small BigQuery boundary with injectable client for offline tests."""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        location: str | None = None,
        client: Any | None = None,
    ) -> None:
        settings = get_settings()
        self.project_id = project_id or settings.gcp_project_id
        self.dataset_id = dataset_id or settings.bigquery_dataset
        self.location = location or settings.gcp_region
        if not self.project_id:
            raise ValueError("GCP_PROJECT_ID is required for BigQuery persistence")
        if client is None:
            from google.cloud import bigquery

            client = bigquery.Client(project=self.project_id, location=self.location)
        self.client = client

    @property
    def dataset_ref(self) -> str:
        return f"{self.project_id}.{self.dataset_id}"

    def table_ref(self, table: str) -> str:
        return f"{self.dataset_ref}.{table}"

    def ensure_schema(self) -> None:
        """Create the dataset/tables if absent; tolerate concurrent creators."""
        from google.api_core.exceptions import Conflict, NotFound
        from google.cloud import bigquery

        dataset = bigquery.Dataset(self.dataset_ref)
        dataset.location = self.location
        try:
            self.client.get_dataset(self.dataset_ref)
        except NotFound:
            try:
                self.client.create_dataset(dataset)
            except Conflict:
                pass
        for name, fields in _schema_fields().items():
            schema = [bigquery.SchemaField(*field) for field in fields]
            table = bigquery.Table(self.table_ref(name), schema=schema)
            table.time_partitioning = bigquery.TimePartitioning(
                field=(
                    "observed_at"
                    if name in {ADS_TABLE, OBSERVATIONS_TABLE}
                    else "extracted_at"
                    if name == FEATURES_TABLE
                    else "computed_at"
                )
            )
            table.clustering_fields = ["ad_id"] if name != SURVIVAL_REPORTS_TABLE else None
            try:
                self.client.get_table(self.table_ref(name))
            except NotFound:
                try:
                    self.client.create_table(table)
                except Conflict:
                    pass

    def _append(self, table: str, rows: list[dict[str, Any]], id_field: str) -> int:
        if not rows:
            return 0
        inserted = 0
        for batch in _row_batches(rows):
            errors = self.client.insert_rows_json(
                self.table_ref(table), batch, row_ids=[r[id_field] for r in batch]
            )
            if errors:
                raise RuntimeError(f"BigQuery insert failed for {table}: {errors[:5]}")
            inserted += len(batch)
        logger.info("bigquery_rows_appended", table=self.table_ref(table), row_count=inserted)
        return inserted

    def append_ads(self, ads: Iterable[dict[str, Any]], observed_at: str | None = None) -> int:
        observed_at = observed_at or datetime.now(tz=UTC).isoformat()
        rows = []
        for ad in ads:
            ad_id = str(ad.get("ad_archive_id") or "")
            if not ad_id:
                continue
            payload = {k: v for k, v in ad.items() if k != "local_image_path"}
            content_hash = _sha256(payload)
            source_observed_at = ad.get("ingested_at") or observed_at
            rows.append(
                {
                    "snapshot_id": _sha256([ad_id, source_observed_at, content_hash]),
                    "ad_id": ad_id,
                    "page_id": str(ad.get("page_id") or ""),
                    "page_name": ad.get("page_name") or "",
                    "start_date": ad.get("start_date"),
                    "reported_stop_date": ad.get("end_date"),
                    "is_active": ad.get("is_active"),
                    "observed_at": source_observed_at,
                    "content_hash": content_hash,
                    "search_queries": list(ad.get("search_queries") or []),
                    "ad_payload": _canonical_json(payload),
                }
            )
        return self._append(ADS_TABLE, rows, "snapshot_id")

    def append_features(
        self,
        feature_rows: Iterable[dict[str, Any]],
        matrix_by_ad_id: dict[str, dict[str, Any]] | None = None,
        schema_version: str = "master-output-v1",
        extracted_at: str | None = None,
    ) -> int:
        extracted_at = extracted_at or datetime.now(tz=UTC).isoformat()
        matrix_by_ad_id = matrix_by_ad_id or {}
        rows = []
        for feature in feature_rows:
            ad_id = str(feature.get("ad_id") or "")
            if not ad_id:
                continue
            matrix = matrix_by_ad_id.get(ad_id)
            content_hash = _sha256({"feature": feature, "matrix": matrix})
            rows.append(
                {
                    "feature_id": _sha256([ad_id, schema_version, content_hash]),
                    "ad_id": ad_id,
                    "extracted_at": extracted_at,
                    "schema_version": schema_version,
                    "content_hash": content_hash,
                    "feature_payload": _canonical_json(feature),
                    "matrix_payload": _canonical_json(matrix) if matrix is not None else None,
                }
            )
        return self._append(FEATURES_TABLE, rows, "feature_id")

    def append_observations(self, rows: list[dict[str, Any]]) -> int:
        serialized = [
            {
                **row,
                "raw_payload": (
                    _canonical_json(row["raw_payload"])
                    if row.get("raw_payload") is not None
                    else None
                ),
            }
            for row in rows
        ]
        return self._append(OBSERVATIONS_TABLE, serialized, "observation_id")

    def append_survival_report(self, report: dict[str, Any]) -> int:
        computed_at = report.get("computed_at") or datetime.now(tz=UTC).isoformat()
        input_hash = str(report.get("input_hash") or _sha256(report))
        row = {
            "report_id": _sha256([computed_at, input_hash]),
            "computed_at": computed_at,
            "input_hash": input_hash,
            "report_payload": _canonical_json(report),
        }
        return self._append(SURVIVAL_REPORTS_TABLE, [row], "report_id")

    def fetch_tracked_ads(self) -> list[dict[str, Any]]:
        query = f"""
            SELECT ad_payload
            FROM `{self.table_ref(ADS_TABLE)}`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ad_id ORDER BY observed_at DESC) = 1
        """
        rows = []
        for row in self.client.query(query).result():
            payload = row["ad_payload"]
            rows.append(json.loads(payload) if isinstance(payload, str) else dict(payload))
        return rows

    def fetch_observations(self) -> list[dict[str, Any]]:
        query = f"""
            SELECT * EXCEPT(row_number)
            FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY observation_id ORDER BY observed_at DESC
              ) AS row_number
              FROM `{self.table_ref(OBSERVATIONS_TABLE)}`
            )
            WHERE row_number = 1
            ORDER BY ad_id, observed_at
        """
        return [dict(row.items()) for row in self.client.query(query).result()]


def load_local_artifacts(
    ads_file: Path,
    step2_dir: Path,
    matrix_file: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    ads = json.loads(ads_file.read_text())
    features = [json.loads(path.read_text()) for path in sorted(step2_dir.glob("*.json"))]
    matrix_by_id: dict[str, dict[str, Any]] = {}
    if matrix_file and matrix_file.exists():
        matrix_by_id = {str(row["ad_id"]): row for row in json.loads(matrix_file.read_text())}
    return ads, features, matrix_by_id
