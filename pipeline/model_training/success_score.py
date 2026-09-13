"""Retrospective feature-attribution analysis, distinct from the per-target
predictive models in trainer.py/survival.py: instead of asking "will this
ad succeed" (a forecast, which a time-based split showed generalizes poorly
from established ads onto brand-new/trend-testing ones), this asks "what do
already-successful ads have in common" -- a composite success score blended
from all three target signals, fit with XGBoost, explained with Tree SHAP
(via XGBoost's own `pred_contribs`, not the separate `shap` package -- same
algorithm, no new dependency).

Uses an advertiser-group holdout: ads from one Meta page never occur in both
train and test. This is retrospective attribution rather than forecasting,
but a random row split would still overstate generalization by leaking each
brand's repeated creative and landing-page conventions across the boundary.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupShuffleSplit, train_test_split

from pipeline.model_training.evaluation import segment_metrics, temporal_advertiser_split
from pipeline.model_training.model_evidence import (
    bootstrap_prediction_evidence,
    feature_drift_report,
)
from pipeline.model_training.preprocessing import (
    _EMBEDDING_COLUMNS,
    _ID_COLUMNS,
    TARGET_COLUMNS,
    _expand_embeddings,
    build_preprocessor,
)

_SCORE_WEIGHTS = {
    "longevity": 0.70,
    "longevity_scaling_interaction": 0.20,
    "variant_boost": 0.10,
}


def _validated_score_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    resolved = dict(_SCORE_WEIGHTS if weights is None else weights)
    if set(resolved) != set(_SCORE_WEIGHTS):
        raise ValueError(f"score weights must have exactly these keys: {sorted(_SCORE_WEIGHTS)}")
    if any(not math.isfinite(value) or value < 0 for value in resolved.values()):
        raise ValueError("score weights must be finite and non-negative")
    if not math.isclose(sum(resolved.values()), 1.0, abs_tol=1e-9):
        raise ValueError("score weights must sum to 1")
    return resolved

_DEFAULT_PARAMETER_GRID = (
    {"n_estimators": 200, "max_depth": 3, "learning_rate": 0.05, "min_child_weight": 1},
    {"n_estimators": 400, "max_depth": 3, "learning_rate": 0.03, "min_child_weight": 3},
    {"n_estimators": 300, "max_depth": 4, "learning_rate": 0.05, "min_child_weight": 3},
    {"n_estimators": 250, "max_depth": 5, "learning_rate": 0.03, "min_child_weight": 5},
)


class SuccessScoreCalibrator:
    """Training-only empirical distributions used to rank proxy ingredients."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        weights: Mapping[str, float] | None = None,
    ):
        frame = pd.DataFrame(rows)
        self.weights = _validated_score_weights(weights)

        def values(name: str, default: float) -> pd.Series:
            source = frame[name] if name in frame else pd.Series(default, index=frame.index)
            return pd.to_numeric(source, errors="coerce").fillna(default)

        self.references = {
            "days_active": np.sort(values("days_active", 0).clip(lower=0)),
            "brand_scaling_count": np.sort(
                np.log1p(values("brand_scaling_count", 1).clip(lower=1))
            ),
            "collation_count": np.sort(np.log1p(values("collation_count", 0).clip(lower=0))),
        }

    def _percentile(self, name: str, values: pd.Series) -> np.ndarray:
        reference = self.references[name]
        return np.searchsorted(reference, values, side="right") / max(len(reference), 1)

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        df = pd.DataFrame(rows)

        def values(name: str, default: float) -> pd.Series:
            source = df[name] if name in df else pd.Series(default, index=df.index)
            return pd.to_numeric(source, errors="coerce").fillna(default)

        longevity = values("days_active", 0).clip(lower=0)
        scaling = np.log1p(values("brand_scaling_count", 1).clip(lower=1))
        variants = np.log1p(values("collation_count", 0).clip(lower=0))
        longevity_rank = self._percentile("days_active", longevity)
        scaling_rank = self._percentile("brand_scaling_count", scaling)
        variant_rank = self._percentile("collation_count", variants)
        df["composite_success_score"] = (
            self.weights["longevity"] * longevity_rank
            + self.weights["longevity_scaling_interaction"] * longevity_rank * scaling_rank
            + self.weights["variant_boost"] * variant_rank
        )
        return df.to_dict("records")


