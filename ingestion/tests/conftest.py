"""Fixtures for ingestion tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def canned_meta_ad_library_item() -> dict:
    """A realistic Meta Ad Library item (from the Apify actor)."""
    return {
        "ad_archive_id": "23842834949284342",
        "adArchiveID": None,  # Can be either camelCase or snake_case.
        "page_id": "1234567890",
        "page_name": "Example Brand Co.",
        "start_date": 1704067200,  # 2024-01-01 in epoch seconds.
        "end_date": 1704153600,  # 2024-01-02 in epoch seconds (or None if still active).
        "is_active": False,
        "ad_active_status": False,
        "collation_count": 3,
        "publisher_platform": ["facebook", "instagram"],
        "publisherPlatform": None,
        "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=23842834949284342",
        "snapshot": {
            "title": "Example Ad Title",
            "body": {"text": "This is the main ad copy text."},
            "caption": "example.com",
            "link_url": "https://example.com/product",
            "cta_text": "Shop Now",
            "images": [
                {
                    "original_image_url": "https://cdn.example.com/original_image.jpg",
                    "resized_image_url": "https://cdn.example.com/resized_image.jpg",
                },
            ],
            "videos": [],
            "cards": [],
        },
    }


@pytest.fixture
def canned_meta_ad_library_item_with_video() -> dict:
    """A Meta Ad Library item with video and card-based images."""
    return {
        "ad_archive_id": "98765432109876543",
        "page_id": "1111111111",
        "page_name": "Another Brand",
        "start_date": "2024-06-01",  # ISO string variant.
        "end_date": None,  # Still active.
        "is_active": True,
        "collation_count": 1,
        "publisher_platform": "facebook",  # String instead of list.
        "snapshot": {
            "title": "Video Ad",
            "body": "Check out this video!",  # String instead of dict.
            "caption": "video.example.com",
            "link_url": "https://example.com/video",
            "cta_text": "Watch",
            "images": [],
            "videos": [
                {
                    "video_hd_url": "https://cdn.example.com/video_hd.mp4",
                    "video_sd_url": "https://cdn.example.com/video_sd.mp4",
                },
            ],
            "cards": [
                {
                    "original_image_url": "https://cdn.example.com/card_image.png",
                },
            ],
        },
    }


@pytest.fixture
def canned_meta_ad_library_item_minimal() -> dict:
    """A minimal Ad Library item (sparse fields)."""
    return {
        "id": "abcdef123456",  # Alternative ad_id field.
        "snapshot": {},
    }
