"""Tests for pipeline/model_training/validation_report.py -- offline,
synthetic feature matrix + ads + training report, no real files needed."""

from __future__ import annotations

import json

from pipeline.model_training.validation_report import (
    _find_model_importance,
    build_validation_report,
    render_markdown,
    run,
)


def _row(ad_id: str, **extra):
    base = {
        "ad_id": ad_id,
        "days_active": 30,
        "collation_count": 1,
        "variants_featured_count": 1,
        "price_tier": "mid",
        "body_length": 100,
        "dominant_color": "red",
        "has_cta_text": True,
        "title_embedding": [], "body_embedding": [], "usp_embedding": [],
    }
    base.update(extra)
    return base


def _ad(ad_id: str, start_date="2026-01-01"):
    return {"ad_archive_id": ad_id, "start_date": start_date}


class TestFindModelImportance:
    def test_matches_numeric_prefixed_entry(self):
        training_report = {
            "model_results": {
                "collation_count": {
                    "without_embeddings": {"top_features": [["numeric__body_length", 0.05]]},
                },
            },
        }
        hits = _find_model_importance("body_length", training_report)
        assert len(hits) == 1
        assert hits[0]["target"] == "collation_count"
        assert hits[0]["importance"] == 0.05

    def test_matches_one_hot_expanded_categorical_entries(self):
        training_report = {
            "model_results": {
                "variants_featured_count": {
                    "without_embeddings": {
                        "top_features": [
                            ["categorical__price_tier_budget", 0.3],
                            ["categorical__price_tier_premium", 0.1],
                            ["categorical__dominant_color_red", 0.02],
                        ],
                    },
                },
            },
        }
        hits = _find_model_importance("price_tier", training_report)
        assert len(hits) == 2
        assert {h["transformed_name"] for h in hits} == {
            "categorical__price_tier_budget", "categorical__price_tier_premium",
        }

    def test_matches_cox_top_covariates_key(self):
        training_report = {
            "model_results": {
                "days_active": {
                    "cox_survival": {"top_covariates": [["numeric__body_length", 0.1]]},
                },
            },
        }
        hits = _find_model_importance("body_length", training_report)
        assert len(hits) == 1

    def test_no_match_returns_empty(self):
        training_report = {
            "model_results": {"collation_count": {"without_embeddings": {"top_features": []}}},
        }
        assert _find_model_importance("body_length", training_report) == []

    def test_does_not_false_positive_on_column_name_substring(self):
        # "body_length" must not match a covariate for a differently-named
        # column that merely shares a prefix, e.g. "body_length_extra".
        training_report = {
            "model_results": {
                "collation_count": {
                    "without_embeddings": {"top_features": [["numeric__body_length_extra", 0.1]]},
                },
            },
        }
        assert _find_model_importance("body_length", training_report) == []


class TestBuildValidationReport:
    def _write_fixtures(self, tmp_path):
        rows = [_row(str(i), days_active=i * 5, body_length=i * 10) for i in range(20)]
        matrix_file = tmp_path / "matrix.json"
        matrix_file.write_text(json.dumps(rows))

        ads = [_ad(str(i), start_date=f"2026-01-{(i % 28) + 1:02d}") for i in range(20)]
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))

        training_report_file = tmp_path / "training_report.json"
        training_report_file.write_text(json.dumps({
            "model_results": {
                "days_active": {"cox_survival": {
                    "top_covariates": [["numeric__body_length", 0.2]], "n_features": 5,
                }},
            },
        }))
        return matrix_file, ads_file, training_report_file

    def test_report_has_expected_top_level_structure(self, tmp_path):
        matrix_file, ads_file, training_report_file = self._write_fixtures(tmp_path)
        report = build_validation_report(matrix_file, ads_file, training_report_file)
        assert "summary" in report
        assert "features" in report
        assert report["summary"]["n_rows"] == 20

    def test_ad_id_excluded_from_features(self, tmp_path):
        matrix_file, ads_file, training_report_file = self._write_fixtures(tmp_path)
        report = build_validation_report(matrix_file, ads_file, training_report_file)
        assert "ad_id" not in report["features"]

    def test_feature_entry_includes_registry_description(self, tmp_path):
        matrix_file, ads_file, training_report_file = self._write_fixtures(tmp_path)
        report = build_validation_report(matrix_file, ads_file, training_report_file)
        assert report["features"]["body_length"]["description"]
        assert report["features"]["body_length"]["source"]

    def test_target_columns_do_not_get_self_correlation(self, tmp_path):
        matrix_file, ads_file, training_report_file = self._write_fixtures(tmp_path)
        report = build_validation_report(matrix_file, ads_file, training_report_file)
        assert "target_correlation" not in report["features"]["days_active"]

    def test_model_importance_wired_through(self, tmp_path):
        matrix_file, ads_file, training_report_file = self._write_fixtures(tmp_path)
        report = build_validation_report(matrix_file, ads_file, training_report_file)
        assert len(report["features"]["body_length"]["model_importance"]) == 1

    def test_missing_training_report_file_degrades_gracefully(self, tmp_path):
        matrix_file, ads_file, _ = self._write_fixtures(tmp_path)
        report = build_validation_report(matrix_file, ads_file, tmp_path / "does_not_exist.json")
        assert report["features"]["body_length"]["model_importance"] == []


class TestRenderMarkdown:
    def test_produces_nonempty_markdown_with_expected_sections(self, tmp_path):
        rows = [_row(str(i), days_active=i * 5, body_length=i * 10) for i in range(20)]
        matrix_file = tmp_path / "matrix.json"
        matrix_file.write_text(json.dumps(rows))
        ads = [_ad(str(i)) for i in range(20)]
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))

        report = build_validation_report(matrix_file, ads_file, tmp_path / "no_such_file.json")
        md = render_markdown(report)
        assert "# Feature/Data Validation Report" in md
        assert "## Executive Summary" in md
        assert "## Per-Feature Detail" in md
        assert "body_length" in md


class TestRun:
    def test_writes_both_output_files(self, tmp_path):
        rows = [_row(str(i), days_active=i * 5) for i in range(20)]
        matrix_file = tmp_path / "matrix.json"
        matrix_file.write_text(json.dumps(rows))
        ads = [_ad(str(i)) for i in range(20)]
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))

        md_out = tmp_path / "report.md"
        json_out = tmp_path / "report.json"
        run(
            matrix_file=matrix_file, ads_file=ads_file,
            training_report_file=tmp_path / "no_such_file.json",
            md_out=md_out, json_out=json_out,
        )
        assert md_out.exists()
        assert json_out.exists()
        assert json.loads(json_out.read_text())["summary"]["n_rows"] == 20
