"""Orchestrates the full feature/data validation suite: loads the feature
matrix + the already-produced model_training_report.json (no re-training),
runs every data_quality.py check (existing + new) per column per target,
joins with feature_registry.py's human descriptions, and writes a Markdown
+ JSON report answering "is the data going into the model actually good."

    uv run python -m pipeline.model_training.validation_report
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.model_training.data_quality import (
    assess_dimensionality,
    build_quality_report,
    compute_missingness_bias,
    compute_target_correlation,
    flag_thin_categories,
)
from pipeline.model_training.feature_registry import get_feature_meta
from pipeline.model_training.preprocessing import TARGET_COLUMNS, load_start_dates, time_based_split

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_MATRIX_FILE = DATA_DIR / "feature_matrix.json"
DEFAULT_ADS_FILE = DATA_DIR / "supplements_enriched.json"
DEFAULT_TRAINING_REPORT_FILE = DATA_DIR / "model_training_report.json"
DEFAULT_REPORT_MD = DATA_DIR / "feature_validation_report.md"
DEFAULT_REPORT_JSON = DATA_DIR / "feature_validation_report.json"

_ID_AND_LABEL_COLUMNS = {"ad_id", "price_tier"}  # profiled, but not run through target correlation


def _find_model_importance(column: str, training_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Every (target, variant, transformed_name, importance) entry from
    model_training_report.json whose transformed name traces back to this
    raw column -- preprocessing.py prefixes numeric columns as
    `numeric__{col}` and expands categoricals into one-hot levels as
    `categorical__{col}_{level}`, so a substring match on both prefixes
    covers both cases. Only the top 20 entries per model are persisted in
    the training report, so absence here means "not in the top 20", not
    necessarily "zero importance"."""
    hits: list[dict[str, Any]] = []
    for target, variants in training_report.get("model_results", {}).items():
        for variant_name, variant_result in variants.items():
            entries = (
                variant_result.get("top_covariates") or variant_result.get("top_features") or []
            )
            for name, importance in entries:
                if name == f"numeric__{column}" or name.startswith(f"categorical__{column}_"):
                    hits.append({
                        "target": target, "variant": variant_name,
                        "transformed_name": name, "importance": importance,
                    })
    return hits


def build_validation_report(
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
    training_report_file: Path = DEFAULT_TRAINING_REPORT_FILE,
) -> dict[str, Any]:
    rows = json.loads(matrix_file.read_text())
    ads = json.loads(ads_file.read_text())
    training_report = (
        json.loads(training_report_file.read_text()) if training_report_file.exists() else {}
    )

    quality = build_quality_report(rows)

    start_dates = load_start_dates(ads)
    train_rows, _test_rows = time_based_split(rows, start_dates)

    # Dimensionality: reuses the exact with/without-embeddings feature
    # counts already computed and persisted by run_training.py, rather
    # than re-deriving them (which would require re-running preprocessing).
    features_by_variant: dict[str, int] = {}
    for target, variants in training_report.get("model_results", {}).items():
        for variant_name, variant_result in variants.items():
            if "n_features" in variant_result:
                features_by_variant[f"{target}:{variant_name}"] = variant_result["n_features"]
    dimensionality = assess_dimensionality(len(train_rows), features_by_variant)

    feature_entries: dict[str, Any] = {}
    for column, profile in quality["columns"].items():
        if column == "ad_id":
            continue
        meta = get_feature_meta(column)
        entry: dict[str, Any] = {
            "column": column,
            "kind": meta.kind,
            "source": meta.source,
            "description": meta.description,
            "caveats": meta.caveats,
            "profile": profile,
            "model_importance": _find_model_importance(column, training_report),
        }

        if column not in _ID_AND_LABEL_COLUMNS and column not in TARGET_COLUMNS:
            entry["target_correlation"] = compute_target_correlation(rows, column, TARGET_COLUMNS)
            if profile["null_rate"] > 0:
                entry["missingness_bias"] = compute_missingness_bias(rows, column, TARGET_COLUMNS)
            if profile["kind"] == "categorical":
                entry["thin_categories"] = flag_thin_categories(rows, column)

        feature_entries[column] = entry

    n_flagged = len(quality["flagged"])
    n_mnar = sum(
        1 for e in feature_entries.values()
        for tv in e.get("missingness_bias", {}).values()
        if tv.get("likely_mnar")
    )
    dimensionality_flags = [
        f"{variant}: {info['flags'][0]}"
        for variant, info in dimensionality["variants"].items() if info["flags"]
    ]

    report = {
        "summary": {
            "n_rows": len(rows),
            "n_train_rows": len(train_rows),
            "n_features": len(feature_entries),
            "n_columns_flagged_by_quality_report": n_flagged,
            "n_missingness_not_at_random": n_mnar,
            "dimensionality": dimensionality,
            "dimensionality_risk_flags": dimensionality_flags,
        },
        "features": feature_entries,
    }
    return report


