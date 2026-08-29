"""Retrospective feature-attribution analysis, distinct from the per-target
predictive models in trainer.py/survival.py: instead of asking "will this
ad succeed" (a forecast, which a time-based split showed generalizes poorly
from established ads onto brand-new/trend-testing ones), this asks "what do
already-successful ads have in common" -- a composite success score blended
from all three target signals, fit with XGBoost, explained with Tree SHAP
(via XGBoost's own `pred_contribs`, not the separate `shap` package -- same
algorithm, no new dependency).

Uses a RANDOM train/test split, not the time-based one preprocessing.py's
time_based_split provides: this analysis isn't forecasting forward onto
not-yet-proven ads, so held-out rows should be a random sample of the whole
population, not deliberately the newest slice.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split

from pipeline.model_training.preprocessing import (
    _EMBEDDING_COLUMNS,
    _ID_COLUMNS,
    TARGET_COLUMNS,
    _expand_embeddings,
    build_preprocessor,
)

# shows_all_variants is a near-deterministic function of variants_featured
# (one of the composite's own three ingredients) -- excluded the same way
# preprocessing.py's LEAKY_FEATURES_BY_TARGET excludes it from
# variants_featured_count's own standalone model.
_COMPOSITE_LEAKY_FEATURES = ("shows_all_variants",)


def compute_composite_success_score(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds `composite_success_score` to a copy of each row: the mean of
    each of the three target signals' corpus-wide z-scores. Equal-weighted
    -- no signal is treated as more important a priori than the others.
    A zero-variance target (std==0) contributes a constant 0 rather than
    dividing by zero."""
    df = pd.DataFrame(rows)
    zscored = []
    for col in TARGET_COLUMNS:
        values = df[col].astype(float)
        std = values.std()
        z = (values - values.mean()) / std if std > 0 else pd.Series(0.0, index=values.index)
        zscored.append(z)
    df["composite_success_score"] = pd.concat(zscored, axis=1).mean(axis=1)
    return df.to_dict("records")


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

    drop_cols = (
        list(_ID_COLUMNS)
        + list(TARGET_COLUMNS)
        + ["composite_success_score", "price_tier"]
        + list(_COMPOSITE_LEAKY_FEATURES)
    )
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
) -> dict[str, Any]:
    scored_rows = compute_composite_success_score(rows)
    X, y = build_xy_composite(scored_rows, include_embeddings=include_embeddings)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=random_state
    )

    preprocessor = build_preprocessor(X_train)
    X_train_t = preprocessor.fit_transform(X_train)
    X_test_t = preprocessor.transform(X_test)
    feature_names = list(preprocessor.get_feature_names_out())

    model = xgb.XGBRegressor(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        n_jobs=-1,
    )
    model.fit(X_train_t, y_train)

    y_test_arr = y_test.to_numpy()
    test_pred = model.predict(X_test_t)
    ss_res = np.sum((y_test_arr - test_pred) ** 2)
    ss_tot = np.sum((y_test_arr - y_test_arr.mean()) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    mae = float(np.mean(np.abs(y_test_arr - test_pred)))

    booster = model.get_booster()
    dtest = xgb.DMatrix(X_test_t, feature_names=feature_names)
    # Tree SHAP: shape (n_test, n_features + 1); the final column is the
    # bias/expected-value term, dropped before ranking features.
    contribs = booster.predict(dtest, pred_contribs=True)
    shap_values = contribs[:, :-1]
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    mean_signed_shap = shap_values.mean(axis=0)

    ranking = sorted(
        zip(feature_names, mean_abs_shap, mean_signed_shap), key=lambda t: -t[1]
    )

    return {
        "n_rows": len(rows),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": X_train_t.shape[1],
        "test_r2": round(r2, 4),
        "test_mae": round(mae, 4),
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
