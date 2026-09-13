"""CLI orchestrator: load the feature matrix, audit quality, fit the primary
composite-proxy attribution model, then evaluate the component signals.
``days_active`` uses censored Cox survival analysis; ``collation_count`` uses
XGBoost regression with an embeddings-in/embeddings-out ablation.

    uv run python -m pipeline.model_training.run_training
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.model_training.data_quality import build_quality_report, print_quality_report
from pipeline.model_training.evaluation import longevity_benchmarks, temporal_advertiser_split
from pipeline.model_training.model_evidence import build_promotion_decision
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
REPORT_SCHEMA_VERSION = "meta-ads-training-report-v2"


def _implementation_sha256() -> str:
    """Fingerprint the model/report implementation without relying on Git state."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    """Hash an input without loading a second copy into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_provenance() -> dict[str, Any]:
    """Record review provenance without making Git state the reproducibility key."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_branch": None, "git_worktree_dirty": None}
    return {"git_commit": commit, "git_branch": branch, "git_worktree_dirty": dirty}


def _taxonomy_gate(ads: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify every training ad came through the human adjudication boundary."""
    sample_hashes = set()
    label_hashes = set()
    failures = []
    for ad in ads:
        label = ad.get("taxonomy_label") or {}
        provenance = label.get("provenance") or {}
        ad_id = str(ad.get("ad_archive_id") or "")
        if label.get("source") != "human_adjudication" or label.get("is_supplement") is not True:
            failures.append(f"ad {ad_id or '<missing>'} lacks an approved human taxonomy label")
        sample_hash = provenance.get("sample_sha256")
        if not sample_hash or len(str(sample_hash)) != 64:
            failures.append(f"ad {ad_id or '<missing>'} lacks taxonomy sample provenance")
        else:
            sample_hashes.add(str(sample_hash))
        if (
            provenance.get("review_schema_version") != "supplements-taxonomy-review-v1"
            or provenance.get("adjudication_report_schema_version")
            != "supplements-taxonomy-adjudication-report-v1"
            or not provenance.get("reviewer_id")
            or not provenance.get("reviewed_at")
        ):
            failures.append(f"ad {ad_id or '<missing>'} has incomplete reviewer provenance")
        label_hash = provenance.get("approved_labels_content_sha256")
        if not label_hash or len(str(label_hash)) != 64:
            failures.append(f"ad {ad_id or '<missing>'} lacks approved-label content provenance")
        else:
            label_hashes.add(str(label_hash))
    if len(sample_hashes) > 1:
        failures.append("training ads combine multiple taxonomy sample versions")
    if len(label_hashes) > 1:
        failures.append("training ads combine multiple approved taxonomy label versions")
    return {
        "status": "passed" if ads and not failures else "failed",
        "n_training_ads": len(ads),
        "sample_sha256": next(iter(sample_hashes), None) if len(sample_hashes) == 1 else None,
        "approved_labels_content_sha256": (
            next(iter(label_hashes), None) if len(label_hashes) == 1 else None
        ),
        "failures": failures[:100],
    }


def run(
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
    report_file: Path = DEFAULT_REPORT_FILE,
    random_state: int = 42,
    n_jobs: int = 1,
) -> dict[str, Any]:
    rows = json.loads(matrix_file.read_text())
    ads = json.loads(ads_file.read_text())
    taxonomy_gate = _taxonomy_gate(ads)
    if taxonomy_gate["status"] != "passed":
        raise ValueError(
            "training ads did not pass the human taxonomy gate: "
            + "; ".join(taxonomy_gate["failures"][:5])
        )

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
    has_embeddings = any(
        row.get(name)
        for row in rows
        for name in ("title_embedding", "body_embedding", "usp_embedding")
    )

    # Primary question: which creative features distinguish top ads under the
    # project's complete Longevity × Scaling + Variant proxy? Keep this on raw
    # rows so target-derived cluster summaries cannot leak into the label fit.
    model_results["composite_success_score"] = {}
    composite_variants = [("without_embeddings", False)]
    if has_embeddings:
        composite_variants.insert(0, ("with_embeddings", True))
    for label, include_embeddings in composite_variants:
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

    train_events = int(event_train.sum())
    if train_events < 5:
        survival_results = {
            "n_test": len(X_test_df),
            "n_events_observed_test": int(event_test.sum()),
            "concordance_index": None,
            "evaluation_status": "insufficient_observed_events",
            "top_covariates": [],
        }
    else:
        cox_model = fit_cox_model(X_train_df, duration_train, event_train)
        survival_results = evaluate_cox_model(cox_model, X_test_df, duration_test, event_test)
    survival_results["n_train"] = len(X_train_df)
    survival_results["n_events_observed_train"] = train_events
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
        regression_variants = [("without_embeddings", False)]
        if has_embeddings:
            regression_variants.insert(0, ("with_embeddings", True))
        for label, include_embeddings in regression_variants:
            X_tr, y_tr = build_xy(train_rows, target, include_embeddings=include_embeddings)
            X_te, y_te = build_xy(test_rows, target, include_embeddings=include_embeddings)
            if len(y_tr) < 20 or len(y_te) < 5:
                print(
                    f"\n=== {target} ({label}) === skipped, too few labeled rows "
                    f"(train={len(y_tr)}, test={len(y_te)})"
                )
                continue
            results = train_and_evaluate(X_tr, y_tr, X_te, y_te, n_jobs=n_jobs)
            print_results(target, label, results)
            model_results[target][label] = results

    run_manifest = {
        "matrix_file": str(matrix_file),
        "matrix_sha256": _sha256(matrix_file),
        "ads_file": str(ads_file),
        "ads_sha256": _sha256(ads_file),
        "random_state": random_state,
        "workers_per_model": n_jobs,
        "embeddings_available": has_embeddings,
        "implementation_sha256": _implementation_sha256(),
        "longevity_event_semantics": (
            "is_active=false; legacy end_date heuristic only when activity status is absent"
        ),
        **_git_provenance(),
    }
    without_embeddings = model_results["composite_success_score"]["without_embeddings"]
    promotion_decision = build_promotion_decision(
        without_embeddings,
        run_manifest,
        taxonomy_gate,
    )
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_manifest": run_manifest,
        "taxonomy_gate": taxonomy_gate,
        "promotion_decision": promotion_decision,
        "quality_report": {
            "columns": quality_report["columns"],
            "flagged_columns": [p["column"] for p in quality_report["flagged"]],
        },
        "split": {
            "n_train": len(train_rows),
            "n_test": len(test_rows),
            "n_dropped": dropped,
            **split_details,
        },
        "longevity_benchmarks": longevity_benchmarks(rows, ads, identify_scrape_dates(ads)),
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
