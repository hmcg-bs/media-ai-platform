"""CLI orchestrator: load the feature matrix -> data-quality report ->
time-based split -> category-trend feature injection -> train+evaluate per
target variable. `days_active` uses censored Cox survival analysis (see
survival.py for why); `collation_count`/`variants_featured_count` use plain
XGBoost regression with an embeddings-in/embeddings-out ablation.

    uv run python -m pipeline.model_training.run_training
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.model_training.category_trends import TREND_FEATURE_NAMES, EmbeddingClusterTrends
from pipeline.model_training.data_quality import build_quality_report, print_quality_report
from pipeline.model_training.preprocessing import (
    build_preprocessor,
    build_xy,
    load_start_dates,
    time_based_split,
)
from pipeline.model_training.survival import (
    build_survival_xy,
    drop_zero_variance_columns,
    evaluate_cox_model,
    fit_cox_model,
)
from pipeline.model_training.trainer import print_results, train_and_evaluate

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_MATRIX_FILE = DATA_DIR / "feature_matrix.json"
DEFAULT_ADS_FILE = DATA_DIR / "supplements_enriched.json"
DEFAULT_REPORT_FILE = DATA_DIR / "model_training_report.json"

REGRESSION_TARGETS = ("collation_count", "variants_featured_count")


def _add_cluster_trend_features(
    rows: list[dict[str, Any]], trends: EmbeddingClusterTrends
) -> list[dict[str, Any]]:
    """Returns new row dicts (originals untouched) with cluster-trend
    features merged in as plain top-level keys, so build_xy picks them up
    like any other numeric column with no special-casing."""
    trend_values = trends.transform(rows)
    return [{**row, **tv} for row, tv in zip(rows, trend_values, strict=True)]


def run(
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
    report_file: Path = DEFAULT_REPORT_FILE,
) -> dict[str, Any]:
    rows = json.loads(matrix_file.read_text())
    ads = json.loads(ads_file.read_text())

    quality_report = build_quality_report(rows)
    print_quality_report(quality_report)

    start_dates = load_start_dates(ads)
    train_rows, test_rows = time_based_split(rows, start_dates)
    dropped = len(rows) - len(train_rows) - len(test_rows)
    print(
        f"\nTime-based split: {len(train_rows)} train / {len(test_rows)} test "
        f"({dropped} rows dropped, no start_date match)"
    )

    # Category-trend / pattern-transfer features: fit strictly on train,
    # transform both splits against that fit -- see category_trends.py.
    # Train rows' own target value contributes to their own cluster's mean
    # (standard target-encoding practice, not fixed here); test rows never
    # see any test-set outcome, which is what keeps the reported test
    # metrics honest.
    trends = EmbeddingClusterTrends().fit(train_rows)
    train_rows = _add_cluster_trend_features(train_rows, trends)
    test_rows = _add_cluster_trend_features(test_rows, trends)
    print(f"Category-trend features added: {TREND_FEATURE_NAMES}")

    model_results: dict[str, dict[str, Any]] = {}

    # --- days_active: censored survival analysis (see survival.py) ---
    X_train, duration_train, event_train = build_survival_xy(train_rows, ads)
    X_test, duration_test, event_test = build_survival_xy(test_rows, ads)
    preprocessor = build_preprocessor(X_train)
    X_train_processed = preprocessor.fit_transform(X_train)
    X_test_processed = preprocessor.transform(X_test)
    feature_names = preprocessor.get_feature_names_out()
    import pandas as pd

    X_train_df = pd.DataFrame(X_train_processed, columns=feature_names)
    X_test_df = pd.DataFrame(X_test_processed, columns=feature_names)
    X_train_df, X_test_df, dropped_cols = drop_zero_variance_columns(X_train_df, X_test_df)
    if dropped_cols:
        print(f"  Dropped {len(dropped_cols)} zero-variance columns before Cox fit: {dropped_cols}")

    cox_model = fit_cox_model(X_train_df, duration_train, event_train)
    survival_results = evaluate_cox_model(cox_model, X_test_df, duration_test, event_test)
    survival_results["n_train"] = len(X_train_df)
    survival_results["n_events_observed_train"] = int(event_train.sum())
    print("\n=== days_active (Cox Proportional Hazards, censored) ===")
    print(f"  train/test rows: {survival_results['n_train']}/{survival_results['n_test']}")
    print(
        f"  events observed: {survival_results['n_events_observed_train']}/"
        f"{survival_results['n_train']} train, "
        f"{survival_results['n_events_observed_test']}/{survival_results['n_test']} test "
        "(rest right-censored, still running or unknown at scrape time)"
    )
    print(f"  concordance index: {survival_results['concordance_index']} (0.5=random, 1.0=perfect)")
    print("  top covariates (by |coefficient|):")
    for name, coef in survival_results["top_covariates"][:10]:
        print(f"    {coef:+.4f}  {name}")
    model_results["days_active"] = {"cox_survival": survival_results}

    # --- collation_count / variants_featured_count: XGBoost regression ---
    for target in REGRESSION_TARGETS:
        model_results[target] = {}
        for label, include_embeddings in (("with_embeddings", True), ("without_embeddings", False)):
            X_tr, y_tr = build_xy(train_rows, target, include_embeddings=include_embeddings)
            X_te, y_te = build_xy(test_rows, target, include_embeddings=include_embeddings)
            if len(y_tr) < 20 or len(y_te) < 5:
                print(f"\n=== {target} ({label}) === skipped, too few labeled rows "
                      f"(train={len(y_tr)}, test={len(y_te)})")
                continue
            results = train_and_evaluate(X_tr, y_tr, X_te, y_te)
            print_results(target, label, results)
            model_results[target][label] = results

    report = {
        "quality_report": {
            "columns": quality_report["columns"],
            "flagged_columns": [p["column"] for p in quality_report["flagged"]],
        },
        "split": {"n_train": len(train_rows), "n_test": len(test_rows), "n_dropped": dropped},
        "model_results": model_results,
    }
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {report_file}")
    return report


if __name__ == "__main__":
    run()
