"""Per-target XGBoost training: time-based holdout + 5-fold CV on train,
MAE/RMSE/R² on the untouched test set, feature importance, and an
embeddings-in vs embeddings-out ablation (per the plan's own Validation &
Evaluation Framework: "Ablation: Remove embeddings -> how much RMSE
increases?").
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, cross_val_score
from sklearn.pipeline import Pipeline
from xgboost import XGBRegressor

from pipeline.model_training.preprocessing import build_preprocessor


def _make_model() -> XGBRegressor:
    return XGBRegressor(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
    )


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")


def train_and_evaluate(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    cv_folds: int = 5,
) -> dict[str, Any]:
    """Fits preprocessing on train only, evaluates on held-out test, and
    reports 5-fold CV (on train only, never touching test) so a single lucky
    split isn't mistaken for a stable result."""
    preprocessor = build_preprocessor(X_train)
    pipeline = Pipeline([("prep", preprocessor), ("model", _make_model())])

    cv = KFold(n_splits=cv_folds, shuffle=True, random_state=42)
    cv_neg_mae = cross_val_score(
        pipeline, X_train, y_train, cv=cv, scoring="neg_mean_absolute_error"
    )
    cv_r2 = cross_val_score(pipeline, X_train, y_train, cv=cv, scoring="r2")

    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)

    feature_names = pipeline.named_steps["prep"].get_feature_names_out()
    importances = pipeline.named_steps["model"].feature_importances_
    top_features = sorted(
        zip(feature_names, importances), key=lambda kv: -kv[1]
    )[:20]

    return {
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": len(feature_names),
        "test_mae": round(_mae(y_test.to_numpy(), y_pred), 4),
        "test_rmse": round(_rmse(y_test.to_numpy(), y_pred), 4),
        "test_r2": round(_r2(y_test.to_numpy(), y_pred), 4),
        "cv_mae_mean": round(float(-cv_neg_mae.mean()), 4),
        "cv_mae_std": round(float(cv_neg_mae.std()), 4),
        "cv_r2_mean": round(float(cv_r2.mean()), 4),
        "cv_r2_std": round(float(cv_r2.std()), 4),
        "top_features": [(str(name), round(float(imp), 4)) for name, imp in top_features],
        "y_train_mean": round(float(y_train.mean()), 4),
        "y_train_median": round(float(y_train.median()), 4),
    }


def print_results(target: str, ablation_label: str, results: dict[str, Any]) -> None:
    print(f"\n=== {target} ({ablation_label}) ===")
    print(
        f"  train/test rows: {results['n_train']}/{results['n_test']}  "
        f"features: {results['n_features']}"
    )
    print(f"  y_train mean/median: {results['y_train_mean']} / {results['y_train_median']}")
    print(
        f"  test  MAE={results['test_mae']}  RMSE={results['test_rmse']}  R2={results['test_r2']}"
    )
    print(
        f"  cv(5) MAE={results['cv_mae_mean']}+/-{results['cv_mae_std']}  "
        f"R2={results['cv_r2_mean']}+/-{results['cv_r2_std']}"
    )
    print("  top features:")
    for name, imp in results["top_features"][:10]:
        print(f"    {imp:.4f}  {name}")
