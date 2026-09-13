"""Evaluate a frozen old proxy model on a later untouched collection window."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.model_training.evaluation import temporal_advertiser_split
from pipeline.model_training.model_evidence import build_promotion_decision
from pipeline.model_training.observation_windows import verify_observation_window
from pipeline.model_training.preprocessing import load_start_dates
from pipeline.model_training.run_training import _taxonomy_gate
from pipeline.model_training.success_score import fit_and_score_future_window

REPORT_SCHEMA_VERSION = "meta-future-window-evaluation-v1"
MINIMUM_FOLLOWUP_DAYS = 30


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _target_age_days(ad: dict[str, Any]) -> int | None:
    """Age represented by this immutable ad snapshot's proxy target."""
    if not ad.get("start_date") or not ad.get("ingested_at"):
        return None
    try:
        start = date.fromisoformat(str(ad["start_date"])[:10])
        observed = _parse_time(str(ad["ingested_at"])).date()
        if ad.get("is_active") is False and ad.get("end_date"):
            observed = date.fromisoformat(str(ad["end_date"])[:10])
    except ValueError:
        return None
    return max(0, (observed - start).days)


def evaluate_future_window(
    baseline_report_file: Path,
    baseline_matrix_file: Path,
    baseline_ads_file: Path,
    baseline_window_file: Path,
    future_matrix_file: Path,
    future_ads_file: Path,
    future_window_file: Path,
    evaluation_as_of: str,
    minimum_followup_days: int = MINIMUM_FOLLOWUP_DAYS,
    n_jobs: int = 1,
    baseline_observations_file: Path | None = None,
    future_observations_file: Path | None = None,
) -> dict[str, Any]:
    """Rebuild the frozen model and score only new, mature future-window ads."""
    baseline_report = json.loads(baseline_report_file.read_text())
    baseline_window = json.loads(baseline_window_file.read_text())
    future_window = json.loads(future_window_file.read_text())
    baseline_rows = json.loads(baseline_matrix_file.read_text())
    baseline_ads = json.loads(baseline_ads_file.read_text())
    future_rows = json.loads(future_matrix_file.read_text())
    future_ads = json.loads(future_ads_file.read_text())
    failures: list[str] = []

    manifest = baseline_report.get("run_manifest", {})
    if manifest.get("matrix_sha256") != _sha256(baseline_matrix_file):
        failures.append("baseline matrix hash does not match the frozen training report")
    if manifest.get("ads_sha256") != _sha256(baseline_ads_file):
        failures.append("baseline ads hash does not match the frozen training report")
    for name, window, ads_file, observations_file, previous_window in (
        ("baseline", baseline_window, baseline_ads_file, baseline_observations_file, None),
        (
            "future",
            future_window,
            future_ads_file,
            future_observations_file,
            baseline_window,
        ),
    ):
        try:
            verify_observation_window(window, ads_file, observations_file, previous_window)
        except ValueError as exc:
            failures.append(f"{name} {exc}")
    if not future_window.get("eligible_for_old_model_evaluation"):
        failures.append("future window is not proven later with new ads")
    if future_window.get("previous_window_id") != baseline_window.get("window_id"):
        failures.append("future window is not chained to the supplied baseline window")

    observed_at_max = future_window.get("observed_at_max")
    if not observed_at_max or _parse_time(evaluation_as_of) < _parse_time(observed_at_max):
        failures.append("evaluation_as_of precedes or cannot verify the future snapshot")

    baseline_ids = {str(row.get("ad_id") or "") for row in baseline_rows}
    future_ad_ids = set(future_window.get("ad_ids", [])) - set(baseline_window.get("ad_ids", []))
    future_ad_by_id = {str(ad.get("ad_archive_id") or ""): ad for ad in future_ads}
    mature_ids = {
        ad_id
        for ad_id in future_ad_ids
        if (
            future_ad_by_id.get(ad_id, {}).get("is_active") is False
            or (_target_age_days(future_ad_by_id.get(ad_id, {})) or -1) >= minimum_followup_days
        )
    }
    evaluation_rows = [row for row in future_rows if str(row.get("ad_id") or "") in mature_ids]
    if not evaluation_rows:
        failures.append("future matrix contains no new-window rows with a mature snapshot target")
    target_age_failures = []
    for row in evaluation_rows:
        ad_id = str(row.get("ad_id") or "")
        age = _target_age_days(future_ad_by_id.get(ad_id, {}))
        target = row.get("days_active")
        if age is None or target is None or abs(float(target) - age) > 1:
            target_age_failures.append(ad_id)
    if target_age_failures:
        failures.append(
            f"{len(target_age_failures)} future target rows are stale or not bound to snapshot age"
        )
    if baseline_ids & {str(row.get("ad_id") or "") for row in evaluation_rows}:
        failures.append("future evaluation contains baseline ad IDs")

    baseline_start_dates = load_start_dates(baseline_ads)
    training_rows, _old_test_rows, split = temporal_advertiser_split(
        baseline_rows, baseline_start_dates
    )
    expected_split = baseline_report.get("split", {})
    if (
        len(training_rows) != expected_split.get("n_train")
        or len(_old_test_rows) != expected_split.get("n_test")
        or split.get("strategy") != expected_split.get("strategy")
        or split.get("advertiser_overlap") != expected_split.get("advertiser_overlap")
    ):
        failures.append("baseline split cannot be reproduced from bound inputs")
    if baseline_report.get("taxonomy_gate", {}).get("status") != "passed":
        failures.append("baseline training report did not pass human taxonomy adjudication")
    taxonomy_gate = _taxonomy_gate(future_ads)
    if taxonomy_gate["status"] != "passed":
        failures.append("future ads have not passed human taxonomy adjudication")

    baseline_model = (
        baseline_report.get("model_results", {})
        .get("composite_success_score", {})
        .get("without_embeddings", {})
    )
    best_parameters = baseline_model.get("best_parameters")
    result = None
    promotion = {"status": "blocked", "failed_checks": ["preconditions"]}
    training_advertisers = {
        str(row.get("page_id") or f"__ad__{row.get('ad_id')}") for row in training_rows
    }
    future_advertisers = {
        str(row.get("page_id") or f"__ad__{row.get('ad_id')}") for row in evaluation_rows
    }
    if training_advertisers & future_advertisers:
        failures.append("future-window advertisers overlap baseline training advertisers")
    if not failures and best_parameters:
        result = fit_and_score_future_window(
            training_rows,
            evaluation_rows,
            best_parameters,
            random_state=int(manifest.get("random_state", 42)),
            n_jobs=n_jobs,
            future_start_dates=load_start_dates(future_ads),
        )
        promotion = build_promotion_decision(
            result,
            {"git_worktree_dirty": manifest.get("git_worktree_dirty")},
            taxonomy_gate,
            baseline_report.get("product_enrichment_gate", {"status": "failed"}),
        )
    elif not best_parameters:
        failures.append("baseline report has no frozen no-embedding parameters")

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "evaluated" if result is not None else "blocked",
        "evaluation_as_of": evaluation_as_of,
        "minimum_followup_days": minimum_followup_days,
        "target_maturity_policy": (
            "active target age is measured at immutable snapshot ingestion; "
            "explicitly inactive targets are complete"
        ),
        "n_new_window_ads": len(future_ad_ids),
        "n_mature_target_ads": len(mature_ids),
        "baseline_report_sha256": _sha256(baseline_report_file),
        "baseline_window_id": baseline_window.get("window_id"),
        "future_window_id": future_window.get("window_id"),
        "baseline_matrix_sha256": _sha256(baseline_matrix_file),
        "future_matrix_sha256": _sha256(future_matrix_file),
        "n_new_window_rows": len(evaluation_rows),
        "precondition_failures": failures,
        "model_result": result,
        "promotion_decision": promotion,
        "loop_isolation": "generated outputs are prohibited as Meta performance labels",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate old model on a frozen future window.")
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--baseline-matrix", type=Path, required=True)
    parser.add_argument("--baseline-ads", type=Path, required=True)
    parser.add_argument("--baseline-window", type=Path, required=True)
    parser.add_argument("--future-matrix", type=Path, required=True)
    parser.add_argument("--future-ads", type=Path, required=True)
    parser.add_argument("--future-window", type=Path, required=True)
    parser.add_argument("--baseline-observations", type=Path)
    parser.add_argument("--future-observations", type=Path)
    parser.add_argument("--evaluation-as-of", required=True)
    parser.add_argument("--minimum-followup-days", type=int, default=MINIMUM_FOLLOWUP_DAYS)
    parser.add_argument("--workers-per-model", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.minimum_followup_days < 1:
        parser.error("--minimum-followup-days must be positive")
    if args.workers_per_model < 1:
        parser.error("--workers-per-model must be positive")
    input_paths = (
        args.baseline_report,
        args.baseline_matrix,
        args.baseline_ads,
        args.baseline_window,
        args.future_matrix,
        args.future_ads,
        args.future_window,
        args.baseline_observations,
        args.future_observations,
    )
    for path in input_paths:
        if path is not None and not path.exists():
            parser.error(f"input not found: {path}")
    with exclusive_output(args.out):
        report = evaluate_future_window(
            args.baseline_report,
            args.baseline_matrix,
            args.baseline_ads,
            args.baseline_window,
            args.future_matrix,
            args.future_ads,
            args.future_window,
            args.evaluation_as_of,
            args.minimum_followup_days,
            args.workers_per_model,
            args.baseline_observations,
            args.future_observations,
        )
        atomic_write_json(args.out, report)
    print(f"Future-window evaluation {report['status']}: {args.out}")
    if report["status"] != "evaluated" or report["promotion_decision"]["status"] != "promoted":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
