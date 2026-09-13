"""Pre-registered proxy-weight and SHAP-direction sensitivity analysis.

This module never selects production weights or emits generation guidance. It
measures how strongly the retrospective proxy conclusions depend on the
project's disclosed 70/20/10 heuristic while preserving the same advertiser-
temporal split and train-only calibration used by the primary model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.model_training.preprocessing import load_start_dates
from pipeline.model_training.run_training import _git_provenance, _taxonomy_gate
from pipeline.model_training.success_score import SuccessScoreCalibrator, train_and_explain
from pipeline.validation.product_enrichment import build_product_enrichment_report

REPORT_SCHEMA_VERSION = "proxy-sensitivity-report-v1"
WEIGHT_SCENARIOS: dict[str, dict[str, float]] = {
    "baseline_70_20_10": {
        "longevity": 0.70,
        "longevity_scaling_interaction": 0.20,
        "variant_boost": 0.10,
    },
    "longevity_minus_10_scaling_plus_10": {
        "longevity": 0.60,
        "longevity_scaling_interaction": 0.30,
        "variant_boost": 0.10,
    },
    "scaling_minus_10_longevity_plus_10": {
        "longevity": 0.80,
        "longevity_scaling_interaction": 0.10,
        "variant_boost": 0.10,
    },
    "longevity_minus_10_variant_plus_10": {
        "longevity": 0.60,
        "longevity_scaling_interaction": 0.20,
        "variant_boost": 0.20,
    },
    "without_longevity": {
        "longevity": 0.0,
        "longevity_scaling_interaction": 2 / 3,
        "variant_boost": 1 / 3,
    },
    "without_scaling_interaction": {
        "longevity": 7 / 8,
        "longevity_scaling_interaction": 0.0,
        "variant_boost": 1 / 8,
    },
    "without_variant_boost": {
        "longevity": 7 / 9,
        "longevity_scaling_interaction": 2 / 9,
        "variant_boost": 0.0,
    },
}


def _content_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ranking_comparison(baseline: np.ndarray, challenger: np.ndarray) -> dict[str, float]:
    baseline_rank = pd.Series(baseline).rank(method="average")
    challenger_rank = pd.Series(challenger).rank(method="average")
    correlation = baseline_rank.corr(challenger_rank, method="pearson")
    k = max(1, int(np.ceil(len(baseline) * 0.20)))
    baseline_top = set(np.argsort(baseline)[-k:])
    challenger_top = set(np.argsort(challenger)[-k:])
    return {
        "spearman_proxy_score_rank_correlation": round(float(correlation), 5),
        "top_20pct_jaccard": round(
            len(baseline_top & challenger_top) / len(baseline_top | challenger_top), 5
        ),
    }


def build_proxy_sensitivity_report(
    rows: list[dict[str, Any]],
    start_dates: dict[str, str],
    random_state: int = 42,
    n_jobs: int = 1,
    trainer: Callable[..., dict[str, Any]] = train_and_explain,
) -> dict[str, Any]:
    """Evaluate every fixed scenario without selecting among them."""
    if not rows:
        raise ValueError("rows must be non-empty")
    baseline_scores = np.array(
        [
            row["composite_success_score"]
            for row in SuccessScoreCalibrator(rows, WEIGHT_SCENARIOS["baseline_70_20_10"])
            .transform(rows)
        ]
    )
    scenarios: dict[str, Any] = {}
    for name, weights in WEIGHT_SCENARIOS.items():
        started = time.perf_counter()
        result = trainer(
            rows,
            include_embeddings=False,
            random_state=random_state,
            n_jobs=n_jobs,
            start_dates=start_dates,
            score_weights=weights,
        )
        scores = np.array(
            [
                row["composite_success_score"]
                for row in SuccessScoreCalibrator(rows, weights).transform(rows)
            ]
        )
        scenarios[name] = {
            "weights": weights,
            "proxy_ranking_vs_baseline": _ranking_comparison(baseline_scores, scores),
            "fit_duration_seconds": round(time.perf_counter() - started, 5),
            "n_train": result["n_train"],
            "n_test": result["n_test"],
            "n_test_advertisers": result["n_test_advertisers"],
            "advertiser_overlap": result["advertiser_overlap"],
            "test_mae": result["test_mae"],
            "baseline_mae": result["baseline_mae"],
            "top_20pct_precision": result["top_20pct_precision"],
            "uncertainty": result["uncertainty"],
            "top_features_by_shap": result["top_features_by_shap"],
        }

    feature_directions: dict[str, dict[str, int]] = {}
    for scenario_name, scenario in scenarios.items():
        for feature in scenario["top_features_by_shap"]:
            signed = float(feature["mean_signed_shap"])
            direction = 1 if signed > 0 else -1 if signed < 0 else 0
            feature_directions.setdefault(feature["feature"], {})[scenario_name] = direction
    stability = []
    for feature, directions in feature_directions.items():
        nonzero = [direction for direction in directions.values() if direction]
        stability.append(
            {
                "feature": feature,
                "scenarios_present": len(directions),
                "direction_consistent_when_present": len(set(nonzero)) <= 1,
                "stable_across_all_scenarios": (
                    len(directions) == len(WEIGHT_SCENARIOS) and len(set(nonzero)) == 1
                ),
                "directions": directions,
            }
        )
    stability.sort(key=lambda item: (-item["scenarios_present"], item["feature"]))
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "analysis_only_no_automatic_weight_selection",
        "evidence_boundary": (
            "This report tests sensitivity of a public-data proxy. It is not conversion, "
            "ROAS, causal, lifecycle-ending, or generation-quality evidence."
        ),
        "loop_isolation": (
            "Generated outputs and reviewer defect/craft labels are prohibited as Meta labels."
        ),
        "production_effect": "none; immutable v3 remains selected",
        "rows_content_sha256": _content_sha256(rows),
        "random_state": random_state,
        "weight_scenarios": WEIGHT_SCENARIOS,
        "scenarios": scenarios,
        "shap_direction_stability": stability,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run analysis-only proxy sensitivity scenarios.")
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--workers-per-model", type=int, default=1)
    args = parser.parse_args()
    if args.workers_per_model < 1:
        parser.error("--workers-per-model must be at least 1")
    rows = json.loads(args.matrix.read_text())
    ads = json.loads(args.ads.read_text())
    report = build_proxy_sensitivity_report(
        rows,
        load_start_dates(ads),
        random_state=args.random_state,
        n_jobs=args.workers_per_model,
    )
    report["input_manifest"] = {
        "matrix_path": str(args.matrix),
        "matrix_sha256": _file_sha256(args.matrix),
        "ads_path": str(args.ads),
        "ads_sha256": _file_sha256(args.ads),
        **_git_provenance(),
    }
    taxonomy_gate = _taxonomy_gate(ads)
    product_gate = build_product_enrichment_report(ads)
    report["input_eligibility"] = {
        "status": (
            "eligible"
            if taxonomy_gate["status"] == product_gate["status"] == "passed"
            else "blocked"
        ),
        "taxonomy_gate": taxonomy_gate,
        "product_enrichment_gate": product_gate,
        "interpretation": (
            "Sensitivity results from blocked inputs are diagnostic only and cannot support "
            "promotion or a new handoff."
        ),
    }
    with exclusive_output(args.out):
        atomic_write_json(args.out, report)
    print(f"Wrote analysis-only proxy sensitivity report: {args.out}")


if __name__ == "__main__":
    main()
