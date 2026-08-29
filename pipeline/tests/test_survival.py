"""Tests for pipeline/model_training/survival.py -- offline, synthetic
rows/ads, no real feature matrix or network calls needed."""

from __future__ import annotations

import pandas as pd

from pipeline.model_training.survival import (
    build_survival_frame,
    build_survival_xy,
    drop_zero_variance_columns,
    evaluate_cox_model,
    fit_cox_model,
    identify_scrape_dates,
)


def _row(ad_id: str, days_active=10, **extra):
    base = {
        "ad_id": ad_id,
        "days_active": days_active,
        "collation_count": 1,
        "variants_featured_count": 0,
        "price_tier": "mid",
        "body_length": 100,
        "dominant_color": "red",
        "has_cta_text": True,
        "title_embedding": [],
        "body_embedding": [],
        "usp_embedding": [],
    }
    base.update(extra)
    return base


def _ad(ad_id: str, end_date: str | None):
    return {"ad_archive_id": ad_id, "end_date": end_date}


class TestIdentifyScrapeDates:
    def test_high_frequency_date_identified_as_scrape_stamp(self):
        # 9/10 ads share one end_date -- a real per-ad death date would
        # essentially never cluster this heavily by chance.
        ads = [_ad(str(i), "2026-08-08") for i in range(9)] + [_ad("9", "2024-01-01")]
        scrape_dates = identify_scrape_dates(ads, frequency_threshold=0.1)
        assert "2026-08-08" in scrape_dates
        assert "2024-01-01" not in scrape_dates

    def test_no_ads_returns_empty_set(self):
        assert identify_scrape_dates([]) == set()

    def test_ads_with_no_end_date_ignored(self):
        ads = [_ad("1", None), _ad("2", None)]
        assert identify_scrape_dates(ads) == set()

    def test_uniformly_distributed_dates_none_flagged(self):
        # Every ad has a distinct end_date -- nothing clusters, nothing
        # should be flagged as a scrape stamp.
        ads = [_ad(str(i), f"2026-01-{i + 1:02d}") for i in range(20)]
        assert identify_scrape_dates(ads, frequency_threshold=0.1) == set()


class TestBuildSurvivalFrame:
    def test_duration_equals_days_active(self):
        rows = [_row("1", days_active=42)]
        ads = [_ad("1", "2024-06-01")]
        frame = build_survival_frame(rows, ads, scrape_dates=set())
        assert frame.iloc[0]["duration"] == 42

    def test_event_observed_true_for_non_scrape_date(self):
        rows = [_row("1")]
        ads = [_ad("1", "2024-06-01")]
        frame = build_survival_frame(rows, ads, scrape_dates={"2026-08-08"})
        assert bool(frame.iloc[0]["event_observed"]) is True

    def test_event_observed_false_for_scrape_date(self):
        rows = [_row("1")]
        ads = [_ad("1", "2026-08-08")]
        frame = build_survival_frame(rows, ads, scrape_dates={"2026-08-08"})
        assert bool(frame.iloc[0]["event_observed"]) is False

    def test_row_with_no_matching_ad_dropped(self):
        rows = [_row("1"), _row("orphan")]
        ads = [_ad("1", "2024-06-01")]
        frame = build_survival_frame(rows, ads, scrape_dates=set())
        assert len(frame) == 1

    def test_row_with_no_end_date_dropped(self):
        rows = [_row("1")]
        ads = [_ad("1", None)]
        frame = build_survival_frame(rows, ads, scrape_dates=set())
        assert len(frame) == 0

    def test_scrape_dates_computed_automatically_when_not_given(self):
        # Default frequency_threshold is 1% -- needs a large enough sample
        # that the single distinct date's 1/200 share genuinely falls
        # below it (a tiny sample makes even one occurrence exceed 1%).
        rows = [_row(str(i)) for i in range(200)]
        ads = [_ad(str(i), "2026-08-08") for i in range(199)] + [_ad("199", "2024-01-01")]
        frame = build_survival_frame(rows, ads)
        observed = dict(zip(frame["ad_id"], frame["event_observed"], strict=True))
        assert bool(observed["199"]) is True  # the one distinct date -> real event
        assert bool(observed["0"]) is False  # the clustered scrape-stamp date -> censored


