"""Leakage-resistant holdouts and cohort diagnostics for Meta ad models."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
from lifelines import KaplanMeierFitter

from pipeline.model_training.survival import is_event_observed


def temporal_advertiser_split(
    rows: list[dict[str, Any]],
    start_dates: dict[str, str],
    test_fraction: float = 0.2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Hold out brands first seen in the newest time window.

    This simultaneously prevents repeated-brand leakage and approximates the
    production question: how well do patterns transfer to newly observed
    advertisers in a later period? Rows without dates are dropped. A brand is
    wholly assigned to one side, so no page can cross the boundary.
    """
    dated = [r for r in rows if r.get("ad_id") in start_dates]
    if len(dated) < 2:
        return dated, [], {"strategy": "insufficient_dated_rows", "advertiser_overlap": 0}

    first_seen: dict[str, str] = {}
    for row in dated:
        group = str(row.get("page_id") or f"__ad__{row['ad_id']}")
        started = start_dates[row["ad_id"]]
        first_seen[group] = min(first_seen.get(group, started), started)
    ordered_groups = sorted(first_seen, key=lambda g: (first_seen[g], g))
    n_test_groups = max(1, int(np.ceil(len(ordered_groups) * test_fraction)))
    test_groups = set(ordered_groups[-n_test_groups:])

    train = [r for r in dated if str(r.get("page_id") or f"__ad__{r['ad_id']}") not in test_groups]
    test = [r for r in dated if str(r.get("page_id") or f"__ad__{r['ad_id']}") in test_groups]
    train_groups = {str(r.get("page_id") or f"__ad__{r['ad_id']}") for r in train}
    return (
        train,
        test,
        {
            "strategy": "newest_first_seen_advertiser_holdout",
            "advertiser_overlap": len(train_groups & test_groups),
            "n_train_advertisers": len(train_groups),
            "n_test_advertisers": len(test_groups),
            "test_period_start": min((start_dates[r["ad_id"]] for r in test), default=None),
        },
    )


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    mae = float(np.mean(np.abs(actual - predicted)))
    baseline = float(np.mean(np.abs(actual - np.median(actual))))
    return {
        "mae": round(mae, 4),
        "within_segment_median_mae": round(baseline, 4),
        "actual_mean": round(float(actual.mean()), 4),
        "predicted_mean": round(float(predicted.mean()), 4),
        "calibration_gap": round(float(predicted.mean() - actual.mean()), 4),
    }


def segment_metrics(
    rows: list[dict[str, Any]],
    actual: np.ndarray,
    predicted: np.ndarray,
    start_dates: dict[str, str],
    minimum_size: int = 10,
    promotion_minimum_size: int = 100,
    promotion_minimum_advertisers: int = 10,
) -> dict[str, Any]:
    """Evaluate transfer by supplement subcategory and calendar quarter.

    Small segments are reported as insufficient rather than producing noisy
    metrics that look authoritative. The adjudicated product subcategory is
    preferred; recorded search queries are only a legacy fallback.
    """
    by_category: dict[str, list[int]] = {}
    by_cohort: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        categories = (
            [row["product_subcategory"]]
            if row.get("product_subcategory")
            else (row.get("search_queries") or ["unknown"])
        )
        if isinstance(categories, str):
            categories = [categories]
        for category in set(str(c).lower() for c in categories if c):
            by_category.setdefault(category, []).append(i)
        started = start_dates.get(row.get("ad_id", ""))
        if started:
            d = date.fromisoformat(started[:10])
            by_cohort.setdefault(f"{d.year}-Q{(d.month - 1) // 3 + 1}", []).append(i)

    def summarize(groups: dict[str, list[int]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, indices in sorted(groups.items()):
            result[name] = {"n": len(indices), "status": "insufficient_sample"}
            if len(indices) >= minimum_size:
                result[name] = {
                    "n": len(indices),
                    "n_advertisers": len(
                        {
                            str(rows[index].get("page_id") or f"__ad__{rows[index].get('ad_id')}")
                            for index in indices
                        }
                    ),
                    "status": "ok",
                    **_metrics(actual[indices], predicted[indices]),
                }
                result[name]["promotion_supported"] = (
                    result[name]["n"] >= promotion_minimum_size
                    and result[name]["n_advertisers"] >= promotion_minimum_advertisers
                )
        return result

    return {
        "minimum_segment_size": minimum_size,
        "promotion_minimum_segment_size": promotion_minimum_size,
        "promotion_minimum_advertisers": promotion_minimum_advertisers,
        "subcategories": summarize(by_category),
        "cohorts": summarize(by_cohort),
    }


def longevity_benchmarks(
    rows: list[dict[str, Any]],
    ads: list[dict[str, Any]],
    scrape_dates: set[str],
    minimum_size: int = 20,
) -> dict[str, Any]:
    """Kaplan–Meier benchmarks for deciding when longevity evidence matures.

    The output is descriptive, not an invented binary success label. It
    reports the first observed duration at which estimated survival falls to
    75% and 50%; if censoring prevents a crossing, the threshold remains null.
    """
    ad_by_id = {str(a.get("ad_archive_id")): a for a in ads}

    def estimate(items: list[dict[str, Any]]) -> dict[str, Any]:
        durations: list[float] = []
        events: list[bool] = []
        for row in items:
            ad = ad_by_id.get(str(row.get("ad_id")))
            if not ad:
                continue
            durations.append(max(0.0, float(row.get("days_active") or 0)))
            events.append(is_event_observed(ad, scrape_dates))
        result: dict[str, Any] = {
            "n": len(durations),
            "events_observed": int(sum(events)),
            "status": "insufficient_sample" if len(durations) < minimum_size else "ok",
        }
        if len(durations) < minimum_size or not durations:
            return result
        km = KaplanMeierFitter().fit(durations, event_observed=events)
        timeline = km.survival_function_["KM_estimate"]
        for survival, label in ((0.75, "days_to_75pct_survival"), (0.5, "median_survival_days")):
            crossed = timeline[timeline <= survival]
            result[label] = round(float(crossed.index[0]), 1) if not crossed.empty else None
        result["survival_probability"] = {
            str(day): round(float(km.predict(day)), 4) for day in (30, 60, 90, 180)
        }
        if result["days_to_75pct_survival"] is None:
            result["status"] = "threshold_not_estimable_due_to_censoring"
        return result

    categories: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        labels = row.get("search_queries") or [row.get("product_subcategory", "unknown")]
        if isinstance(labels, str):
            labels = [labels]
        for label in set(str(x).lower() for x in labels if x):
            categories.setdefault(label, []).append(row)
    return {
        "interpretation": "Use as maturity evidence, not a universal binary-success cutoff.",
        "overall": estimate(rows),
        "by_subcategory": {name: estimate(items) for name, items in sorted(categories.items())},
    }
