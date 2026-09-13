from __future__ import annotations

import numpy as np

from pipeline.model_training.evaluation import (
    longevity_benchmarks,
    segment_metrics,
    temporal_advertiser_split,
)


def test_temporal_advertiser_split_holds_out_new_brands_without_overlap() -> None:
    rows = [
        {"ad_id": "a1", "page_id": "old"},
        {"ad_id": "a2", "page_id": "old"},
        {"ad_id": "b1", "page_id": "middle"},
        {"ad_id": "c1", "page_id": "new"},
    ]
    dates = {"a1": "2025-01-01", "a2": "2026-01-01", "b1": "2025-06-01", "c1": "2026-06-01"}
    train, test, details = temporal_advertiser_split(rows, dates, test_fraction=1 / 3)

    assert {r["page_id"] for r in train} == {"old", "middle"}
    assert {r["page_id"] for r in test} == {"new"}
    assert details["advertiser_overlap"] == 0


def test_segment_metrics_gates_small_subcategories() -> None:
    rows = [
        {"ad_id": "1", "search_queries": ["creatine"]},
        {"ad_id": "2", "search_queries": ["creatine"]},
        {"ad_id": "3", "search_queries": ["vitamins"]},
    ]
    result = segment_metrics(
        rows, np.array([0.1, 0.9, 0.5]), np.array([0.2, 0.7, 0.4]), {}, minimum_size=2
    )
    assert result["subcategories"]["creatine"]["status"] == "ok"
    assert result["subcategories"]["creatine"]["mae"] == 0.15
    assert result["subcategories"]["vitamins"]["status"] == "insufficient_sample"


def test_segment_promotion_needs_rows_and_advertiser_diversity() -> None:
    rows = [
        {"ad_id": str(i), "page_id": f"brand-{i // 5}", "product_subcategory": "protein"}
        for i in range(100)
    ]
    actual = np.linspace(0, 1, 100)
    result = segment_metrics(rows, actual, actual.copy(), {}, minimum_size=10)
    segment = result["subcategories"]["protein"]

    assert segment["n"] == 100
    assert segment["n_advertisers"] == 20
    assert segment["promotion_supported"] is True


def test_classifier_admitted_rows_cannot_create_subcategory_evidence() -> None:
    rows = [
        {
            "ad_id": str(i),
            "page_id": f"brand-{i}",
            "taxonomy_label_source": "validated_classifier",
            "product_subcategory": "protein",
            "search_queries": ["protein"],
        }
        for i in range(100)
    ]
    actual = np.linspace(0, 1, 100)
    result = segment_metrics(rows, actual, actual.copy(), {}, minimum_size=10)

    assert result["subcategories"] == {}


def test_longevity_threshold_stays_null_when_all_ads_are_censored() -> None:
    rows = [
        {"ad_id": str(i), "days_active": i + 10, "search_queries": ["creatine"]} for i in range(20)
    ]
    ads = [{"ad_archive_id": str(i), "end_date": None} for i in range(20)]
    report = longevity_benchmarks(rows, ads, scrape_dates=set(), minimum_size=20)

    assert report["overall"]["days_to_75pct_survival"] is None
    assert report["overall"]["median_survival_days"] is None
    assert report["overall"]["status"] == "threshold_not_estimable_due_to_censoring"
