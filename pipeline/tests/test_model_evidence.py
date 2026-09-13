from __future__ import annotations

import numpy as np

from pipeline.model_training.model_evidence import (
    bootstrap_prediction_evidence,
    build_promotion_decision,
    feature_drift_report,
)
from pipeline.model_training.run_training import _taxonomy_gate


def test_bootstrap_distinguishes_real_improvement_from_point_only_claim():
    actual = np.linspace(0, 1, 100)
    predicted = actual + np.tile(np.array([-0.01, 0.01]), 50)
    evidence = bootstrap_prediction_evidence(
        actual, predicted, baseline_prediction=0.5, random_state=42, n_resamples=500
    )
    assert evidence["mae_difference_confidence_interval_95"][1] < 0
    assert evidence["top_20pct_precision_confidence_interval_95"][0] > 0.2


def test_bootstrap_is_reproducible_for_same_seed():
    actual = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    predicted = np.array([0.1, 0.2, 0.6, 0.7, 0.9])
    first = bootstrap_prediction_evidence(actual, predicted, 0.5, 7, n_resamples=100)
    second = bootstrap_prediction_evidence(actual, predicted, 0.5, 7, n_resamples=100)
    assert first == second


def test_cluster_bootstrap_resamples_whole_asymmetric_advertisers():
    actual = np.zeros(9)
    predicted = np.array([1.0] * 8 + [-1.0])
    groups = np.array(["large"] * 8 + ["small"])
    row = bootstrap_prediction_evidence(actual, predicted, 0.0, 9, n_resamples=500)
    clustered = bootstrap_prediction_evidence(
        actual, predicted, 0.0, 9, n_resamples=500, groups=groups
    )
    assert clustered["method"] == "paired_advertiser_cluster_bootstrap"
    assert clustered["n_clusters"] == 2
    assert (
        clustered["calibration_gap_confidence_interval_95"][0]
        < row["calibration_gap_confidence_interval_95"][0]
    )


def test_cluster_bootstrap_rejects_invalid_group_assignments():
    actual = np.array([0.0, 1.0])
    predicted = np.array([0.1, 0.9])
    for groups in (np.array(["only-one"]), np.array(["valid", ""])):
        try:
            bootstrap_prediction_evidence(actual, predicted, 0.5, 7, groups=groups)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid groups must fail closed")


def test_drift_report_surfaces_changed_numeric_and_categorical_features():
    train = [{"ad_id": str(i), "page_id": "old", "numeric": i, "category": "a"} for i in range(20)]
    test = [
        {"ad_id": str(i + 20), "page_id": "new", "numeric": i + 100, "category": "b"}
        for i in range(20)
    ]
    report = feature_drift_report(train, test)
    by_feature = {row["feature"]: row for row in report["top_shifted_features"]}
    assert by_feature["numeric"]["score"] > 1
    assert by_feature["category"]["score"] == 1
    assert "ad_id" not in by_feature


def test_promotion_fails_closed_when_taxonomy_or_confidence_is_missing():
    result = {
        "n_test": 100,
        "n_test_advertisers": 10,
        "advertiser_overlap": 0,
        "test_mae": 0.3,
        "baseline_mae": 0.4,
        "uncertainty": {
            "method": "paired_advertiser_cluster_bootstrap",
            "n_clusters": 10,
            "mae_difference_confidence_interval_95": [-0.2, -0.01],
            "top_20pct_precision_confidence_interval_95": [0.25, 0.5],
        },
    }
    decision = build_promotion_decision(
        result,
        {"git_worktree_dirty": False},
        {"status": "failed"},
        {"status": "passed"},
    )
    assert decision["status"] == "blocked"
    assert decision["failed_checks"] == ["taxonomy_adjudication_passed"]


def test_promotion_passes_only_when_every_objective_gate_passes():
    result = {
        "n_test": 100,
        "n_test_advertisers": 10,
        "advertiser_overlap": 0,
        "test_mae": 0.3,
        "baseline_mae": 0.4,
        "uncertainty": {
            "method": "paired_advertiser_cluster_bootstrap",
            "n_clusters": 10,
            "mae_difference_confidence_interval_95": [-0.2, -0.01],
            "top_20pct_precision_confidence_interval_95": [0.25, 0.5],
        },
    }
    decision = build_promotion_decision(
        result,
        {"git_worktree_dirty": False},
        {"status": "passed"},
        {"status": "passed"},
    )
    assert decision["status"] == "promoted"
    assert all(decision["checks"].values())


def test_promotion_blocks_when_product_enrichment_is_unverified():
    result = {
        "n_test": 100,
        "n_test_advertisers": 10,
        "advertiser_overlap": 0,
        "test_mae": 0.3,
        "baseline_mae": 0.4,
        "uncertainty": {
            "method": "paired_advertiser_cluster_bootstrap",
            "n_clusters": 10,
            "mae_difference_confidence_interval_95": [-0.2, -0.01],
            "top_20pct_precision_confidence_interval_95": [0.25, 0.5],
        },
    }
    decision = build_promotion_decision(
        result,
        {"git_worktree_dirty": False},
        {"status": "passed"},
        {"status": "failed"},
    )
    assert decision["status"] == "blocked"
    assert decision["failed_checks"] == ["product_enrichment_passed"]


def test_training_taxonomy_gate_rejects_unreviewed_ads():
    assert _taxonomy_gate([{"ad_archive_id": "1"}])["status"] == "failed"


def test_training_taxonomy_gate_accepts_one_provenance_version():
    ads = [
        {
            "ad_archive_id": "1",
            "taxonomy_label": {
                "source": "human_adjudication",
                "is_supplement": True,
                "provenance": {
                    "sample_sha256": "a" * 64,
                    "review_schema_version": "supplements-taxonomy-review-v1",
                    "adjudication_report_schema_version": (
                        "supplements-taxonomy-adjudication-report-v1"
                    ),
                    "reviewer_id": "reviewer-a",
                    "reviewed_at": "2026-09-12T10:00:00Z",
                    "approved_labels_content_sha256": "b" * 64,
                },
            },
        }
    ]
    gate = _taxonomy_gate(ads)
    assert gate["status"] == "passed"
    assert gate["sample_sha256"] == "a" * 64
