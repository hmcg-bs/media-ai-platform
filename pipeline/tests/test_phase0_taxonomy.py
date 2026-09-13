from __future__ import annotations

from pipeline.validation.phase0_validator import (
    apply_manual_taxonomy_gate,
    stratified_validation_sample,
    taxonomy_review_strata,
)


def _ad(ad_id: str, query: str, title: str = "Supplement") -> dict:
    return {
        "ad_archive_id": ad_id,
        "title": title,
        "body": "",
        "page_name": "Brand",
        "search_queries": [query],
        "image_urls": ["https://example.com/image.jpg"],
    }


def test_review_strata_flags_risk_without_assigning_a_label():
    strata = taxonomy_review_strata(_ad("1", "probiotics", "Daily dog probiotic"))
    assert strata == {"query:probiotics", "candidate:probiotic", "possible_pet"}


def test_review_strata_recovers_sampling_coverage_when_query_provenance_is_absent():
    ad = _ad("1", "", "Daily magnesium capsule")
    ad["search_queries"] = []
    assert taxonomy_review_strata(ad) == {"candidate:magnesium"}


def test_stratified_sample_covers_small_query_buckets_before_refilling():
    ads = [
        _ad("c1", "collagen"),
        _ad("c2", "collagen"),
        _ad("p1", "protein"),
        _ad("m1", "magnesium"),
    ]
    sample = stratified_validation_sample(ads, sample_size=3, seed=42)
    assert {ad["search_queries"][0] for ad in sample} == {"collagen", "protein", "magnesium"}


def test_manual_gate_fails_closed_for_unlabeled_ads():
    ads = [_ad("yes", "protein"), _ad("no", "protein"), _ad("unknown", "protein")]
    labels = [
        {"ad_id": "yes", "is_supplement": True, "supplement_subcategory": "protein"},
        {"ad_id": "no", "is_supplement": False},
    ]
    accepted, counts = apply_manual_taxonomy_gate(ads, labels)
    assert [ad["ad_archive_id"] for ad in accepted] == ["yes"]
    assert counts == {"accepted": 1, "rejected": 1, "unlabeled": 1}
