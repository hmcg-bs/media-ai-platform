"""Tests for pipeline/model_training/data_quality.py -- pure functions,
fully offline, no real feature matrix needed."""

from __future__ import annotations

from pipeline.model_training.data_quality import (
    assess_dimensionality,
    build_quality_report,
    classify_column,
    compute_missingness_bias,
    compute_target_correlation,
    flag_thin_categories,
    profile_column,
)


class TestClassifyColumn:
    def test_id_column(self):
        assert classify_column("ad_id", ["a", "b"]) == "id"

    def test_embedding_column(self):
        assert classify_column("title_embedding", [[0.1, 0.2], []]) == "embedding"

    def test_numeric_column(self):
        assert classify_column("days_active", [1, 2, None]) == "numeric"

    def test_boolean_column(self):
        assert classify_column("has_cta_text", [True, False, None]) == "boolean"

    def test_categorical_column(self):
        assert classify_column("dominant_color", ["red", "blue", None]) == "categorical"

    def test_all_null_column_defaults_to_categorical(self):
        assert classify_column("mystery", [None, None]) == "categorical"


class TestProfileColumnNumeric:
    def test_null_rate_computed(self):
        p = profile_column("x", [1, 2, None, None])
        assert p["null_rate"] == 0.5

    def test_high_null_rate_flagged(self):
        p = profile_column("x", [1] + [None] * 9)
        assert any("high_null_rate" in f for f in p["flags"])

    def test_zero_variance_flagged(self):
        p = profile_column("collation_count", [1, 1, 1, 1, 1, 1])
        assert any("zero_variance" in f for f in p["flags"])

    def test_high_point_mass_flagged_below_full_variance(self):
        # 8/10 identical, not perfectly constant -- should hit point_mass,
        # not zero_variance.
        values = [5] * 8 + [1, 9]
        p = profile_column("x", values)
        assert p["stdev"] > 0
        assert any("high_point_mass" in f for f in p["flags"])

    def test_outliers_flagged(self):
        values = [10, 11, 12, 9, 10, 11, 10, 9, 1000]  # one clear outlier
        p = profile_column("x", values)
        assert p["outlier_rate"] > 0
        assert any("high_outlier_rate" in f for f in p["flags"])

    def test_normal_distribution_not_flagged(self):
        values = [10, 12, 11, 13, 9, 14, 10, 11, 12, 13]
        p = profile_column("x", values)
        assert p["flags"] == []


class TestProfileColumnBoolean:
    def test_always_true_flagged(self):
        p = profile_column("x", [True, True, True])
        assert p["true_rate"] == 1.0
        assert any("zero_variance" in f for f in p["flags"])

    def test_mixed_boolean_not_flagged(self):
        p = profile_column("x", [True, False, True, False])
        assert p["flags"] == []


class TestProfileColumnCategorical:
    def test_high_point_mass_flagged(self):
        values = ["unknown"] * 8 + ["red", "blue"]
        p = profile_column("x", values)
        assert any("high_point_mass" in f for f in p["flags"])

    def test_high_cardinality_flagged(self):
        values = [f"val_{i}" for i in range(30)]  # all unique, > 20 uniques
        p = profile_column("x", values)
        assert any("high_cardinality" in f for f in p["flags"])

    def test_low_cardinality_not_flagged_for_cardinality(self):
        values = ["a", "b", "a", "b", "c"] * 4
        p = profile_column("x", values)
        assert not any("high_cardinality" in f for f in p["flags"])


class TestProfileColumnEmbedding:
    def test_empty_rate_computed(self):
        p = profile_column("title_embedding", [[0.1, 0.2], [], [0.3, 0.4]])
        assert p["empty_rate"] == round(1 / 3, 4)

    def test_inconsistent_dims_flagged(self):
        p = profile_column("title_embedding", [[0.1, 0.2], [0.1, 0.2, 0.3]])
        assert any("inconsistent_dims" in f for f in p["flags"])

    def test_consistent_dims_not_flagged_for_dims(self):
        p = profile_column("title_embedding", [[0.1, 0.2], [0.3, 0.4]])
        assert not any("inconsistent_dims" in f for f in p["flags"])


class TestBuildQualityReport:
    def test_empty_rows_returns_empty_report(self):
        report = build_quality_report([])
        assert report == {"columns": {}, "flagged": []}

    def test_flagged_sorted_by_null_rate_descending(self):
        rows = [
            {"a": 1, "b": None, "c": 1},
            {"a": 1, "b": None, "c": None},
            {"a": 1, "b": 2, "c": None},
        ]
        report = build_quality_report(rows)
        # "a" has 0% null and no variance issue at n=3 identical values ->
        # flagged for zero_variance, not null; "b" and "c" have real nulls.
        null_rates = [p["null_rate"] for p in report["flagged"]]
        assert null_rates == sorted(null_rates, reverse=True)

    def test_columns_covers_every_key_seen_across_rows(self):
        rows = [{"a": 1}, {"b": 2}]
        report = build_quality_report(rows)
        assert set(report["columns"]) == {"a", "b"}
        assert report["columns"]["a"]["null_rate"] == 0.5
        assert report["columns"]["b"]["null_rate"] == 0.5


