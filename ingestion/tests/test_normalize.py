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

    def test_constructed_snapshot_url_wins_over_generic_url_echo(self) -> None:
        """Regression: `raw.get("url")` isn't a per-ad field — it's the
        Apify actor echoing back its own scrape input (the same generic
        search-query URL for every item in a run, e.g.
        "?q=supplements&sort_data...", never a per-ad link). Confirmed live
        against the real corpus: this actor's output never populates
        `ad_snapshot_url`, so every one of 2,736 ads ended up with the
        identical generic search URL — useless for reaching a specific ad's
        own detail page. The constructed `?id={ad_id}` form is Meta's real,
        stable per-ad URL and must win whenever `ad_snapshot_url` is
        absent, even though a (wrong) `url` field is also present."""
        ad = normalize_ad({
            "ad_archive_id": "999",
            "url": "https://www.facebook.com/ads/library/?q=supplements&sort_data[mode]=total_impressions",
            "snapshot": {},
        })
        assert ad.snapshot_url == "https://www.facebook.com/ads/library/?id=999"

    def test_url_field_used_only_when_no_ad_id_available(self) -> None:
        ad = normalize_ad({
            "ad_archive_id": "",
            "url": "https://www.facebook.com/ads/library/?q=supplements",
            "snapshot": {},
        })
        assert ad.snapshot_url == "https://www.facebook.com/ads/library/?q=supplements"

    def test_snapshot_url_defaults_to_empty_string_not_none(self) -> None:
        ad = normalize_ad({"ad_archive_id": "", "snapshot": {}})
        assert ad.snapshot_url == ""

    def test_ingested_at_populated(self, canned_meta_ad_library_item: dict) -> None:
        before_normalized = normalize_ad(canned_meta_ad_library_item)
        # ingested_at should be a non-empty ISO string.
        assert before_normalized.ingested_at
        assert "T" in before_normalized.ingested_at
        assert "+" in before_normalized.ingested_at or "Z" in before_normalized.ingested_at


class TestVideoOnlyCards:
    """Regression: a live fresh-scrape run found a real ad whose only
    creative was a video-carousel "card" (video_hd_url +
    video_preview_image_url, no original_image_url/resized_image_url at
    all) -- normalize_ad silently produced image_urls=[] AND video_urls=[]
    for it, discarding the ad's entire creative even though a real,
    analyzable video + preview frame existed."""

    def _video_card_raw(self) -> dict:
        return {
            "ad_archive_id": "1",
            "snapshot": {
                "cards": [{
                    "video_hd_url": "https://video.example.com/hd.mp4",
                    "video_sd_url": "https://video.example.com/sd.mp4",
                    "video_preview_image_url": "https://cdn.example.com/preview.jpg",
                }],
                "images": [],
                "videos": [],
            },
        }

    def test_video_url_extracted_from_card_not_just_top_level_videos(self) -> None:
        ad = normalize_ad(self._video_card_raw())
        assert "https://video.example.com/hd.mp4" in ad.video_urls

    def test_video_preview_image_used_as_fallback_when_no_real_image(self) -> None:
        ad = normalize_ad(self._video_card_raw())
        assert ad.image_urls == ["https://cdn.example.com/preview.jpg"]

    def test_real_image_wins_over_video_preview_when_both_exist(self) -> None:
        raw = self._video_card_raw()
        raw["snapshot"]["images"] = [{"original_image_url": "https://cdn.example.com/real.jpg"}]
        ad = normalize_ad(raw)
        assert ad.image_urls == ["https://cdn.example.com/real.jpg"]

    def test_top_level_videos_still_work_as_before(self) -> None:
        raw = {
            "ad_archive_id": "1",
            "snapshot": {"videos": [{"video_hd_url": "https://video.example.com/hd.mp4"}]},
        }
        ad = normalize_ad(raw)
        assert ad.video_urls == ["https://video.example.com/hd.mp4"]


class TestDeliverySignals:
    """impressions/reach/spend/gated_type/regional_transparency -- Meta's own
    disclosure data. Confirmed live via a real Apify run that these come
    back None/-1 for ordinary US commercial ads (the kind this corpus is
    made of); still captured since a future EU-targeted or political/
    social-issue scrape could populate them for real."""

    def test_impressions_disclosed(self) -> None:
        ad = normalize_ad({
            "ad_archive_id": "1",
            "snapshot": {},
            "impressions_with_index": {"impressions_text": "10K-50K", "impressions_index": 4},
        })
        assert ad.impressions_text == "10K-50K"
        assert ad.impressions_index == 4

    def test_impressions_not_disclosed_normalizes_sentinel_to_none(self) -> None:
        """Regression: confirmed live Meta uses impressions_index=-1 as its
        own "not disclosed" sentinel, not a real bucket value -- must not
        be stored as a literal -1 that looks like real (if odd) data."""
        ad = normalize_ad({
            "ad_archive_id": "1",
            "snapshot": {},
            "impressions_with_index": {"impressions_text": None, "impressions_index": -1},
        })
        assert ad.impressions_text is None
        assert ad.impressions_index is None

    def test_missing_impressions_field_entirely(self) -> None:
        ad = normalize_ad({"ad_archive_id": "1", "snapshot": {}})
        assert ad.impressions_text is None
        assert ad.impressions_index is None

    def test_reach_estimate_and_spend_passthrough_when_present(self) -> None:
        ad = normalize_ad({
            "ad_archive_id": "1",
            "snapshot": {},
            "reach_estimate": "50K-100K",
            "spend": "$1K-$5K",
        })
        assert ad.reach_estimate == "50K-100K"
        assert ad.spend == "$1K-$5K"

    def test_reach_estimate_unexpected_shape_coerced_not_crashed(self) -> None:
        """reach_estimate/spend's real populated shape has never been
        observed live -- a non-str value (e.g. a nested dict or a number,
        if that's what Meta actually sends) must not raise a validation
        error the first time a real value appears."""
        ad = normalize_ad({
            "ad_archive_id": "1",
            "snapshot": {},
            "reach_estimate": {"low": 50000, "high": 100000},
        })
        assert ad.reach_estimate == "{'low': 50000, 'high': 100000}"

    def test_gated_type_captured(self) -> None:
        ad = normalize_ad({"ad_archive_id": "1", "snapshot": {}, "gated_type": "ELIGIBLE"})
        assert ad.gated_type == "ELIGIBLE"

    def test_regional_transparency_dict_captured(self) -> None:
        ad = normalize_ad({
            "ad_archive_id": "1",
            "snapshot": {},
            "transparency_by_location": {"eu_transparency": None, "uk_transparency": None},
        })
        assert ad.regional_transparency == {"eu_transparency": None, "uk_transparency": None}

    def test_regional_transparency_non_dict_normalized_to_none(self) -> None:
        ad = normalize_ad({"ad_archive_id": "1", "snapshot": {}, "transparency_by_location": "n/a"})
        assert ad.regional_transparency is None

    def test_all_delivery_signals_default_safely_on_minimal_ad(
        self, canned_meta_ad_library_item_minimal: dict
    ) -> None:
        ad = normalize_ad(canned_meta_ad_library_item_minimal)
        assert ad.impressions_text is None
        assert ad.impressions_index is None
        assert ad.reach_estimate is None
        assert ad.spend is None
        assert ad.gated_type is None
        assert ad.regional_transparency is None
