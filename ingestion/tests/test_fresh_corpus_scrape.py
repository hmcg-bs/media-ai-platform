"""Tests for ingestion/fresh_corpus_scrape.py -- offline, injected ApifyClient
via monkeypatch, no real Apify calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ingestion.fresh_corpus_scrape import scrape_and_normalize, validate_resume_state


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
    class FakeClient:
        def __init__(self, rows_by_query):
            self.rows_by_query = rows_by_query
            self.calls = []

        def run_ad_scrape(self, search_query, count, actor_id=None, country="US"):
            self.calls.append(search_query)
            return self.rows_by_query[search_query]

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

    def test_resume_skips_completed_queries_and_checkpoints_each_success(self):
        client = self.FakeClient({
            "already-done": [_raw_item("duplicate")],
            "new": [_raw_item("2")],
        })
        completed = {"already-done"}
        checkpoints = []

        ads = scrape_and_normalize(
            queries=("already-done", "new"),
            count_per_query=10,
            client=client,
            existing_ads=[{"ad_archive_id": "1", "image_urls": ["existing.jpg"]}],
            completed_queries=completed,
            checkpoint=lambda rows, done: checkpoints.append((list(rows), set(done))),
        )

        assert client.calls == ["new"]
        assert {ad["ad_archive_id"] for ad in ads} == {"1", "2"}
        assert checkpoints[-1][1] == {"already-done", "new"}


def test_resume_rejects_a_different_corpus_definition():
    state = {"queries": ["vitamins"], "count_per_query": 100, "country": "US"}
    with pytest.raises(ValueError, match="queries, count_per_query, country"):
        validate_resume_state(state, ("protein",), 200, "SG")
