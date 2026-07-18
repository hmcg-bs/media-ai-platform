"""Tests for ingestion/normalize.py."""

from __future__ import annotations

from ingestion.normalize import _days_active, _to_iso_date, normalize_ad


class TestToIsoDate:
    """Test date conversion helpers."""

    def test_epoch_seconds_int(self) -> None:
        result = _to_iso_date(1704067200)
        assert result == "2024-01-01"

    def test_epoch_seconds_float(self) -> None:
        result = _to_iso_date(1704067200.5)
        assert result == "2024-01-01"

    def test_epoch_seconds_numeric_string(self) -> None:
        result = _to_iso_date("1704067200")
        assert result == "2024-01-01"

    def test_iso_string(self) -> None:
        result = _to_iso_date("2024-01-01T00:00:00Z")
        assert result == "2024-01-01"

    def test_iso_string_with_timezone(self) -> None:
        result = _to_iso_date("2024-01-01T00:00:00+00:00")
        assert result == "2024-01-01"

    def test_partial_iso_string(self) -> None:
        result = _to_iso_date("2024-01-01")
        assert result == "2024-01-01"

    def test_none(self) -> None:
        assert _to_iso_date(None) is None

    def test_empty_string(self) -> None:
        assert _to_iso_date("") is None

    def test_invalid_string(self) -> None:
        # Gracefully degrade: return first 10 chars or None.
        result = _to_iso_date("not-a-date-x")
        assert result == "not-a-date"


class TestDaysActive:
    """Test longevity computation."""

    def test_same_day(self) -> None:
        result = _days_active("2024-01-01", "2024-01-01")
        assert result == 0

    def test_one_day_apart(self) -> None:
        result = _days_active("2024-01-01", "2024-01-02")
        assert result == 1

    def test_seven_days_apart(self) -> None:
        result = _days_active("2024-01-01", "2024-01-08")
        assert result == 7

    def test_end_date_none_uses_today(self) -> None:
        # Should be a positive number (days since start to today).
        result = _days_active("2024-01-01", None)
        assert result >= 200  # Very permissive; just check it's reasonable.

    def test_no_start_date(self) -> None:
        assert _days_active(None, "2024-01-01") == 0
        assert _days_active("", "2024-01-01") == 0

    def test_invalid_start_date(self) -> None:
        result = _days_active("not-a-date", "2024-01-01")
        assert result == 0

    def test_invalid_end_date_uses_today(self) -> None:
        result = _days_active("2024-01-01", "not-a-date")
        assert result >= 200

    def test_negative_span_clamped_to_zero(self) -> None:
        # End before start: clamped to 0.
        result = _days_active("2024-01-08", "2024-01-01")
        assert result == 0


class TestNormalizeAd:
    """Test the main normalize_ad function."""

    def test_basic_normalization(self, canned_meta_ad_library_item: dict) -> None:
        result = normalize_ad(canned_meta_ad_library_item)

        assert result.ad_archive_id == "23842834949284342"
        assert result.page_id == "1234567890"
        assert result.page_name == "Example Brand Co."
        assert result.start_date == "2024-01-01"
        assert result.end_date == "2024-01-02"
        assert result.is_active is False
        assert result.days_active == 1
        assert result.collation_count == 3
        assert result.body == "This is the main ad copy text."
        assert result.title == "Example Ad Title"
        assert result.caption == "example.com"
        assert result.link_url == "https://example.com/product"
        assert result.cta_text == "Shop Now"
        assert result.publisher_platforms == ["facebook", "instagram"]
        assert "https://cdn.example.com/original_image.jpg" in result.image_urls
        assert result.snapshot_url == "https://www.facebook.com/ads/library/?id=23842834949284342"
        assert result.local_image_path is None
        assert result.ingested_at

    def test_video_and_card_images(self, canned_meta_ad_library_item_with_video: dict) -> None:
        result = normalize_ad(canned_meta_ad_library_item_with_video)

        assert result.ad_archive_id == "98765432109876543"
        assert result.page_name == "Another Brand"
        assert result.start_date == "2024-06-01"
        assert result.end_date is None
        assert result.is_active is True
        assert result.days_active >= 1
        assert result.collation_count == 1
        # Videos should be captured.
        assert "https://cdn.example.com/video_hd.mp4" in result.video_urls
        # Card image should be in image_urls.
        assert "https://cdn.example.com/card_image.png" in result.image_urls
        # String publisher_platform should be converted to list.
        assert result.publisher_platforms == ["facebook"]
        # Body as string should work.
        assert result.body == "Check out this video!"

    def test_minimal_ad(self, canned_meta_ad_library_item_minimal: dict) -> None:
        result = normalize_ad(canned_meta_ad_library_item_minimal)

        assert result.ad_archive_id == "abcdef123456"
        assert result.page_id == ""
        assert result.page_name == ""
        assert result.body == ""
        assert result.title == ""
        assert result.image_urls == []
        assert result.video_urls == []
        # Defaults should be safe.
        assert result.collation_count == 0
        assert result.days_active == 0

    def test_is_active_inference(self) -> None:
        # is_active not set, end_date is None → infer active.
        ad = normalize_ad({
            "snapshot": {},
            "end_date": None,
        })
        assert ad.is_active is True

        # is_active not set, end_date set → infer inactive.
        ad = normalize_ad({
            "snapshot": {},
            "end_date": "2024-01-01",
        })
        assert ad.is_active is False

    def test_ingested_at_populated(self, canned_meta_ad_library_item: dict) -> None:
        before_normalized = normalize_ad(canned_meta_ad_library_item)
        # ingested_at should be a non-empty ISO string.
        assert before_normalized.ingested_at
        assert "T" in before_normalized.ingested_at
        assert "+" in before_normalized.ingested_at or "Z" in before_normalized.ingested_at
