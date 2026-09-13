"""CLI orchestrator: load the feature matrix, audit quality, fit the primary
composite-proxy attribution model, then evaluate the component signals.
``days_active`` uses censored Cox survival analysis; ``collation_count`` uses
XGBoost regression with an embeddings-in/embeddings-out ablation.

    uv run python -m pipeline.model_training.run_training
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.model_training.data_quality import build_quality_report, print_quality_report
from pipeline.model_training.evaluation import longevity_benchmarks, temporal_advertiser_split
from pipeline.model_training.preprocessing import (
    build_preprocessor,
    build_xy,
    load_start_dates,
)
from pipeline.model_training.success_score import train_and_explain
from pipeline.model_training.survival import (
    build_survival_xy,
    drop_zero_variance_columns,
    evaluate_cox_model,
    fit_cox_model,
    identify_scrape_dates,
)
from pipeline.model_training.trainer import print_results, train_and_evaluate

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_MATRIX_FILE = DATA_DIR / "feature_matrix.json"
DEFAULT_ADS_FILE = DATA_DIR / "supplements_enriched.json"
DEFAULT_REPORT_FILE = DATA_DIR / "model_training_report.json"

REGRESSION_TARGETS = ("collation_count",)


def _sha256(path: Path) -> str:
    """Hash an input without loading a second copy into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
    report_file: Path = DEFAULT_REPORT_FILE,
    random_state: int = 42,
    n_jobs: int = 1,
) -> dict[str, Any]:
    rows = json.loads(matrix_file.read_text())
    ads = json.loads(ads_file.read_text())

    quality_report = build_quality_report(rows)
    print_quality_report(quality_report)

    start_dates = load_start_dates(ads)
    train_rows, test_rows, split_details = temporal_advertiser_split(rows, start_dates)
    dropped = len(rows) - len(train_rows) - len(test_rows)
    print(
        f"\nTemporal advertiser split: {len(train_rows)} train / {len(test_rows)} test "
        f"({dropped} rows dropped, no start_date match)"
    )

    model_results: dict[str, dict[str, Any]] = {}

    # Primary question: which creative features distinguish top ads under the
    # project's complete Longevity × Scaling + Variant proxy? Keep this on raw
    # rows so target-derived cluster summaries cannot leak into the label fit.
    model_results["composite_success_score"] = {}
    for label, include_embeddings in (("with_embeddings", True), ("without_embeddings", False)):
        composite = train_and_explain(
            rows,
            include_embeddings=include_embeddings,
            random_state=random_state,
            n_jobs=n_jobs,
            start_dates=start_dates,
        )
        model_results["composite_success_score"][label] = composite
        print(f"\n=== composite_success_score ({label}) ===")
        print(
            f"  split={composite['split_strategy']} advertiser_overlap="
            f"{composite['advertiser_overlap']} top-20%-precision="
            f"{composite['top_20pct_precision']}"
        )

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
    print(
        f"  concordance index: {survival_results['concordance_index']} "
        f"(status={survival_results['evaluation_status']}; 0.5=random, 1.0=perfect)"
    )
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
            results = train_and_evaluate(X_tr, y_tr, X_te, y_te, n_jobs=n_jobs)
            print_results(target, label, results)
            model_results[target][label] = results

    report = {
        "run_manifest": {
            "matrix_file": str(matrix_file),
            "matrix_sha256": _sha256(matrix_file),
            "ads_file": str(ads_file),
            "ads_sha256": _sha256(ads_file),
            "random_state": random_state,
            "workers_per_model": n_jobs,
        },
        "quality_report": {
            "columns": quality_report["columns"],
            "flagged_columns": [p["column"] for p in quality_report["flagged"]],
        },
        "split": {
            "n_train": len(train_rows), "n_test": len(test_rows), "n_dropped": dropped,
            **split_details,
        },
        "longevity_benchmarks": longevity_benchmarks(
            rows, ads, identify_scrape_dates(ads)
        ),
        "model_results": model_results,
    }
    atomic_write_json(report_file, report)
    print(f"\nWrote {report_file}")
    return report


def main() -> None:
    """CLI entry point with exclusive report ownership."""
    import argparse

    parser = argparse.ArgumentParser(description="Train Meta ad proxy models.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_FILE)
    parser.add_argument("--ads", type=Path, default=DEFAULT_ADS_FILE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_FILE)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument(
        "--workers-per-model",
        type=int,
        default=1,
        help="CPU workers used by each XGBoost fit (default 1 for safe concurrent jobs).",
    )
    args = parser.parse_args()
    if args.workers_per_model < 1:
        parser.error("--workers-per-model must be at least 1")
    with exclusive_output(args.report):
        run(
            matrix_file=args.matrix,
            ads_file=args.ads,
            report_file=args.report,
            random_state=args.random_state,
            n_jobs=args.workers_per_model,
        )


if __name__ == "__main__":
    main()
