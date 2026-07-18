"""Tests for ingestion/apify_client.py (with injected mock)."""

from __future__ import annotations

from ingestion.apify_client import run_ad_scrape


class TestRunAdScrape:
    """Test the run_ad_scrape function with injected mock."""

    def test_run_with_injected_fn(self) -> None:
        """Test that run_fn is called when provided."""

        def mock_run(
            search_query: str, count: int, actor_id: str | None, country: str
        ) -> list[dict]:
            return [
                {
                    "ad_archive_id": "123",
                    "page_name": "Test Brand",
                    "snapshot": {"title": "Test Ad"},
                },
            ]

        result = run_ad_scrape(
            search_query="linkedin",
            count=10,
            run_fn=mock_run,
        )

        assert len(result) == 1
        assert result[0]["ad_archive_id"] == "123"

    def test_run_with_injected_fn_multiple_ads(self) -> None:
        """Test that run_fn returns multiple ads."""

        def mock_run(
            search_query: str, count: int, actor_id: str | None, country: str
        ) -> list[dict]:
            return [
                {
                    "ad_archive_id": f"ad_{i}",
                    "page_name": f"Brand {i}",
                    "snapshot": {},
                }
                for i in range(5)
            ]

        result = run_ad_scrape(
            search_query="linkedin",
            count=10,
            run_fn=mock_run,
        )

        assert len(result) == 5
        assert all(r["ad_archive_id"] for r in result)

    def test_run_with_injected_empty_result(self) -> None:
        """Test that run_fn can return empty results."""

        def mock_run(
            search_query: str, count: int, actor_id: str | None, country: str
        ) -> list[dict]:
            return []

        result = run_ad_scrape(
            search_query="nonexistent",
            count=10,
            run_fn=mock_run,
        )

        assert result == []

    def test_run_fn_receives_correct_arguments(self) -> None:
        """Test that the injected run_fn receives the right arguments."""
        captured_args: dict = {}

        def mock_run(
            search_query: str, count: int, actor_id: str | None, country: str
        ) -> list[dict]:
            captured_args["search_query"] = search_query
            captured_args["count"] = count
            captured_args["actor_id"] = actor_id
            captured_args["country"] = country
            return []

        run_ad_scrape(
            search_query="apple",
            count=50,
            actor_id="custom/actor",
            country="IN",
            run_fn=mock_run,
        )

        assert captured_args["search_query"] == "apple"
        assert captured_args["count"] == 50
        assert captured_args["actor_id"] == "custom/actor"
        assert captured_args["country"] == "IN"

    def test_run_fn_default_country(self) -> None:
        """Test that country defaults to US when not provided."""
        captured_args: dict = {}

        def mock_run(
            search_query: str, count: int, actor_id: str | None, country: str
        ) -> list[dict]:
            captured_args["country"] = country
            return []

        run_ad_scrape(
            search_query="test",
            count=10,
            run_fn=mock_run,
        )

        assert captured_args["country"] == "US"
