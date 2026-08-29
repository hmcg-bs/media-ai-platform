"""Per-column data-quality report for the feature matrix — null rates,
outlier rates, and "suspicious point-mass" detection (a column dominated by
one repeated value, usually a silent default-fill rather than real signal).

Built because several feature-engineering functions fill missing data with a
plausible-looking constant instead of leaving a real null (confirmed by
reading the source, not assumed): `color_features.py::extract_color_features`
defaults `palette_vibrancy` to 0.5 and `psychological_warmth_index` to 0.0
when no creative_features exist for an ad — indistinguishable from a real
computed 0.5/0.0 without this kind of check. Since only ~46% of the corpus
has Step 2 creative_features at all, every `creative_*`-prefixed column and
these two color defaults carry a real "missing, not measured" population
mixed into their "real value" population.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Literal

ColumnKind = Literal["numeric", "boolean", "categorical", "embedding", "id"]

# Columns whose real semantics are "id" or "label", not a feature to profile
# the same way as everything else.
_ID_COLUMNS = {"ad_id"}
_EMBEDDING_COLUMNS = {"title_embedding", "body_embedding", "usp_embedding"}

# High null rate / high point-mass thresholds -- tunable, not load-bearing
# constants elsewhere in the codebase.
NULL_RATE_FLAG_THRESHOLD = 0.30
POINT_MASS_FLAG_THRESHOLD = 0.40
OUTLIER_RATE_FLAG_THRESHOLD = 0.05
HIGH_CARDINALITY_RATIO_THRESHOLD = 0.5


def classify_column(name: str, values: list[Any]) -> ColumnKind:
    if name in _ID_COLUMNS:
        return "id"
    if name in _EMBEDDING_COLUMNS:
        return "embedding"
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "categorical"  # can't tell; treat conservatively
    sample = non_null[0]
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, (int, float)):
        return "numeric"
    return "categorical"


def _iqr_outlier_rate(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[(3 * n) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return 0.0
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = sum(1 for v in values if v < lo or v > hi)
    return outliers / len(values)


def _point_mass(values: list[Any]) -> tuple[Any, float]:
    """Returns (most_common_value, fraction_of_non_null_rows_holding_it)."""
    if not values:
        return None, 0.0
    counts: dict[Any, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    mode_val, mode_count = max(counts.items(), key=lambda kv: kv[1])
    return mode_val, mode_count / len(values)


def profile_column(name: str, values: list[Any]) -> dict[str, Any]:
    """Computes stats + flags for one column's raw values across every row
    (None allowed, one entry per row, same order not required)."""
    n = len(values)
    non_null = [v for v in values if v is not None]
    null_rate = 1 - (len(non_null) / n) if n else 0.0
    kind = classify_column(name, values)

    stats: dict[str, Any] = {"column": name, "kind": kind, "n": n, "null_rate": round(null_rate, 4)}
    flags: list[str] = []
    if null_rate >= NULL_RATE_FLAG_THRESHOLD:
        flags.append(f"high_null_rate ({null_rate:.0%} missing)")

    if kind == "embedding":
        empty = sum(1 for v in values if not v)
        empty_rate = empty / n if n else 0.0
        stats["empty_rate"] = round(empty_rate, 4)
        dims = {len(v) for v in values if v}
        stats["observed_dims"] = sorted(dims)
        if empty_rate >= NULL_RATE_FLAG_THRESHOLD:
            flags.append(f"high_empty_rate ({empty_rate:.0%} empty vector)")
        if len(dims) > 1:
            flags.append(f"inconsistent_dims {sorted(dims)}")

    elif kind == "numeric":
        nums = [float(v) for v in non_null]
        if nums:
            stats["mean"] = round(statistics.fmean(nums), 4)
            stats["median"] = round(statistics.median(nums), 4)
            stats["min"] = round(min(nums), 4)
            stats["max"] = round(max(nums), 4)
            stats["stdev"] = round(statistics.pstdev(nums), 4) if len(nums) > 1 else 0.0
            mode_val, mode_frac = _point_mass(nums)
            stats["point_mass_value"] = mode_val
            stats["point_mass_fraction"] = round(mode_frac, 4)
            outlier_rate = _iqr_outlier_rate(nums)
            stats["outlier_rate"] = round(outlier_rate, 4)
            if stats["stdev"] == 0.0 and len(non_null) > 1:
                flags.append("zero_variance (every non-null value identical)")
            elif mode_frac >= POINT_MASS_FLAG_THRESHOLD:
                flags.append(
                    f"high_point_mass ({mode_frac:.0%} of non-null values == {mode_val!r} "
                    "-- likely a default-fill artifact, not real signal)"
                )
            if outlier_rate >= OUTLIER_RATE_FLAG_THRESHOLD:
                flags.append(f"high_outlier_rate ({outlier_rate:.0%} beyond 1.5x IQR)")

    elif kind == "boolean":
        true_rate = sum(1 for v in non_null if v) / len(non_null) if non_null else 0.0
        stats["true_rate"] = round(true_rate, 4)
        if true_rate in (0.0, 1.0) and len(non_null) > 1:
            flags.append(f"zero_variance (always {'True' if true_rate == 1.0 else 'False'})")

    elif kind == "categorical":
        counts: dict[Any, int] = {}
        for v in non_null:
            counts[v] = counts.get(v, 0) + 1
        stats["unique_values"] = len(counts)
        stats["top_values"] = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
        if non_null:
            _, mode_frac = _point_mass(non_null)
            stats["point_mass_fraction"] = round(mode_frac, 4)
            if mode_frac >= POINT_MASS_FLAG_THRESHOLD:
                flags.append(
                    f"high_point_mass ({mode_frac:.0%} of non-null values == "
                    f"{stats['top_values'][0][0]!r})"
                )
            cardinality_ratio = len(counts) / len(non_null)
            if cardinality_ratio >= HIGH_CARDINALITY_RATIO_THRESHOLD and len(counts) > 20:
                flags.append(
                    f"high_cardinality ({len(counts)} unique values across "
                    f"{len(non_null)} non-null rows)"
                )

    stats["flags"] = flags
    return stats


def build_quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: the feature-matrix rows (list of flat dicts, e.g. loaded from
    data/feature_matrix.json). Returns {"columns": {name: profile}, "flagged":
    [profiles with >=1 flag, sorted worst-first by null_rate]}."""
    if not rows:
        return {"columns": {}, "flagged": []}

    all_columns = sorted({k for row in rows for k in row})
    columns: dict[str, Any] = {}
    for col in all_columns:
        values = [row.get(col) for row in rows]
        columns[col] = profile_column(col, values)

    flagged = sorted(
        (p for p in columns.values() if p["flags"]),
        key=lambda p: -p["null_rate"],
    )
    return {"columns": columns, "flagged": flagged}


