"""Tests for pipeline/generation/guide.py -- confirms the filtering rules
(missing-data levels, embedding dims, non-visual dimensions dropped; direction
reliability threshold applied to SHAP sources; Cox coefficients always
directional) against synthetic report fixtures shaped like the real
model_training_report.json / success_score_report.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.generation.guide import extract_generation_guide


@pytest.fixture
def reports(tmp_path: Path) -> tuple[Path, Path]:
    training_report = {
        "model_results": {
            "days_active": {
                "cox_survival": {
                    "top_covariates": [
                        ["categorical__cta_type_shop_now", 0.05],
                        ["categorical__dominant_color_unknown", 0.09],  # missing-data level
                        ["categorical__background_style_Studio", -0.03],
                        # non-visual (campaign-ops dimension)
                        ["categorical__utm_medium_category_dedicated_paid_social", 0.04],
                    ]
                }
            },
            "collation_count": {
                "without_embeddings": {
                    "top_features": [
                        ["numeric__creative_cta_present", 0.05],
                        ["categorical__dominant_color_white", 0.04],
                    ]
                }
            },
            "variants_featured_count": {
                "without_embeddings": {"top_features": []}
            },
        }
    }
    success_score_report = {
        "without_embeddings": {
            "top_features_by_shap": [
                # reliable direction: |signed|/|abs| = 0.8
                {
                    "feature": "numeric__creative_uppercase_ratio",
                    "mean_abs_shap": 0.05, "mean_signed_shap": 0.04,
                },
                # unreliable direction: ratio = 0.02 -- should become non-directional only
                {"feature": "numeric__rating", "mean_abs_shap": 0.05, "mean_signed_shap": 0.001},
                # raw embedding dim -- must be dropped entirely (unparseable/unbucketed)
                {
                    "feature": "numeric__body_embedding_12",
                    "mean_abs_shap": 0.09, "mean_signed_shap": 0.08,
                },
                # positioning context, opposite direction from the Cox entry below
                {
                    "feature": "categorical__price_tier_budget",
                    "mean_abs_shap": 0.02, "mean_signed_shap": -0.018,
                },
            ]
        }
    }
    training_report["model_results"]["days_active"]["cox_survival"]["top_covariates"].append(
        ["categorical__price_tier_budget", 0.08]
    )

    tr_path = tmp_path / "training_report.json"
    sr_path = tmp_path / "success_score_report.json"
    tr_path.write_text(json.dumps(training_report))
    sr_path.write_text(json.dumps(success_score_report))
    return tr_path, sr_path


class TestExtractGenerationGuide:
    def test_missing_data_levels_dropped(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        all_dims = [(s.dimension, s.value) for s in guide.visual_directives]
        assert ("dominant_color", "unknown") not in all_dims

    def test_non_visual_campaign_dimension_dropped(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        all_dims = [s.dimension for s in guide.visual_directives + guide.copy_style_directives]
        assert "utm_medium_category" not in all_dims

    def test_cox_coefficients_are_always_directional(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        cta = [
            s for s in guide.visual_directives
            if s.dimension == "cta_type" and s.value == "shop_now"
        ]
        assert len(cta) == 1
        assert cta[0].direction == "higher_is_better"
        assert cta[0].source == "cox:days_active"

    def test_low_reliability_shap_feature_excluded_from_directives(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        directive_dims = [s.dimension for s in guide.copy_style_directives]
        assert "rating" not in directive_dims
        assert any("rating" in n for n in guide.non_directional_signals)

    def test_reliable_shap_feature_kept_directional(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        matches = [s for s in guide.copy_style_directives if s.dimension == "uppercase_ratio"]
        assert len(matches) == 1
        assert matches[0].direction == "higher_is_better"

    def test_raw_embedding_dimension_never_surfaces_anywhere(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        haystack = (
            [s.dimension for s in guide.visual_directives + guide.copy_style_directives]
            + guide.non_directional_signals
        )
        assert not any("embedding" in str(h) for h in haystack)

    def test_conflicting_direction_across_sources_both_preserved(self, reports):
        """price_tier=budget is higher_is_better per Cox, lower_is_better per
        SHAP -- both real findings, and the guide must not silently pick one
        or merge them into a false consensus."""
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        budget_signals = [s for s in guide.positioning_context if s.value == "budget"]
        directions = {s.direction for s in budget_signals}
        assert directions == {"higher_is_better", "lower_is_better"}

    def test_xgboost_features_recorded_as_non_directional_only(self, reports):
        tr, sr = reports
        guide = extract_generation_guide(tr, sr)
        directive_dims = [s.dimension for s in guide.visual_directives]
        assert "cta_present" not in directive_dims  # only in collation_count's xgboost importances
        assert any("cta_present" in n and "xgboost" in n for n in guide.non_directional_signals)