def compute_composite_success_score(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the bounded v1 Performance proxy to copies of ``rows``.

    Percentile ranks make heavy-tailed ad ages and page sizes robust without
    inventing spend precision the Ad Library does not expose. Longevity is the
    base (70%), page-level Scaling weights Longevity through an interaction
    (20%), and Meta collation count supplies a secondary Variant boost (10%).
    A legacy matrix without Scaling degrades to one ad per page rather than
    substituting the unrelated landing-page SKU count.
    """
    return SuccessScoreCalibrator(rows).transform(rows) if rows else []


def build_xy_composite(
    rows: list[dict[str, Any]], include_embeddings: bool = True
) -> tuple[pd.DataFrame, pd.Series]:
    """rows (already carrying composite_success_score) -> (X, y). Drops the
    three raw target columns (they compose the label -- leaving them in X
    would let the model "predict" the score by reading its own ingredients
    back) plus shows_all_variants, and re-adds price_tier as a plain
    categorical feature, matching build_xy's own convention."""
    df = pd.DataFrame(rows)
    y = df["composite_success_score"].astype(float)

    drop_cols = list(_ID_COLUMNS) + list(TARGET_COLUMNS) + ["composite_success_score", "price_tier"]
    X = df.drop(columns=[c for c in drop_cols if c in df.columns])
    X["price_tier"] = df["price_tier"]

    if not include_embeddings:
        X = X.drop(columns=[c for c in _EMBEDDING_COLUMNS if c in X.columns])
    else:
        X = _expand_embeddings(X)

    return X, y


def train_and_explain(
    rows: list[dict[str, Any]],
    include_embeddings: bool = True,
    random_state: int = 42,
    top_n: int = 30,
    n_jobs: int = 1,
    start_dates: dict[str, str] | None = None,
    parameter_grid: tuple[dict[str, Any], ...] = _DEFAULT_PARAMETER_GRID,
    score_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    if start_dates:
        train_rows, test_rows, split = temporal_advertiser_split(rows, start_dates)
        split_strategy = split["strategy"]
    else:
        raw = pd.DataFrame(rows)
        page_ids = raw.get("page_id", pd.Series("", index=raw.index)).fillna("").astype(str)
        ad_ids = raw.get("ad_id", pd.Series(raw.index.astype(str), index=raw.index)).astype(str)
        groups = page_ids.where(page_ids.str.len() > 0, "__ad__" + ad_ids)
        if groups.nunique() >= 2:
            splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=random_state)
            train_idx, test_idx = next(splitter.split(raw, groups=groups))
            split_strategy = "advertiser_group_holdout"
        else:
            train_idx, test_idx = train_test_split(
                np.arange(len(raw)), test_size=0.2, random_state=random_state
            )
            split_strategy = "random_holdout_insufficient_advertiser_groups"
        train_rows = [rows[i] for i in train_idx]
        test_rows = [rows[i] for i in test_idx]

    # Fit score percentiles on training only. Test labels cannot influence
    # either label calibration, preprocessing, parameter choice, or fitting.
    resolved_score_weights = _validated_score_weights(score_weights)
    calibrator = SuccessScoreCalibrator(train_rows, resolved_score_weights)
    scored_train = calibrator.transform(train_rows)
    scored_test = calibrator.transform(test_rows)
    X_train, y_train = build_xy_composite(scored_train, include_embeddings=include_embeddings)
    X_test, y_test = build_xy_composite(scored_test, include_embeddings=include_embeddings)

    train_ids = {r.get("ad_id") for r in train_rows}
    inner_dates = {k: v for k, v in (start_dates or {}).items() if k in train_ids}
    if inner_dates:
        tune_train_rows, validation_rows, _ = temporal_advertiser_split(train_rows, inner_dates)
    else:
        cut = max(1, int(len(train_rows) * 0.8))
        tune_train_rows, validation_rows = train_rows[:cut], train_rows[cut:]
    tune_calibrator = SuccessScoreCalibrator(tune_train_rows, resolved_score_weights)
    tune_scored = tune_calibrator.transform(tune_train_rows)
    validation_scored = tune_calibrator.transform(validation_rows)
    X_tune, y_tune = build_xy_composite(tune_scored, include_embeddings=include_embeddings)
    X_validation, y_validation = build_xy_composite(
        validation_scored, include_embeddings=include_embeddings
    )

    tuning_results: list[dict[str, Any]] = []
    for params in parameter_grid:
        prep = build_preprocessor(X_tune)
        train_t = prep.fit_transform(X_tune)
        validation_t = prep.transform(X_validation)
        candidate = xgb.XGBRegressor(
            **params,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            n_jobs=n_jobs,
        )
        candidate.fit(train_t, y_tune)
        val_mae = float(np.mean(np.abs(y_validation.to_numpy() - candidate.predict(validation_t))))
        tuning_results.append({"parameters": params, "validation_mae": round(val_mae, 5)})
    tuning_results.sort(key=lambda result: result["validation_mae"])
    best_params = tuning_results[0]["parameters"]

    preprocessor = build_preprocessor(X_train)
    X_train_t = preprocessor.fit_transform(X_train)
    X_test_t = preprocessor.transform(X_test)
    feature_names = list(preprocessor.get_feature_names_out())

    model = xgb.XGBRegressor(
        **best_params,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        n_jobs=n_jobs,
    )
    model.fit(X_train_t, y_train)

    y_test_arr = y_test.to_numpy()
    test_pred = model.predict(X_test_t)
    ss_res = np.sum((y_test_arr - test_pred) ** 2)
    ss_tot = np.sum((y_test_arr - y_test_arr.mean()) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    mae = float(np.mean(np.abs(y_test_arr - test_pred)))
    baseline_mae = float(np.mean(np.abs(y_test_arr - np.median(y_train))))
    baseline_prediction = float(np.median(y_train))
    k = max(1, int(np.ceil(len(y_test_arr) * 0.2)))
    actual_top = set(np.argsort(y_test_arr)[-k:])
    predicted_top = set(np.argsort(test_pred)[-k:])
    top_precision = len(actual_top & predicted_top) / k

    booster = model.get_booster()
    dtest = xgb.DMatrix(X_test_t, feature_names=feature_names)
    # Tree SHAP: shape (n_test, n_features + 1); the final column is the
    # bias/expected-value term, dropped before ranking features.
    contribs = booster.predict(dtest, pred_contribs=True)
    shap_values = contribs[:, :-1]
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    mean_signed_shap = shap_values.mean(axis=0)

    ranking = sorted(zip(feature_names, mean_abs_shap, mean_signed_shap), key=lambda t: -t[1])

    test_group_sequence = np.array(
        [str(row.get("page_id") or f"__ad__{row['ad_id']}") for row in test_rows]
    )
    train_groups = {str(r.get("page_id") or f"__ad__{r['ad_id']}") for r in train_rows}
    test_groups = set(test_group_sequence)
    return {
        "n_rows": len(rows),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": X_train_t.shape[1],
        "test_r2": round(r2, 4),
        "test_mae": round(mae, 4),
        "baseline_mae": round(baseline_mae, 4),
        "mae_improvement_over_baseline": round(baseline_mae - mae, 4),
        "top_20pct_precision": round(top_precision, 4),
        "uncertainty": bootstrap_prediction_evidence(
            y_test_arr,
            test_pred,
            baseline_prediction,
            random_state=random_state,
            groups=test_group_sequence,
        ),
        "feature_drift": feature_drift_report(train_rows, test_rows),
        "split_strategy": split_strategy,
        "advertiser_overlap": len(train_groups & test_groups),
        "n_train_advertisers": len(train_groups),
        "n_test_advertisers": len(test_groups),
        "best_parameters": best_params,
        "tuning_results": tuning_results,
        "label_calibration": "training_only_empirical_percentiles",
        "score_weights": resolved_score_weights,
        "segment_evaluation": segment_metrics(
            test_rows, y_test_arr, test_pred, start_dates or {}, minimum_size=10
        ),
        "random_state": random_state,
        "y_train_mean": round(float(y_train.mean()), 4),
        "y_train_std": round(float(y_train.std()), 4),
        "top_features_by_shap": [
            {
                "feature": name,
                "mean_abs_shap": round(float(a), 5),
                "mean_signed_shap": round(float(s), 5),
            }
            for name, a, s in ranking[:top_n]
        ],
    }


def fit_and_score_future_window(
    training_rows: list[dict[str, Any]],
    future_rows: list[dict[str, Any]],
    best_parameters: dict[str, Any],
    random_state: int = 42,
    n_jobs: int = 1,
    future_start_dates: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Refit a frozen specification on old rows and score only future rows.

    Parameter selection is not repeated here. The future window cannot affect
    percentile calibration, preprocessing, model fitting, or feature pooling.
    """
    if not training_rows or not future_rows:
        raise ValueError("training and future rows must both be non-empty")
    calibrator = SuccessScoreCalibrator(training_rows)
    scored_train = calibrator.transform(training_rows)
    scored_future = calibrator.transform(future_rows)
    X_train, y_train = build_xy_composite(scored_train, include_embeddings=False)
    X_future, y_future = build_xy_composite(scored_future, include_embeddings=False)
    X_future = X_future.reindex(columns=X_train.columns)
    preprocessor = build_preprocessor(X_train)
    X_train_t = preprocessor.fit_transform(X_train)
    X_future_t = preprocessor.transform(X_future)
    model = xgb.XGBRegressor(
        **best_parameters,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        n_jobs=n_jobs,
    )
    model.fit(X_train_t, y_train)
    actual = y_future.to_numpy()
    predicted = model.predict(X_future_t)
    baseline_prediction = float(np.median(y_train))
    future_group_sequence = np.array(
        [str(row.get("page_id") or f"__ad__{row.get('ad_id')}") for row in future_rows]
    )
    uncertainty = bootstrap_prediction_evidence(
        actual,
        predicted,
        baseline_prediction,
        random_state=random_state,
        groups=future_group_sequence,
    )
    train_groups = {str(row.get("page_id") or f"__ad__{row.get('ad_id')}") for row in training_rows}
    future_groups = set(future_group_sequence)
    return {
        "n_train": len(training_rows),
        "n_test": len(future_rows),
        "test_mae": round(float(np.mean(np.abs(actual - predicted))), 4),
        "baseline_mae": round(float(np.mean(np.abs(actual - baseline_prediction))), 4),
        "top_20pct_precision": uncertainty["top_20pct_precision"],
        "advertiser_overlap": len(train_groups & future_groups),
        "n_train_advertisers": len(train_groups),
        "n_test_advertisers": len(future_groups),
        "uncertainty": uncertainty,
        "feature_drift": feature_drift_report(training_rows, future_rows),
        "segment_evaluation": segment_metrics(
            future_rows,
            actual,
            predicted,
            future_start_dates or {},
            minimum_size=10,
        ),
        "best_parameters": best_parameters,
        "random_state": random_state,
        "fit_policy": "frozen_parameters_old_training_rows_only",
    }


def print_results(ablation_label: str, results: dict[str, Any]) -> None:
    print(f"\n=== composite_success_score ({ablation_label}) ===")
    print(
        f"  train/test rows: {results['n_train']}/{results['n_test']}  "
        f"features: {results['n_features']}"
    )
    print(f"  y_train mean/std: {results['y_train_mean']} / {results['y_train_std']}")
    print(f"  test  MAE={results['test_mae']}  R2={results['test_r2']}")
    print("  top features by mean |SHAP|:")
    for f in results["top_features_by_shap"][:15]:
        direction = "+" if f["mean_signed_shap"] >= 0 else "-"
        print(
            f"    {f['mean_abs_shap']:.5f}  {direction}  {f['feature']}  "
            f"(mean signed={f['mean_signed_shap']:+.5f})"
        )