def compute_target_correlation(
    rows: list[dict[str, Any]], column: str, targets: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Pearson r (numeric/boolean columns) or one-way ANOVA F-stat
    (categorical columns) against each target -- an independent check of
    whether a feature actually relates to what we're predicting, distinct
    from (and a useful cross-check against) a trained model's own
    feature_importances_/Cox coefficients, which can be inflated by
    collinearity or tree bias toward high-cardinality columns."""
    from scipy import stats as scipy_stats

    values = [row.get(column) for row in rows]
    kind = classify_column(column, values)
    result: dict[str, dict[str, Any]] = {}

    for target in targets:
        pairs = [
            (row.get(column), row.get(target))
            for row in rows
            if row.get(column) is not None and row.get(target) is not None
        ]
        if len(pairs) < 10:
            result[target] = {"method": "insufficient_data", "n": len(pairs)}
            continue

        if kind in ("numeric", "boolean"):
            xs = [float(x) for x, _ in pairs]
            ys = [float(y) for _, y in pairs]
            if len(set(xs)) < 2 or len(set(ys)) < 2:
                result[target] = {"method": "pearson", "n": len(pairs), "r": None, "p": None}
                continue
            r, p = scipy_stats.pearsonr(xs, ys)
            result[target] = {
                "method": "pearson", "n": len(pairs),
                "r": round(float(r), 4), "p": round(float(p), 6),
            }

        elif kind == "categorical":
            groups: dict[Any, list[float]] = {}
            for x, y in pairs:
                groups.setdefault(x, []).append(float(y))
            group_values = [v for v in groups.values() if len(v) >= 2]
            if len(group_values) < 2:
                result[target] = {"method": "anova", "n": len(pairs), "f": None, "p": None}
                continue
            f_stat, p = scipy_stats.f_oneway(*group_values)
            result[target] = {
                "method": "anova", "n": len(pairs), "n_groups": len(group_values),
                "f": round(float(f_stat), 4) if not math.isnan(f_stat) else None,
                "p": round(float(p), 6) if not math.isnan(p) else None,
            }
        else:
            result[target] = {"method": "unsupported", "n": len(pairs)}

    return result


def compute_missingness_bias(
    rows: list[dict[str, Any]], column: str, targets: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Mean target value for rows where `column` is null vs. non-null, plus
    a Welch's t-test -- flags when a column's own missingness correlates
    with the target ("missing not at random"), which plain null-rate
    reporting can't reveal and which silently biases median/most-frequent
    imputation (preprocessing.py's SimpleImputer treats every null as
    interchangeable, which is only safe when missingness itself carries no
    signal)."""
    from scipy import stats as scipy_stats

    result: dict[str, dict[str, Any]] = {}
    for target in targets:
        null_group = [
            float(row[target]) for row in rows
            if row.get(column) is None and row.get(target) is not None
        ]
        non_null_group = [
            float(row[target]) for row in rows
            if row.get(column) is not None and row.get(target) is not None
        ]

        if len(null_group) < 10 or len(non_null_group) < 10:
            result[target] = {
                "method": "insufficient_data",
                "n_null": len(null_group), "n_non_null": len(non_null_group),
            }
            continue

        t_stat, p = scipy_stats.ttest_ind(null_group, non_null_group, equal_var=False)
        result[target] = {
            "n_null": len(null_group),
            "n_non_null": len(non_null_group),
            "mean_when_null": round(statistics.fmean(null_group), 4),
            "mean_when_non_null": round(statistics.fmean(non_null_group), 4),
            "t_stat": round(float(t_stat), 4),
            "p": round(float(p), 6),
            "likely_mnar": bool(p < 0.05),
        }
    return result


def flag_thin_categories(
    rows: list[dict[str, Any]], column: str, min_n: int = 30
) -> list[dict[str, Any]]:
    """Category levels with fewer than min_n examples -- too small to trust
    a group statistic (e.g. a target-correlation ANOVA group mean) computed
    from them. Recomputes full counts directly from rows rather than
    relying on profile_column's own top-5-truncated top_values, so a thin
    category outside the top 5 is never silently missed."""
    counts: dict[Any, int] = {}
    for row in rows:
        v = row.get(column)
        if v is not None:
            counts[v] = counts.get(v, 0) + 1
    return [
        {"value": v, "count": c}
        for v, c in sorted(counts.items(), key=lambda kv: kv[1])
        if c < min_n
    ]


def assess_dimensionality(n_train_rows: int, features_by_variant: dict[str, int]) -> dict[str, Any]:
    """Rows-vs-features ratio per model variant -- flags when a variant has
    more raw features than training rows (severe overfitting risk, missed
    so far because only test-set R2/concordance was being checked, never
    this ratio) or falls short of the classic ~10x-rows-per-feature rule of
    thumb."""
    result: dict[str, Any] = {"n_train_rows": n_train_rows, "variants": {}}
    for variant, n_features in features_by_variant.items():
        ratio = n_train_rows / n_features if n_features else float("inf")
        flags: list[str] = []
        if n_features >= n_train_rows:
            flags.append(
                f"features_exceed_rows ({n_features} features >= {n_train_rows} "
                "training rows -- severe overfitting risk)"
            )
        elif ratio < 10:
            flags.append(
                f"thin_ratio ({ratio:.1f} rows per feature, below the classic 10x rule of thumb)"
            )
        result["variants"][variant] = {
            "n_features": n_features, "rows_per_feature": round(ratio, 2), "flags": flags,
        }
    return result


def print_quality_report(report: dict[str, Any]) -> None:
    flagged = report["flagged"]
    print(f"Data-quality report: {len(flagged)}/{len(report['columns'])} columns flagged\n")
    for p in flagged:
        print(f"  {p['column']:45} ({p['kind']:11}) null={p['null_rate']:.0%}")
        for f in p["flags"]:
            print(f"      - {f}")
