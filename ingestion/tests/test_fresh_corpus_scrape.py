"""Tests for ingestion/fresh_corpus_scrape.py -- offline, injected ApifyClient
via monkeypatch, no real Apify calls."""

from __future__ import annotations

from unittest.mock import patch

from ingestion.fresh_corpus_scrape import scrape_and_normalize


def _raw_item(ad_id: str, with_image: bool = True) -> dict:
    return {
        "ad_archive_id": ad_id,
        "page_id": "1",
        "page_name": "Brand",
        "start_date": "2026-01-01",
        "end_date": None,
        "collation_count": 1,
        "publisher_platform": ["facebook"],
        "snapshot": {
            "title": "Ad",
            "body": {"text": "Buy now"},
            "images": (
                [{"original_image_url": "https://cdn.example.com/x.jpg"}] if with_image else []
            ),
        },
    }


class TestScrapeAndNormalize:
    def test_dedupes_across_queries_by_ad_archive_id(self):
        def fake_run_ad_scrape(self, search_query, count, actor_id=None, country="US"):
            return [_raw_item("1"), _raw_item("2")]

        with patch("ingestion.apify_client.ApifyClient.run_ad_scrape", fake_run_ad_scrape):
            with patch("ingestion.fresh_corpus_scrape.get_settings") as mock_settings:
                mock_settings.return_value.apify_api_token = "fake-token"
                ads = scrape_and_normalize(queries=("q1", "q2"), count_per_query=10)

        assert len(ads) == 2  # not 4 -- same two ads seen under both queries

    def test_ads_without_images_are_dropped(self):
        def fake_run_ad_scrape(self, search_query, count, actor_id=None, country="US"):
            return [_raw_item("1", with_image=True), _raw_item("2", with_image=False)]

        with patch("ingestion.apify_client.ApifyClient.run_ad_scrape", fake_run_ad_scrape):
            with patch("ingestion.fresh_corpus_scrape.get_settings") as mock_settings:
                mock_settings.return_value.apify_api_token = "fake-token"
                ads = scrape_and_normalize(queries=("q1",), count_per_query=10)

        assert len(ads) == 1
        assert ads[0]["ad_archive_id"] == "1"

    def test_one_failed_query_does_not_kill_the_whole_scrape(self):
        call_count = {"n": 0}

        def fake_run_ad_scrape(self, search_query, count, actor_id=None, country="US"):
            call_count["n"] += 1
            if search_query == "bad_query":
                raise RuntimeError("actor failed")
            return [_raw_item("1")]

        with patch("ingestion.apify_client.ApifyClient.run_ad_scrape", fake_run_ad_scrape):
            with patch("ingestion.fresh_corpus_scrape.get_settings") as mock_settings:
                mock_settings.return_value.apify_api_token = "fake-token"
                ads = scrape_and_normalize(queries=("bad_query", "good_query"), count_per_query=10)

        assert call_count["n"] == 2
        assert len(ads) == 1

    def test_missing_api_token_raises(self):
        with patch("ingestion.fresh_corpus_scrape.get_settings") as mock_settings:
            mock_settings.return_value.apify_api_token = ""
            try:
                scrape_and_normalize(queries=("q1",))
                raise AssertionError("expected ValueError")
            except ValueError:
                pass
