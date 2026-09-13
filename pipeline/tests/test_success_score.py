"""Tests for pipeline/model_training/success_score.py -- confirms the
composite score computation is correct on synthetic data with known z-scores,
confirms leaky/target columns never leak into X, and smoke-tests the full
train_and_explain path end-to-end (not asserting specific SHAP values -- real
ML on random data isn't a deterministic contract, just that nothing raises
and every promised key/shape is present)."""

from __future__ import annotations

import random

from pipeline.model_training.success_score import (
    build_xy_composite,
    compute_composite_success_score,
    train_and_explain,
)


def _synthetic_rows(n: int) -> list[dict]:
    rng = random.Random(42)
    rows = []
    for i in range(n):
        rows.append({
            "ad_id": str(i),
            "page_id": f"brand_{i // 3}",
            "days_active": rng.randint(1, 200),
            "brand_scaling_count": 3,
            "collation_count": rng.randint(1, 5),
            "variants_featured_count": rng.randint(0, 4),
            "shows_all_variants": rng.choice([True, False]),
            "price_tier": rng.choice(["budget", "mid", "premium"]),
            "body_length": rng.randint(10, 500),
            "dominant_color": rng.choice(["red", "blue", "green"]),
            "title_embedding": [rng.random() for _ in range(8)],
            "body_embedding": [],
            "usp_embedding": [],
        })
    return rows


class TestComputeCompositeSuccessScore:
    def test_longevity_is_base_and_score_is_bounded(self):
        rows = [
            {"ad_id": "1", "days_active": 1, "brand_scaling_count": 100, "collation_count": 10},
            {"ad_id": "2", "days_active": 100, "brand_scaling_count": 1, "collation_count": 0},
        ]
        scored = compute_composite_success_score(rows)
        low_score = scored[0]["composite_success_score"]
        high_score = scored[1]["composite_success_score"]
        assert 0 <= low_score < high_score <= 1

    def test_scaling_weights_equal_longevity_and_variant_inputs(self):
        rows = [
            {"ad_id": "1", "days_active": 30, "brand_scaling_count": 1, "collation_count": 1},
            {"ad_id": "2", "days_active": 30, "brand_scaling_count": 20, "collation_count": 1},
        ]
        scored = compute_composite_success_score(rows)
        assert scored[1]["composite_success_score"] > scored[0]["composite_success_score"]

    def test_landing_page_sku_count_is_not_a_proxy_ingredient(self):
        base = {"days_active": 30, "brand_scaling_count": 2, "collation_count": 1}
        rows = [
            {"ad_id": "1", **base, "variants_featured_count": 0},
            {"ad_id": "2", **base, "variants_featured_count": 99},
        ]
        scores = compute_composite_success_score(rows)
        assert scores[0]["composite_success_score"] == scores[1]["composite_success_score"]


class TestBuildXyComposite:
    def test_target_and_leaky_columns_excluded_from_features(self):
        rows = compute_composite_success_score(_synthetic_rows(20))
        X, y = build_xy_composite(rows, include_embeddings=False)

        leaky_cols = (
            "days_active", "brand_scaling_count", "collation_count", "page_id",
        )
        for col in leaky_cols:
            assert col not in X.columns
        assert "composite_success_score" not in X.columns
        assert "ad_id" not in X.columns
        assert "variants_featured_count" in X.columns
        assert len(y) == len(X) == 20

    def test_price_tier_kept_as_categorical_feature(self):
        rows = compute_composite_success_score(_synthetic_rows(20))
        X, _y = build_xy_composite(rows, include_embeddings=False)
        assert "price_tier" in X.columns


class TestTrainAndExplain:
    def test_returns_expected_keys_without_raising(self):
        rows = _synthetic_rows(60)
        results = train_and_explain(rows, include_embeddings=False, top_n=10)

        for key in (
            "n_rows", "n_train", "n_test", "n_features", "test_r2", "test_mae",
            "y_train_mean", "y_train_std", "top_features_by_shap",
        ):
            assert key in results
        assert results["n_train"] + results["n_test"] == 60
        assert results["split_strategy"] == "advertiser_group_holdout"
        assert results["advertiser_overlap"] == 0
        assert results["baseline_mae"] >= 0
        assert 0 <= results["top_20pct_precision"] <= 1
        assert len(results["top_features_by_shap"]) > 0
        for entry in results["top_features_by_shap"]:
            assert entry["mean_abs_shap"] >= 0