class TestBuildSurvivalXy:
    def test_aligned_output_lengths(self):
        rows = [_row(str(i), days_active=i * 10) for i in range(10)]
        ads = [_ad(str(i), "2024-06-01") for i in range(10)]
        X, duration, event_observed = build_survival_xy(rows, ads, scrape_dates=set())
        assert len(X) == len(duration) == len(event_observed) == 10

    def test_embeddings_excluded(self):
        rows = [_row(str(i), title_embedding=[0.1, 0.2]) for i in range(5)]
        ads = [_ad(str(i), "2024-06-01") for i in range(5)]
        X, _duration, _event = build_survival_xy(rows, ads, scrape_dates=set())
        assert not any(c.startswith("title_embedding") for c in X.columns)

    def test_days_active_not_in_x(self):
        rows = [_row(str(i)) for i in range(5)]
        ads = [_ad(str(i), "2024-06-01") for i in range(5)]
        X, _duration, _event = build_survival_xy(rows, ads, scrape_dates=set())
        assert "days_active" not in X.columns

    def test_rows_without_a_matching_ad_are_excluded(self):
        rows = [_row("1"), _row("orphan")]
        ads = [_ad("1", "2024-06-01")]
        X, duration, event = build_survival_xy(rows, ads, scrape_dates=set())
        assert len(X) == len(duration) == len(event) == 1


class TestCoxFitAndEvaluate:
    def _synthetic_survival_data(self, n=80):
        """A feature *weakly* correlated with duration (real signal, but
        noisy -- a near-perfect correlation triggers lifelines' "complete
        separation" convergence failure) plus real variance in every
        categorical/boolean column (an all-constant column is exactly
        collinear with Cox's implicit baseline hazard and also blocks
        convergence) and a mix of observed/censored events."""
        import random

        rng = random.Random(7)
        rows, ads = [], []
        for i in range(n):
            score = rng.random()
            noise = rng.gauss(0, 60)
            duration = max(1, int(20 + score * 100 + noise))
            observed = rng.random() < 0.6  # 60% observed, 40% censored
            rows.append(_row(
                str(i),
                days_active=duration,
                body_length=int(rng.random() * 1000),
                has_cta_text=rng.random() < 0.5,
                dominant_color=rng.choice(["red", "blue", "green"]),
                price_tier=rng.choice(["budget", "mid", "premium"]),
            ))
            ads.append(_ad(str(i), "2024-06-01" if observed else "2026-08-08"))
        return rows, ads

    def test_fit_and_evaluate_returns_expected_keys(self):
        rows, ads = self._synthetic_survival_data()
        train_rows, test_rows = rows[:60], rows[60:]
        scrape_dates = {"2026-08-08"}
        X_train, dur_train, ev_train = build_survival_xy(train_rows, ads, scrape_dates)
        X_test, dur_test, ev_test = build_survival_xy(test_rows, ads, scrape_dates)

        from pipeline.model_training.preprocessing import build_preprocessor

        preprocessor = build_preprocessor(X_train)

        X_train_proc = pd.DataFrame(
            preprocessor.fit_transform(X_train), columns=preprocessor.get_feature_names_out()
        )
        X_test_proc = pd.DataFrame(
            preprocessor.transform(X_test), columns=preprocessor.get_feature_names_out()
        )

        model = fit_cox_model(X_train_proc, dur_train, ev_train)
        results = evaluate_cox_model(model, X_test_proc, dur_test, ev_test)

        for key in ("n_test", "n_events_observed_test", "concordance_index", "top_covariates"):
            assert key in results
        assert 0.0 <= results["concordance_index"] <= 1.0
        assert results["n_test"] == len(X_test_proc)


class TestDropZeroVarianceColumns:
    def test_constant_column_dropped_from_both_frames(self):
        # Regression: a real full-corpus Cox fit hit ConvergenceError
        # ("singular matrix") -- traced to creative_* columns that are
        # ~97-100% one repeated value; after StandardScaler a genuinely-
        # constant column becomes a literal all-zero column, which is
        # exactly the singular-design-matrix case Cox can't invert.
        X_train = pd.DataFrame({"real": [1.0, 2.0, 3.0], "constant": [0.0, 0.0, 0.0]})
        X_test = pd.DataFrame({"real": [4.0, 5.0], "constant": [0.0, 0.0]})
        train_out, test_out, dropped = drop_zero_variance_columns(X_train, X_test)
        assert dropped == ["constant"]
        assert list(train_out.columns) == ["real"]
        assert list(test_out.columns) == ["real"]

    def test_no_zero_variance_columns_returns_unchanged(self):
        X_train = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
        X_test = pd.DataFrame({"a": [7.0], "b": [8.0]})
        train_out, test_out, dropped = drop_zero_variance_columns(X_train, X_test)
        assert dropped == []
        assert list(train_out.columns) == ["a", "b"]
        assert list(test_out.columns) == ["a", "b"]
