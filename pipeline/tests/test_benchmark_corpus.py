from __future__ import annotations

from pipeline.model_training.benchmark_corpus import build_benchmark_review


def _ad(ad_id: str, page: str) -> dict:
    return {
        "ad_archive_id": ad_id,
        "page_id": page,
        "page_name": page,
        "image_urls": [f"https://example.com/{ad_id}.jpg"],
    }


def _row(ad_id: str, page: str, days: int) -> dict:
    return {
        "ad_id": ad_id,
        "page_id": page,
        "days_active": days,
        "collation_count": 1,
        "brand_scaling_count": 1,
        "product_subcategory": "protein",
    }


def test_benchmark_candidates_require_manual_taxonomy_and_maturity():
    ads = [_ad("approved", "a"), _ad("unlabeled", "b"), _ad("young", "c")]
    rows = [
        _row("approved", "a", 90),
        _row("unlabeled", "b", 180),
        _row("young", "c", 10),
    ]
    labels = [{"ad_id": "approved", "is_supplement": True}]
    result = build_benchmark_review(ads, rows, labels, minimum_days_active=30)
    assert [row["ad_id"] for row in result] == ["approved"]
    assert result[0]["approved_for_craft_reference"] is None


def test_benchmark_candidates_diversify_advertisers_before_refill():
    ads = [_ad("a1", "a"), _ad("a2", "a"), _ad("b1", "b")]
    rows = [_row("a1", "a", 100), _row("a2", "a", 90), _row("b1", "b", 80)]
    labels = [{"ad_id": ad["ad_archive_id"], "is_supplement": True} for ad in ads]
    result = build_benchmark_review(ads, rows, labels, candidate_count=3)
    assert result[0]["page_id"] == "a"
    assert result[1]["page_id"] == "b"
    assert result[2]["page_id"] == "a"
