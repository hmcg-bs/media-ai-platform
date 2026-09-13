from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.model_training import handoff_bundle
from pipeline.model_training.handoff_bundle import build_handoff_bundle, verify_handoff_bundle


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    guide = {
        "visual_directives": [
            {
                "dimension": "palette_vibrancy",
                "value": None,
                "direction": "lower_is_better",
                "magnitude": 0.1,
                "source": "shap:composite_success_score",
            }
        ],
        "copy_style_directives": [],
        "positioning_context": [],
        "non_directional_signals": [],
        "excluded_notes": [],
    }
    model = {
        "n_train": 8,
        "n_test": 2,
        "advertiser_overlap": 0,
        "test_mae": 0.2,
        "baseline_mae": 0.3,
        "split_strategy": "newest_first_seen_advertiser_holdout",
    }
    training = {
        "report_schema_version": "meta-ads-training-report-v2",
        "run_manifest": {
            "matrix_sha256": "a" * 64,
            "ads_sha256": "b" * 64,
            "implementation_sha256": "c" * 64,
            "git_commit": "d" * 40,
            "git_branch": "feature",
            "git_worktree_dirty": False,
            "longevity_event_semantics": (
                "is_active=false; legacy end_date heuristic only when activity status is absent"
            ),
        },
        "split": {
            "n_train": 8,
            "n_test": 2,
            "advertiser_overlap": 0,
            "strategy": "newest_first_seen_advertiser_holdout",
        },
        "model_results": {
            "composite_success_score": {
                "without_embeddings": {
                    **model,
                    "top_20pct_precision": 0.4,
                    "top_features_by_shap": [
                        {
                            "feature": "numeric__palette_vibrancy",
                            "mean_abs_shap": 0.1,
                            "mean_signed_shap": -0.1,
                        }
                    ],
                },
                "with_embeddings": {
                    **model,
                    "test_mae": 0.25,
                    "top_20pct_precision": 0.2,
                },
            },
            "days_active": {
                "cox_survival": {
                    "evaluation_status": "insufficient_observed_events",
                    "n_events_observed_train": 0,
                    "n_events_observed_test": 0,
                    "top_covariates": [],
                }
            },
        },
    }
    survival = {
        "report_schema_version": "survival-validation-v1",
        "input_hash": "e" * 64,
        "status": "insufficient_observed_endings",
        "events_observed": 2,
        "horizons": {str(day): {"status": "insufficient_evidence"} for day in (30, 60, 90, 180)},
    }

    def write(name: str, value: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return path

    return (
        write("guide.json", guide),
        write("training.json", training),
        write("survival.json", survival),
    )


def _accept_fixture_as_v3(monkeypatch: pytest.MonkeyPatch, files: tuple[Path, Path, Path]) -> None:
    monkeypatch.setattr(
        handoff_bundle,
        "ACCEPTED_V3_HASHES",
        {
            role: handoff_bundle._sha256(path)
            for role, path in zip(
                ("generation_guide", "training_report", "survival_validation"),
                files,
                strict=True,
            )
        },
    )


def test_existing_v3_bundle_is_atomic_immutable_and_verifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    guide, training, survival = _write_inputs(tmp_path)
    _accept_fixture_as_v3(monkeypatch, (guide, training, survival))
    output = tmp_path / "supplements-v3"
    manifest = build_handoff_bundle(
        guide,
        training,
        survival,
        output,
        "supplements-v3",
        freeze_existing_v3=True,
    )
    assert manifest["status"] == "frozen_existing_evidence"
    assert manifest["evidence_version"] == "supplements-v3"
    assert (
        manifest["files"]["survival_validation"]["path"]
        == "meta_model_handoff_supplements_survival_validation_v1.json"
    )
    assert verify_handoff_bundle(output) == manifest
    with pytest.raises(FileExistsError, match="immutable"):
        build_handoff_bundle(
            guide,
            training,
            survival,
            output,
            "supplements-v3",
            freeze_existing_v3=True,
        )


def test_bundle_detects_changed_content_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    guide, training, survival = _write_inputs(tmp_path)
    _accept_fixture_as_v3(monkeypatch, (guide, training, survival))
    output = tmp_path / "supplements-v3"
    build_handoff_bundle(
        guide,
        training,
        survival,
        output,
        "supplements-v3",
        freeze_existing_v3=True,
    )
    (output / "meta_model_handoff_supplements_generation_guide_v3.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_handoff_bundle(output)


def test_new_bundle_fails_closed_without_promoted_decision(tmp_path: Path):
    guide, training, survival = _write_inputs(tmp_path)
    with pytest.raises(ValueError, match="promoted decision"):
        build_handoff_bundle(guide, training, survival, tmp_path / "v4", "supplements-v4")


def test_existing_v3_rejects_better_embedding_variant(tmp_path: Path):
    guide, training, survival = _write_inputs(tmp_path)
    payload = json.loads(training.read_text())
    payload["model_results"]["composite_success_score"]["with_embeddings"]["test_mae"] = 0.1
    training.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="embeddings to be worse"):
        build_handoff_bundle(
            guide,
            training,
            survival,
            tmp_path / "supplements-v3",
            "supplements-v3",
            freeze_existing_v3=True,
        )


def test_guide_rejects_non_shap_directive(tmp_path: Path):
    guide, training, survival = _write_inputs(tmp_path)
    payload = json.loads(guide.read_text())
    payload["visual_directives"][0]["source"] = "cox:days_active"
    guide.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="only directional composite SHAP"):
        build_handoff_bundle(
            guide,
            training,
            survival,
            tmp_path / "supplements-v3",
            "supplements-v3",
            freeze_existing_v3=True,
        )
