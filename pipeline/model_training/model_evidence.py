"""Uncertainty, drift, and fail-closed promotion evidence for proxy models."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from pipeline.model_training.preprocessing import TARGET_COLUMNS

BOOTSTRAP_RESAMPLES = 2000
TOP_FRACTION = 0.20
_EXCLUDED_COLUMNS = {
    "ad_id",
    "page_id",
    "title_embedding",
    "body_embedding",
    "usp_embedding",
    *TARGET_COLUMNS,
}


def _percentile_interval(values: list[float]) -> list[float]:
    return [
        round(float(np.percentile(values, 2.5)), 5),
        round(float(np.percentile(values, 97.5)), 5),
    ]


def bootstrap_prediction_evidence(
    actual: np.ndarray,
    predicted: np.ndarray,
    baseline_prediction: float,
    random_state: int,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    groups: np.ndarray | None = None,
) -> dict[str, Any]:
    """Paired MAE/calibration intervals and ranking-hit uncertainty.

    Ranking precision is a property of the fixed predicted top-k set. Its
    interval bootstraps the hit indicators within that set instead of
    repeatedly redefining "top" on duplicated bootstrap rows.

    When advertiser ``groups`` are supplied, whole advertisers are sampled
    with replacement. This preserves within-advertiser dependence instead of
    pretending repeated creatives from one advertiser are independent rows.
    """
    if len(actual) != len(predicted) or not len(actual):
        raise ValueError("actual and predicted must have the same non-zero length")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    group_array: np.ndarray | None = None
    group_indices: list[np.ndarray] | None = None
    unique_groups: np.ndarray | None = None
    if groups is not None:
        group_array = np.asarray(groups, dtype=object)
        if len(group_array) != len(actual):
            raise ValueError("groups must have the same length as actual and predicted")
        if any(group is None or str(group).strip() == "" for group in group_array):
            raise ValueError("groups must not contain missing or blank values")
        group_array = group_array.astype(str)
        unique_groups = np.unique(group_array)
        group_indices = [np.flatnonzero(group_array == group) for group in unique_groups]
    rng = np.random.default_rng(random_state)
    n = len(actual)
    mae_differences: list[float] = []
    calibration_gaps: list[float] = []
    for _ in range(n_resamples):
        if group_indices is None:
            indices = rng.integers(0, n, size=n)
        else:
            sampled_groups = rng.integers(0, len(group_indices), size=len(group_indices))
            indices = np.concatenate([group_indices[index] for index in sampled_groups])
        sampled_actual = actual[indices]
        sampled_predicted = predicted[indices]
        model_mae = np.mean(np.abs(sampled_actual - sampled_predicted))
        baseline_mae = np.mean(np.abs(sampled_actual - baseline_prediction))
        mae_differences.append(float(model_mae - baseline_mae))
        calibration_gaps.append(float(np.mean(sampled_predicted - sampled_actual)))

    k = max(1, int(np.ceil(n * TOP_FRACTION)))
    actual_top = set(np.argsort(actual)[-k:])
    predicted_top = np.argsort(predicted)[-k:]
    hit_values = np.array([int(index in actual_top) for index in predicted_top])
    if group_array is None:
        precision_samples = [
            float(rng.choice(hit_values, size=k, replace=True).mean()) for _ in range(n_resamples)
        ]
        top_group_count = None
    else:
        top_groups = group_array[predicted_top]
        unique_top_groups = np.unique(top_groups)
        top_group_hits = [hit_values[top_groups == group] for group in unique_top_groups]
        precision_samples = []
        for _ in range(n_resamples):
            sampled_groups = rng.integers(
                0, len(top_group_hits), size=len(top_group_hits)
            )
            sampled_hits = np.concatenate([top_group_hits[index] for index in sampled_groups])
            precision_samples.append(float(sampled_hits.mean()))
        top_group_count = len(unique_top_groups)
    return {
        "method": (
            "paired_advertiser_cluster_bootstrap"
            if group_array is not None
            else "paired_nonparametric_bootstrap"
        ),
        "n_resamples": n_resamples,
        "random_state": random_state,
        "n_clusters": len(unique_groups) if unique_groups is not None else None,
        "n_top_set_clusters": top_group_count,
        "mae_difference_challenger_minus_baseline": round(
            float(
                np.mean(np.abs(actual - predicted)) - np.mean(np.abs(actual - baseline_prediction))
            ),
            5,
        ),
        "mae_difference_confidence_interval_95": _percentile_interval(mae_differences),
        "calibration_gap": round(float(np.mean(predicted - actual)), 5),
        "calibration_gap_confidence_interval_95": _percentile_interval(calibration_gaps),
        "top_20pct_precision": round(float(hit_values.mean()), 5),
        "top_20pct_precision_confidence_interval_95": _percentile_interval(precision_samples),
        "top_20pct_prevalence_baseline": TOP_FRACTION,
    }


def _numeric_psi(train: list[float], test: list[float]) -> float:
    quantiles = np.unique(np.quantile(train, np.linspace(0, 1, 11)))
    if len(quantiles) < 2:
        return 0.0
    edges = np.concatenate(([-np.inf], quantiles[1:-1], [np.inf]))
    train_counts, _ = np.histogram(train, bins=edges)
    test_counts, _ = np.histogram(test, bins=edges)
    epsilon = 1e-6
    train_share = np.maximum(train_counts / max(sum(train_counts), 1), epsilon)
    test_share = np.maximum(test_counts / max(sum(test_counts), 1), epsilon)
    return float(np.sum((test_share - train_share) * np.log(test_share / train_share)))


def _categorical_total_variation(train: list[Any], test: list[Any]) -> float:
    train_counts = Counter("__missing__" if value is None else str(value) for value in train)
    test_counts = Counter("__missing__" if value is None else str(value) for value in test)
    levels = set(train_counts) | set(test_counts)
    return 0.5 * sum(
        abs(train_counts[level] / len(train) - test_counts[level] / len(test)) for level in levels
    )


def feature_drift_report(
    train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]], top_n: int = 30
) -> dict[str, Any]:
    """Measure raw train/test feature drift without turning it into causality."""
    if not train_rows or not test_rows:
        return {"status": "insufficient_rows", "n_train": len(train_rows), "n_test": len(test_rows)}
    columns = sorted(
        ({key for row in train_rows for key in row} | {key for row in test_rows for key in row})
        - _EXCLUDED_COLUMNS
    )
    entries = []
    for column in columns:
        train_values = [row.get(column) for row in train_rows]
        test_values = [row.get(column) for row in test_rows]
        train_non_null = [value for value in train_values if value is not None]
        test_non_null = [value for value in test_values if value is not None]
        numeric = (
            train_non_null
            and test_non_null
            and all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in train_non_null + test_non_null
            )
        )
        if numeric:
            score = _numeric_psi(
                [float(value) for value in train_non_null],
                [float(value) for value in test_non_null],
            )
            metric = "population_stability_index"
        else:
            score = _categorical_total_variation(train_values, test_values)
            metric = "total_variation_distance"
        entries.append(
            {
                "feature": column,
                "metric": metric,
                "score": round(score, 5),
                "train_missing_rate": round(
                    sum(v is None for v in train_values) / len(train_values), 5
                ),
                "test_missing_rate": round(
                    sum(v is None for v in test_values) / len(test_values), 5
                ),
            }
        )
    entries.sort(key=lambda row: (-row["score"], row["feature"]))
    return {
        "status": "descriptive_only",
        "interpretation": "Drift identifies changed inputs; it does not establish a causal trend.",
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "features_evaluated": len(entries),
        "top_shifted_features": entries[:top_n],
    }


def build_promotion_decision(
    without_embeddings: dict[str, Any],
    run_manifest: dict[str, Any],
    taxonomy_gate: dict[str, Any],
    product_enrichment_gate: dict[str, Any],
) -> dict[str, Any]:
    """Apply M0/M1/M3 release gates without hiding failed conditions."""
    uncertainty = without_embeddings.get("uncertainty", {})
    mae_interval = uncertainty.get("mae_difference_confidence_interval_95") or [None, None]
    precision_interval = uncertainty.get("top_20pct_precision_confidence_interval_95") or [
        None,
        None,
    ]
    checks = {
        "taxonomy_adjudication_passed": taxonomy_gate.get("status") == "passed",
        "product_enrichment_passed": product_enrichment_gate.get("status") == "passed",
        "git_worktree_clean": run_manifest.get("git_worktree_dirty") is False,
        "advertiser_overlap_zero": without_embeddings.get("advertiser_overlap") == 0,
        "holdout_rows_at_least_100": without_embeddings.get("n_test", 0) >= 100,
        "holdout_advertisers_at_least_10": (without_embeddings.get("n_test_advertisers", 0) >= 10),
        "uncertainty_is_advertiser_clustered": (
            uncertainty.get("method") == "paired_advertiser_cluster_bootstrap"
            and uncertainty.get("n_clusters") == without_embeddings.get("n_test_advertisers")
        ),
        "mae_point_better_than_baseline": (
            without_embeddings.get("test_mae", float("inf"))
            < without_embeddings.get("baseline_mae", float("-inf"))
        ),
        "mae_bootstrap_interval_below_zero": mae_interval[1] is not None and mae_interval[1] < 0,
        "top_20pct_precision_lower_bound_above_prevalence": (
            precision_interval[0] is not None and precision_interval[0] > TOP_FRACTION
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "status": "promoted" if not failed else "blocked",
        "checks": checks,
        "failed_checks": failed,
        "policy": "joint-plan-M0-M1-M3-v1",
    }