class TestComputeTargetCorrelation:
    def test_perfectly_correlated_numeric_column(self):
        rows = [{"x": float(i), "y": float(i) * 2} for i in range(20)]
        result = compute_target_correlation(rows, "x", ("y",))
        assert result["y"]["method"] == "pearson"
        assert result["y"]["r"] == 1.0
        assert result["y"]["p"] < 0.05

    def test_negatively_correlated_numeric_column(self):
        rows = [{"x": float(i), "y": float(-i)} for i in range(20)]
        result = compute_target_correlation(rows, "x", ("y",))
        assert result["y"]["r"] == -1.0

    def test_categorical_with_real_group_differences_is_significant(self):
        rows = (
            [{"cat": "a", "y": 1.0} for _ in range(15)]
            + [{"cat": "b", "y": 100.0} for _ in range(15)]
        )
        result = compute_target_correlation(rows, "cat", ("y",))
        assert result["y"]["method"] == "anova"
        assert result["y"]["p"] < 0.05

    def test_insufficient_data_reported_honestly(self):
        rows = [{"x": 1.0, "y": 2.0}, {"x": 2.0, "y": 3.0}]
        result = compute_target_correlation(rows, "x", ("y",))
        assert result["y"]["method"] == "insufficient_data"

    def test_null_values_excluded_from_pairs(self):
        rows = [{"x": float(i), "y": float(i) * 2} for i in range(20)]
        rows += [{"x": None, "y": 5.0}] * 5  # should not count toward n
        result = compute_target_correlation(rows, "x", ("y",))
        assert result["y"]["n"] == 20


class TestComputeMissingnessBias:
    def test_detects_planted_missing_not_at_random(self):
        # Rows where `flag` is null skew heavily toward a high target value;
        # rows where it's present skew low -- a real MNAR pattern.
        rows = (
            [{"flag": None, "y": 100.0} for _ in range(20)]
            + [{"flag": "x", "y": 1.0} for _ in range(20)]
        )
        result = compute_missingness_bias(rows, "flag", ("y",))
        assert result["y"]["likely_mnar"] is True
        assert result["y"]["mean_when_null"] == 100.0
        assert result["y"]["mean_when_non_null"] == 1.0

    def test_no_bias_when_null_and_non_null_targets_are_similar(self):
        rows = (
            [{"flag": None, "y": 50.0} for _ in range(20)]
            + [{"flag": "x", "y": 50.0} for _ in range(20)]
        )
        result = compute_missingness_bias(rows, "flag", ("y",))
        assert result["y"]["likely_mnar"] is False

    def test_insufficient_data_reported_honestly(self):
        rows = [{"flag": None, "y": 1.0}, {"flag": "x", "y": 2.0}]
        result = compute_missingness_bias(rows, "flag", ("y",))
        assert result["y"]["method"] == "insufficient_data"


class TestFlagThinCategories:
    def test_thin_category_flagged(self):
        rows = [{"cat": "common"} for _ in range(50)] + [{"cat": "rare"} for _ in range(3)]
        thin = flag_thin_categories(rows, "cat", min_n=30)
        assert len(thin) == 1
        assert thin[0]["value"] == "rare"
        assert thin[0]["count"] == 3

    def test_no_thin_categories_when_all_well_represented(self):
        rows = [{"cat": "a"} for _ in range(50)] + [{"cat": "b"} for _ in range(50)]
        assert flag_thin_categories(rows, "cat", min_n=30) == []

    def test_null_values_not_counted_as_a_category(self):
        rows = [{"cat": None} for _ in range(50)]
        assert flag_thin_categories(rows, "cat", min_n=30) == []


class TestAssessDimensionality:
    def test_features_exceeding_rows_flagged(self):
        result = assess_dimensionality(2188, {"with_embeddings": 2400})
        flags = result["variants"]["with_embeddings"]["flags"]
        assert any("features_exceed_rows" in f for f in flags)

    def test_thin_ratio_flagged_below_10x(self):
        result = assess_dimensionality(500, {"without_embeddings": 90})
        flags = result["variants"]["without_embeddings"]["flags"]
        assert any("thin_ratio" in f for f in flags)

    def test_healthy_ratio_not_flagged(self):
        result = assess_dimensionality(10000, {"small_model": 20})
        assert result["variants"]["small_model"]["flags"] == []
