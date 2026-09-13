from __future__ import annotations

from typing import Any

from pipeline.model_training.proxy_sensitivity import (
    WEIGHT_SCENARIOS,
    build_proxy_sensitivity_report,
)


def test_proxy_sensitivity_is_analysis_only_and_reports_direction_instability():
    rows = [
        {
            "ad_id": str(index),
            "page_id": f"brand-{index // 2}",
            "days_active": index + 1,
            "brand_scaling_count": 2,
            "collation_count": index % 3,
        }
        for index in range(20)
    ]

    def fake_trainer(
        *_args: Any,
        score_weights: dict[str, float],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        direction = 1 if score_weights["longevity"] >= 0.6 else -1
        return {
            "n_train": 16,
            "n_test": 4,
            "n_test_advertisers": 2,
            "advertiser_overlap": 0,
            "test_mae": 0.2,
            "baseline_mae": 0.3,
            "top_20pct_precision": 0.5,
            "uncertainty": {"method": "paired_advertiser_cluster_bootstrap"},
            "top_features_by_shap": [
                {
                    "feature": "numeric__contrast",
                    "mean_abs_shap": 0.2,
                    "mean_signed_shap": 0.1 * direction,
                }
            ],
        }

    report = build_proxy_sensitivity_report(rows, {}, trainer=fake_trainer)

    assert report["status"] == "analysis_only_no_automatic_weight_selection"
    assert report["production_effect"] == "none; immutable v3 remains selected"
    assert set(report["scenarios"]) == set(WEIGHT_SCENARIOS)
    contrast = report["shap_direction_stability"][0]
    assert contrast["direction_consistent_when_present"] is False
    assert contrast["stable_across_all_scenarios"] is False
    assert "prohibited" in report["loop_isolation"]
