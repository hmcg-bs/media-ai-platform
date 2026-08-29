"""Tests for ingestion/refresh_image_urls.py (with injected mock scrape)."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.refresh_image_urls import refresh_image_urls


def _mock_run(fresh_items: list[dict]):
    def run_fn(search_query: str, count: int, actor_id: str | None, country: str) -> list[dict]:
        return fresh_items

    return run_fn


class TestRefreshImageUrls:
    def test_matching_ad_id_gets_fresh_image_urls(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "111", "image_urls": ["https://stale.example.com/old.jpg"]},
        ]))

        run_fn = _mock_run([
            {
                "ad_archive_id": "111",
                "snapshot": {"images": [{"original_image_url": "https://fresh.example.com/new.jpg"}]},
            },
        ])

        out_file = tmp_path / "out.json"
        total, refreshed = refresh_image_urls(
            ads_file, out_file, count=10, search_query="supplements", run_fn=run_fn
        )

        assert total == 1
        assert refreshed == 1
        result = json.loads(out_file.read_text())
        assert result[0]["image_urls"] == ["https://fresh.example.com/new.jpg"]

    def test_non_matching_ad_keeps_stale_url_unchanged(self, tmp_path: Path) -> None:
        """An ad not resurfaced by the fresh scrape keeps its (unusable)
        stale URL rather than being dropped or nulled — Step 2's own fetch
        stage is what skips it later, this function never removes ads."""
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "222", "image_urls": ["https://stale.example.com/old.jpg"]},
        ]))

        run_fn = _mock_run([
            {"ad_archive_id": "999", "snapshot": {"images": [{"original_image_url": "https://fresh.example.com/x.jpg"}]}},
        ])

        out_file = tmp_path / "out.json"
        total, refreshed = refresh_image_urls(
            ads_file, out_file, count=10, search_query="supplements", run_fn=run_fn
        )

        assert total == 1
        assert refreshed == 0
        result = json.loads(out_file.read_text())
        assert result[0]["image_urls"] == ["https://stale.example.com/old.jpg"]

    def test_fresh_item_with_no_images_does_not_overwrite(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "333", "image_urls": ["https://stale.example.com/old.jpg"]},
        ]))

        run_fn = _mock_run([
            {"ad_archive_id": "333", "snapshot": {"images": []}},
        ])

        out_file = tmp_path / "out.json"
        _, refreshed = refresh_image_urls(
            ads_file, out_file, count=10, search_query="supplements", run_fn=run_fn
        )

        assert refreshed == 0
        result = json.loads(out_file.read_text())
        assert result[0]["image_urls"] == ["https://stale.example.com/old.jpg"]

    def test_preserves_other_ad_fields(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {
                "ad_archive_id": "444",
                "image_urls": ["https://stale.example.com/old.jpg"],
                "product_page": {"price": 29.99, "brand_name": "Test Brand"},
                "title": "Original Title",
            },
        ]))

        run_fn = _mock_run([
            {"ad_archive_id": "444", "snapshot": {"images": [{"original_image_url": "https://fresh.example.com/x.jpg"}]}},
        ])

        out_file = tmp_path / "out.json"
        refresh_image_urls(ads_file, out_file, count=10, search_query="supplements", run_fn=run_fn)

        result = json.loads(out_file.read_text())
        assert result[0]["product_page"] == {"price": 29.99, "brand_name": "Test Brand"}
        assert result[0]["title"] == "Original Title"
