"""Smoke test for pipeline/model_training/trainer.py -- confirms the full
fit/predict/CV/importance path runs end-to-end on tiny synthetic data and
returns the expected result shape. Not asserting specific accuracy numbers
(that's real ML on random data, not a deterministic contract) -- just that
nothing raises and every promised key is present."""

from __future__ import annotations

import random

from pipeline.model_training.preprocessing import build_xy
from pipeline.model_training.trainer import train_and_evaluate


def _synthetic_rows(n: int) -> list[dict]:
    rng = random.Random(42)
    rows = []
    for i in range(n):
        rows.append({
            "ad_id": str(i),
            "days_active": rng.randint(1, 200),
            "collation_count": 1,
            "variants_featured_count": 0,
            "price_tier": rng.choice(["budget", "mid", "premium"]),
            "body_length": rng.randint(10, 500),
            "dominant_color": rng.choice(["red", "blue", "green"]),
            "has_cta_text": rng.choice([True, False]),
            "title_embedding": [rng.random() for _ in range(8)],
            "body_embedding": [],
            "usp_embedding": [],
        })
    return rows


class TestTrainAndEvaluate:
    def test_returns_expected_keys_without_raising(self):
        rows = _synthetic_rows(60)
        train_rows, test_rows = rows[:48], rows[48:]
        X_train, y_train = build_xy(train_rows, "days_active", include_embeddings=False)
        X_test, y_test = build_xy(test_rows, "days_active", include_embeddings=False)

        results = train_and_evaluate(X_train, y_train, X_test, y_test, cv_folds=3)

        for key in (
            "n_train", "n_test", "n_features", "test_mae", "test_rmse", "test_r2",
            "cv_mae_mean", "cv_mae_std", "cv_r2_mean", "cv_r2_std", "top_features",
            "y_train_mean", "y_train_median",
        ):
            assert key in results
        assert results["n_train"] == len(X_train)
        assert results["n_test"] == len(X_test)
        assert results["test_mae"] >= 0
        assert results["test_rmse"] >= 0
        assert len(results["top_features"]) > 0
