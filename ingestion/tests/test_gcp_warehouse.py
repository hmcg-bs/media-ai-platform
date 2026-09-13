from __future__ import annotations

import json

from ingestion.gcp_warehouse import ADS_TABLE, FEATURES_TABLE, BigQueryWarehouse, _row_batches


class FakeClient:
    def __init__(self):
        self.calls = []

    def insert_rows_json(self, table, rows, row_ids):
        self.calls.append((table, rows, row_ids))
        return []


def test_ad_snapshots_strip_local_path_and_have_stable_content_hash():
    client = FakeClient()
    warehouse = BigQueryWarehouse(project_id="project", dataset_id="dataset", client=client)
    ad = {
        "ad_archive_id": "1",
        "page_id": "p",
        "start_date": "2026-01-01",
        "is_active": True,
        "local_image_path": "/tmp/secret.jpg",
        "image_urls": ["https://x"],
    }
    assert warehouse.append_ads([ad], observed_at="2026-02-01T00:00:00Z") == 1
    table, rows, row_ids = client.calls[0]
    assert table.endswith(ADS_TABLE)
    assert "local_image_path" not in json.loads(rows[0]["ad_payload"])
    assert row_ids == [rows[0]["snapshot_id"]]


def test_ad_snapshot_retry_uses_source_ingestion_time_for_stable_id():
    client = FakeClient()
    warehouse = BigQueryWarehouse(project_id="project", dataset_id="dataset", client=client)
    ad = {"ad_archive_id": "1", "ingested_at": "2026-01-02T03:04:05Z"}
    warehouse.append_ads([ad], observed_at="2026-02-01T00:00:00Z")
    first = client.calls[-1][1][0]
    warehouse.append_ads([ad], observed_at="2026-03-01T00:00:00Z")
    second = client.calls[-1][1][0]
    assert first["snapshot_id"] == second["snapshot_id"]
    assert second["observed_at"] == ad["ingested_at"]


def test_feature_snapshot_id_is_content_addressed_not_timestamp_addressed():
    client = FakeClient()
    warehouse = BigQueryWarehouse(project_id="project", dataset_id="dataset", client=client)
    feature = {"ad_id": "1", "technical_metadata": {"width": 100}}
    warehouse.append_features([feature], extracted_at="2026-02-01T00:00:00Z")
    first = client.calls[-1][1][0]
    warehouse.append_features([feature], extracted_at="2026-03-01T00:00:00Z")
    second = client.calls[-1][1][0]
    assert client.calls[-1][0].endswith(FEATURES_TABLE)
    assert first["feature_id"] == second["feature_id"]
    assert first["extracted_at"] != second["extracted_at"]
    assert json.loads(second["feature_payload"]) == feature


def test_row_batches_respect_byte_budget_not_only_row_count():
    rows = [{"id": str(i), "payload": "x" * 80} for i in range(3)]
    batches = list(_row_batches(rows, max_rows=500, max_bytes=750))
    assert [len(batch) for batch in batches] == [2, 1]
    assert [row["id"] for batch in batches for row in batch] == ["0", "1", "2"]
