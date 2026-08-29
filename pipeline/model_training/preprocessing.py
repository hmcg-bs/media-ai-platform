"""Turns feature-matrix rows into a model-ready (X, y) split: time-based
train/test split, then impute/scale/encode fit ONLY on train (never on the
full matrix up front) to avoid leaking test-set statistics into training.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# The three columns this project's plan treats as target-variable
# candidates (days_active P0, collation_count/variants_featured_count P1).
# Excluded from every model's X regardless of which one is the current y,
# to avoid one target silently leaking into another's feature set.
TARGET_COLUMNS = ("days_active", "collation_count", "variants_featured_count")

_ID_COLUMNS = ("ad_id",)
_EMBEDDING_COLUMNS = ("title_embedding", "body_embedding", "usp_embedding")

# Features that are near-deterministic functions of a specific target,
# computed from the exact same underlying data the target counts -- not
# independent signal. Confirmed live: shows_all_variants is computed
# upstream as `len(variants_featured) > 1` (zenrows_scraper.py,
# shopify_json.py), so training variants_featured_count with it in X let
# the model "predict" the count largely by reading a boolean summary of
# itself (it dominated feature importance at 0.35, far above any real
# creative/product signal).
LEAKY_FEATURES_BY_TARGET: dict[str, tuple[str, ...]] = {
    "variants_featured_count": ("shows_all_variants",),
}


def load_start_dates(ads: list[dict[str, Any]]) -> dict[str, str]:
    """ad_archive_id -> start_date ('YYYY-MM-DD'), for the time-based split.
    Sourced from the ingestion corpus (data/supplements_enriched.json), not
    the feature matrix itself -- build_matrix.py's rows don't carry it."""
    return {a["ad_archive_id"]: a["start_date"] for a in ads if a.get("start_date")}


def time_based_split(
    rows: list[dict[str, Any]],
    start_dates: dict[str, str],
    train_fraction: float = 0.8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sorts by the ad's real start_date (not any target-variable-derived
    ordering -- days_active is itself a target, so sorting by it would leak
    target-adjacent structure into the split) and splits oldest-first into
    train, newest into test. Rows with no known start_date are dropped
    (can't be time-ordered) rather than silently included in an arbitrary
    position."""
    dated = [(row, start_dates[row["ad_id"]]) for row in rows if row.get("ad_id") in start_dates]
    dated.sort(key=lambda pair: pair[1])
    n_train = int(len(dated) * train_fraction)
    train = [row for row, _ in dated[:n_train]]
    test = [row for row, _ in dated[n_train:]]
    return train, test


def _expand_embeddings(df: pd.DataFrame) -> pd.DataFrame:
    """Each *_embedding column (list[float] or []) becomes N numeric
    columns (*_embedding_0..N-1); an empty/missing vector becomes all-NaN
    for that row (imputed downstream like any other numeric gap, not
    silently zero-filled -- a real "no source text" signal, not "embedding
    of the empty string")."""
    df = df.copy()
    for col in _EMBEDDING_COLUMNS:
        if col not in df.columns:
            continue
        dim = next((len(v) for v in df[col] if v), 0)
        if dim == 0:
            df = df.drop(columns=[col])
            continue
        expanded = pd.DataFrame(
            [(list(v) if v else [np.nan] * dim) for v in df[col]],
            columns=[f"{col}_{i}" for i in range(dim)],
            index=df.index,
        )
        df = pd.concat([df.drop(columns=[col]), expanded], axis=1)
    return df


def build_xy(
    rows: list[dict[str, Any]],
    target: str,
    include_embeddings: bool = True,
    exclude_leaky_features: bool = True,
) -> tuple[pd.DataFrame, pd.Series]:
    """rows -> (X, y) for one target. Drops rows where the target itself is
    null (can't train/score on an unknown label) -- reports how many via the
    caller inspecting len(rows) vs len(returned y) if it wants to."""
    if target not in TARGET_COLUMNS:
        raise ValueError(f"target must be one of {TARGET_COLUMNS}, got {target!r}")

    df = pd.DataFrame(rows)
    df = df[df[target].notna()].reset_index(drop=True)

    y = df[target].astype(float)
    drop_cols = list(_ID_COLUMNS) + list(TARGET_COLUMNS) + ["price_tier"]
    if exclude_leaky_features:
        drop_cols += list(LEAKY_FEATURES_BY_TARGET.get(target, ()))
    # price_tier is a segmentation label (see product_features.py's own
    # docstring: price excluded as a raw numeric feature per the user's
    # price-strategy decision) -- re-added below as a plain categorical
    # feature, not dropped for good, since tier membership is still real
    # signal the model can use without exposing raw price.
    X = df.drop(columns=[c for c in drop_cols if c in df.columns])
    X["price_tier"] = df["price_tier"]

    if not include_embeddings:
        X = X.drop(columns=[c for c in _EMBEDDING_COLUMNS if c in X.columns])
    else:
        X = _expand_embeddings(X)

    return X, y


def build_preprocessor(X: pd.DataFrame) -> ColumnTransformer:
    """Median-impute + scale numeric columns, most_frequent-impute +
    one-hot encode categorical/boolean columns. Fit only on train (call
    .fit_transform on train, .transform on test) -- constructing this from
    X's dtypes, not the full corpus, is what keeps test-set values out of
    the fit."""
    numeric_cols = [c for c in X.columns if pd.api.types.is_numeric_dtype(X[c])]
    categorical_cols = [c for c in X.columns if c not in numeric_cols]

    numeric_pipeline = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical_pipeline = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    return ColumnTransformer([
        ("numeric", numeric_pipeline, numeric_cols),
        ("categorical", categorical_pipeline, categorical_cols),
    ])