def _fmt_correlation(tc: dict[str, Any]) -> str:
    if tc.get("method") == "pearson" and tc.get("r") is not None:
        sig = "significant" if tc["p"] < 0.05 else "not significant"
        return f"r={tc['r']:+.3f}, p={tc['p']:.4f} ({sig}, n={tc['n']})"
    if tc.get("method") == "anova" and tc.get("f") is not None:
        sig = "significant" if tc["p"] < 0.05 else "not significant"
        return f"F={tc['f']:.3f}, p={tc['p']:.4f} ({sig}, {tc['n_groups']} groups, n={tc['n']})"
    return f"insufficient data (n={tc.get('n', tc.get('n_null', 0) + tc.get('n_non_null', 0))})"


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines: list[str] = []
    lines.append("# Feature/Data Validation Report\n")
    lines.append(
        "Answers: is the data going into the model sufficient, anomaly-free, "
        "and are null counts silently biasing the output? Generated from "
        "`data/feature_matrix.json` + `data/model_training_report.json` -- no "
        "re-training, pure analysis of what's already there.\n"
    )

    lines.append("## Executive Summary\n")
    lines.append(f"- **Rows**: {s['n_rows']} total, {s['n_train_rows']} in the training split")
    lines.append(f"- **Features profiled**: {s['n_features']}")
    lines.append(
        f"- **Columns flagged by the quality report**: {s['n_columns_flagged_by_quality_report']}"
        f"/{s['n_features']} (high null rate, high point-mass/default-fill artifact, "
        "high outlier rate, or high cardinality)"
    )
    lines.append(
        f"- **Columns with missing-not-at-random evidence**: {s['n_missingness_not_at_random']} "
        "(a column's own null-ness statistically correlates with a target -- median/"
        "most-frequent imputation silently biases these)"
    )
    if s["dimensionality_risk_flags"]:
        lines.append("- **Dimensionality risk (rows vs. features)**:")
        for f in s["dimensionality_risk_flags"]:
            lines.append(f"  - {f}")
    lines.append("")

    lines.append("## Known Open Findings (flagged, not fixed in this pass)\n")
    lines.append(
        "1. **Cox survival model (`days_active`) has no outlier handling.** XGBoost's "
        "tree splits are naturally robust to extreme values; Cox's linear coefficient "
        "fit is not. `days_active` itself shows a real outlier rate in the profile "
        "below, feeding directly into the Cox covariates uncapped."
    )
    lines.append(
        "2. **The `with_embeddings` ablation variant may have more raw features than "
        "training rows** -- see the dimensionality risk flags above if present. Left "
        "open per an explicit decision: fix (dimensionality reduction) is a follow-up, "
        "not bundled into this report.\n"
    )

    lines.append("## Per-Feature Detail\n")
    lines.append(
        "Sorted worst-first (most quality-report flags, then alphabetically) so the "
        "riskiest columns surface first.\n"
    )

    def _risk_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        name, entry = item
        return (-len(entry["profile"]["flags"]), name)

    for column, entry in sorted(report["features"].items(), key=_risk_key):
        p = entry["profile"]
        lines.append(f"### `{column}` ({entry['kind']})\n")
        lines.append(f"{entry['description']}")
        if entry["caveats"]:
            lines.append(f"\n> **Caveat**: {entry['caveats']}")
        lines.append(f"\n*Source*: `{entry['source']}`\n")
        lines.append(f"- Null rate: {p['null_rate']:.1%}")
        if p["flags"]:
            lines.append("- Quality flags:")
            for f in p["flags"]:
                lines.append(f"  - {f}")
        else:
            lines.append("- Quality flags: none")

        if "target_correlation" in entry:
            lines.append("- Correlation with targets:")
            for target, tc in entry["target_correlation"].items():
                lines.append(f"  - `{target}`: {_fmt_correlation(tc)}")

        if entry.get("missingness_bias"):
            for target, mb in entry["missingness_bias"].items():
                if mb.get("likely_mnar"):
                    lines.append(
                        f"- ⚠️ Missing-not-at-random vs `{target}`: mean when null = "
                        f"{mb['mean_when_null']}, mean when present = {mb['mean_when_non_null']} "
                        f"(p={mb['p']:.4f})"
                    )

        if entry.get("thin_categories"):
            thin = ", ".join(
                f"{t['value']!r} (n={t['count']})" for t in entry["thin_categories"][:5]
            )
            lines.append(f"- Thin categories (n<30): {thin}")

        if entry["model_importance"]:
            lines.append("- Appeared in trained-model top features:")
            for imp in entry["model_importance"][:5]:
                lines.append(
                    f"  - `{imp['target']}` ({imp['variant']}): "
                    f"{imp['transformed_name']} = {imp['importance']}"
                )

        lines.append("")

    return "\n".join(lines)


def run(
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
    training_report_file: Path = DEFAULT_TRAINING_REPORT_FILE,
    md_out: Path = DEFAULT_REPORT_MD,
    json_out: Path = DEFAULT_REPORT_JSON,
) -> dict[str, Any]:
    report = build_validation_report(matrix_file, ads_file, training_report_file)
    md_out.write_text(render_markdown(report))
    json_out.write_text(json.dumps(report, indent=2, default=str))
    print(f"Wrote {md_out}")
    print(f"Wrote {json_out}")
    print(
        f"\n{report['summary']['n_columns_flagged_by_quality_report']}/"
        f"{report['summary']['n_features']} columns flagged, "
        f"{report['summary']['n_missingness_not_at_random']} missing-not-at-random"
    )
    return report


if __name__ == "__main__":
    run()
