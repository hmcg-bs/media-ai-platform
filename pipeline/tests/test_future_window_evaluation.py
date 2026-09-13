from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pipeline.model_training.evaluate_future_window import evaluate_future_window
from pipeline.model_training.evaluation import temporal_advertiser_split
from pipeline.model_training.observation_windows import build_observation_window
from pipeline.model_training.preprocessing import load_start_dates


def _taxonomy() -> dict:
    return {
        "source": "human_adjudication",
        "is_supplement": True,
        "supplement_subcategory": "protein",
        "provenance": {
            "sample_sha256": "a" * 64,
            "review_schema_version": "supplements-taxonomy-review-v1",
            "adjudication_report_schema_version": "supplements-taxonomy-adjudication-report-v1",
            "reviewer_id": "reviewer-a",
            "reviewed_at": "2026-09-12T10:00:00Z",
            "approved_labels_content_sha256": "b" * 64,
        },
    }


def _inputs(tmp_path: Path) -> tuple[Path, ...]:
    baseline_ads = [
        {
            "ad_archive_id": str(i),
            "page_id": f"old-{i // 2}",
            "start_date": f"2026-01-{i % 28 + 1:02d}",
            "ingested_at": "2026-09-01T00:00:00Z",
            "taxonomy_label": _taxonomy(),
        }
        for i in range(40)
    ]
    baseline_matrix = [
        {
            "ad_id": str(i),
            "page_id": f"old-{i // 2}",
            "days_active": 30 + i,
            "brand_scaling_count": 2,
            "collation_count": i % 4,
            "variants_featured_count": i % 3,
            "price_tier": "mid",
            "product_subcategory": "protein",
            "body_length": 20 + i,
        }
        for i in range(40)
    ]
    future_ads = [
        {
            "ad_archive_id": str(100 + i),
            "page_id": f"new-{i}",
            "start_date": f"2026-08-{i % 28 + 1:02d}",
            "ingested_at": "2026-10-01T00:00:00Z",
            "is_active": True,
            "taxonomy_label": _taxonomy(),
        }
        for i in range(20)
    ]
    future_matrix = [
        {
            "ad_id": str(100 + i),
            "page_id": f"new-{i}",
            "days_active": 61 - (i % 28),
            "brand_scaling_count": 1,
            "collation_count": i % 2,
            "variants_featured_count": i % 3,
            "price_tier": "mid",
            "product_subcategory": "protein",
            "body_length": 40 + i,
        }
        for i in range(20)
    ]

    def write(name: str, value: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path

    baseline_ads_file = write("baseline-ads.json", baseline_ads)
    baseline_matrix_file = write("baseline-matrix.json", baseline_matrix)
    future_ads_file = write("future-ads.json", future_ads)
    future_matrix_file = write("future-matrix.json", future_matrix)
    baseline_window = build_observation_window(baseline_ads_file)
    future_window = build_observation_window(future_ads_file, previous_window=baseline_window)
    baseline_window_file = write("baseline-window.json", baseline_window)
    future_window_file = write("future-window.json", future_window)
    train, _test, split = temporal_advertiser_split(baseline_matrix, load_start_dates(baseline_ads))
    report = {
        "run_manifest": {
            "matrix_sha256": hashlib.sha256(baseline_matrix_file.read_bytes()).hexdigest(),
            "ads_sha256": hashlib.sha256(baseline_ads_file.read_bytes()).hexdigest(),
            "random_state": 42,
            "git_worktree_dirty": False,
        },
        "split": {
            "n_train": len(train),
            "n_test": len(_test),
            "strategy": split["strategy"],
            "advertiser_overlap": split["advertiser_overlap"],
        },
        "taxonomy_gate": {"status": "passed"},
        "model_results": {
            "composite_success_score": {
                "without_embeddings": {
                    "best_parameters": {
                        "n_estimators": 10,
                        "max_depth": 2,
                        "learning_rate": 0.1,
                        "min_child_weight": 1,
                    }
                }
            }
        },
    }
    baseline_report_file = write("baseline-report.json", report)
    return (
        baseline_report_file,
        baseline_matrix_file,
        baseline_ads_file,
        baseline_window_file,
        future_matrix_file,
        future_ads_file,
        future_window_file,
    )


def test_future_window_is_scored_without_refitting_on_future_rows(tmp_path: Path):
    report = evaluate_future_window(
        *_inputs(tmp_path),
        evaluation_as_of="2026-11-15T00:00:00Z",
        n_jobs=1,
    )
    assert report["status"] == "evaluated"
    assert report["n_new_window_rows"] == 20
    assert report["model_result"]["fit_policy"] == "frozen_parameters_old_training_rows_only"
    assert report["model_result"]["advertiser_overlap"] == 0
    assert report["loop_isolation"].startswith("generated outputs are prohibited")


def test_future_window_fails_closed_before_minimum_followup(tmp_path: Path):
    inputs = _inputs(tmp_path)
    future_ads_file = inputs[5]
    future_ads = json.loads(future_ads_file.read_text())
    for ad in future_ads:
        ad["start_date"] = "2026-09-20"
    future_ads_file.write_text(json.dumps(future_ads))
    # Rebuild the immutable manifest so hashes are valid; the snapshot target
    # itself is still too young and must not mature just because wall time passes.
    baseline_window = json.loads(inputs[3].read_text())
    inputs[6].write_text(
        json.dumps(build_observation_window(future_ads_file, previous_window=baseline_window))
    )
    report = evaluate_future_window(*inputs, evaluation_as_of="2026-11-15T00:00:00Z")
    assert report["status"] == "blocked"
    assert report["model_result"] is None
    assert any("mature snapshot target" in reason for reason in report["precondition_failures"])
