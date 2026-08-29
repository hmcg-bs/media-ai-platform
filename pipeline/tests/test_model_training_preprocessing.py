"""Tests for pipeline/model_training/preprocessing.py -- offline, synthetic
rows, no real feature matrix or network calls needed."""

from __future__ import annotations

import numpy as np

from pipeline.model_training.preprocessing import (
    build_preprocessor,
    build_xy,
    load_start_dates,
    time_based_split,
)


def _row(ad_id: str, days_active=10, collation_count=1, variants_featured_count=0, **extra):
    base = {
        "ad_id": ad_id,
        "days_active": days_active,
        "collation_count": collation_count,
        "variants_featured_count": variants_featured_count,
        "price_tier": "mid",
        "body_length": 100,
        "dominant_color": "red",
        "has_cta_text": True,
        "title_embedding": [0.1, 0.2, 0.3],
        "body_embedding": [0.4, 0.5, 0.6],
        "usp_embedding": [],
    }
    base.update(extra)
    return base


class TestLoadStartDates:
    def test_maps_ad_archive_id_to_start_date(self):
        ads = [{"ad_archive_id": "1", "start_date": "2026-01-01"}, {"ad_archive_id": "2"}]
        result = load_start_dates(ads)
        assert result == {"1": "2026-01-01"}


class TestTimeBasedSplit:
    def test_splits_oldest_into_train_newest_into_test(self):
        rows = [_row(str(i)) for i in range(10)]
        start_dates = {str(i): f"2026-01-{i + 1:02d}" for i in range(10)}
        train, test = time_based_split(rows, start_dates, train_fraction=0.8)
        assert len(train) == 8
        assert len(test) == 2
        assert [r["ad_id"] for r in train] == [str(i) for i in range(8)]
        assert [r["ad_id"] for r in test] == ["8", "9"]

    def test_rows_without_start_date_are_dropped(self):
        rows = [_row("1"), _row("2")]
        start_dates = {"1": "2026-01-01"}  # "2" has no known start_date
        train, test = time_based_split(rows, start_dates, train_fraction=0.8)
        assert len(train) + len(test) == 1

    def test_not_ordered_by_days_active(self):
        # Regression: days_active is itself a target variable -- splitting
        # by it would leak target-adjacent ordering into train/test.
        rows = [_row("1", days_active=999), _row("2", days_active=1)]
        start_dates = {"1": "2026-01-01", "2": "2026-06-01"}  # "1" is older
        train, _test = time_based_split(rows, start_dates, train_fraction=0.5)
        assert train[0]["ad_id"] == "1"  # ordered by start_date, not days_active


class TestBuildXy:
    def test_target_column_excluded_from_x(self):
        rows = [_row(str(i), days_active=i) for i in range(5)]
        X, y = build_xy(rows, "days_active")
        assert "days_active" not in X.columns
        assert list(y) == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_other_target_columns_also_excluded_from_x(self):
        rows = [_row(str(i)) for i in range(5)]
        X, _y = build_xy(rows, "days_active")
        assert "collation_count" not in X.columns
        assert "variants_featured_count" not in X.columns

    def test_ad_id_excluded_from_x(self):
        rows = [_row(str(i)) for i in range(5)]
        X, _y = build_xy(rows, "days_active")
        assert "ad_id" not in X.columns

    def test_price_tier_kept_as_categorical_feature(self):
        rows = [_row(str(i)) for i in range(5)]
        X, _y = build_xy(rows, "days_active")
        assert "price_tier" in X.columns

    def test_rows_with_null_target_are_dropped(self):
        rows = [_row("1", days_active=5), _row("2", days_active=None)]
        X, y = build_xy(rows, "days_active")
        assert len(y) == 1
        assert len(X) == 1

    def test_invalid_target_raises(self):
        import pytest

        with pytest.raises(ValueError):
            build_xy([_row("1")], "not_a_real_target")

    def test_embeddings_excluded_when_include_embeddings_false(self):
        rows = [_row(str(i)) for i in range(5)]
        X, _y = build_xy(rows, "days_active", include_embeddings=False)
        assert not any(c.startswith("title_embedding") for c in X.columns)
        assert "title_embedding" not in X.columns

    def test_embeddings_expanded_into_numeric_columns_by_default(self):
        rows = [_row(str(i)) for i in range(5)]
        X, _y = build_xy(rows, "days_active", include_embeddings=True)
        assert "title_embedding" not in X.columns  # replaced, not kept as a list column
        assert "title_embedding_0" in X.columns
        assert "title_embedding_1" in X.columns
        assert "title_embedding_2" in X.columns

    def test_all_empty_embedding_column_dropped_not_zero_filled(self):
        # usp_embedding is [] in every _row() fixture -- with no row to infer
        # a real dimensionality from, the column is dropped entirely rather
        # than silently becoming a real-looking [0.0, 0.0, ...] vector (which
        # would claim "usp text embeds to exactly zero", not "no usp text at
        # all, dimensionality unknown").
        rows = [_row(str(i)) for i in range(3)]
        X, _y = build_xy(rows, "days_active", include_embeddings=True)
        usp_cols = [c for c in X.columns if c.startswith("usp_embedding")]
        assert usp_cols == []

    def test_partially_empty_embedding_becomes_nan_not_zero(self):
        # A mix of real and empty usp_embedding vectors: dimensionality is
        # inferred from the real one, and the empty row becomes NaN (to be
        # median-imputed downstream) rather than a fabricated zero vector.
        rows = [
            _row("1", usp_embedding=[0.1, 0.2]),
            _row("2", usp_embedding=[]),
        ]
        X, _y = build_xy(rows, "days_active", include_embeddings=True)
        assert "usp_embedding_0" in X.columns
        assert X.loc[1, "usp_embedding_0"] != X.loc[1, "usp_embedding_0"]  # NaN != NaN


class TestBuildPreprocessor:
    def test_fits_and_transforms_without_error(self):
        rows = [_row(str(i), body_length=i * 10, dominant_color=["red", "blue"][i % 2])
                for i in range(20)]
        X, y = build_xy(rows, "days_active", include_embeddings=False)
        preprocessor = build_preprocessor(X)
        X_transformed = preprocessor.fit_transform(X)
        assert X_transformed.shape[0] == len(X)
        assert not np.isnan(X_transformed).any()

    def test_numeric_nulls_are_imputed_not_dropped(self):
        rows = [_row(str(i), body_length=(None if i == 0 else i * 10)) for i in range(10)]
        X, _y = build_xy(rows, "days_active", include_embeddings=False)
        preprocessor = build_preprocessor(X)
        X_transformed = preprocessor.fit_transform(X)
        assert X_transformed.shape[0] == 10
        assert not np.isnan(X_transformed).any()

    def test_categorical_unseen_value_at_transform_time_does_not_raise(self):
        train_rows = [_row(str(i), dominant_color="red") for i in range(10)]
        test_rows = [_row("99", dominant_color="never_seen_before")]
        X_train, _ = build_xy(train_rows, "days_active", include_embeddings=False)
        X_test, _ = build_xy(test_rows, "days_active", include_embeddings=False)
        preprocessor = build_preprocessor(X_train)
        preprocessor.fit(X_train)
        result = preprocessor.transform(X_test)  # must not raise
        assert result.shape[0] == 1
