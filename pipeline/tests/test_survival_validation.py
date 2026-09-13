from __future__ import annotations

from pipeline.model_training.validate_survival import build_survival_validation


def _observation(ad_id, day, status, run):
    return {
        "observation_id": f"{run}-{ad_id}",
        "run_id": run,
        "ad_id": ad_id,
        "observed_at": f"2026-01-{day:02d}T00:00:00Z",
        "observed_status": status,
        "poll_complete": True,
    }


def test_horizon_report_counts_confirmed_endings_and_censoring():
    ads = [
        {"ad_archive_id": "ended", "start_date": "2026-01-01"},
        {"ad_archive_id": "active", "start_date": "2026-01-01"},
    ]
    observations = [
        _observation("ended", 2, "active", "r1"),
        _observation("ended", 10, "not_observed", "r2"),
        _observation("ended", 11, "not_observed", "r3"),
        _observation("active", 2, "active", "r1"),
        _observation("active", 31, "active", "r2"),
    ]
    report = build_survival_validation(ads, observations, horizons=(7, 30))
    assert report["events_observed"] == 1
    assert report["interval_censored_events_approximated_at_first_missing"] == 1
    assert report["horizons"]["7"]["endings_observed_by_horizon"] == 0
    assert report["horizons"]["30"]["ads_observed_through_horizon"] == 1
    assert report["horizons"]["30"]["ads_eligible_for_outcome"] == 2


def test_censored_before_horizon_is_not_a_known_outcome():
    ads = [
        {"ad_archive_id": "early-end", "start_date": "2026-01-01"},
        {"ad_archive_id": "early-censor", "start_date": "2026-01-01"},
    ]
    observations = [
        _observation("early-end", 2, "active", "r1"),
        _observation("early-end", 10, "explicitly_inactive", "r2"),
        _observation("early-censor", 10, "active", "r1"),
    ]
    report = build_survival_validation(ads, observations, horizons=(30,))
    horizon = report["horizons"]["30"]
    assert horizon["ads_eligible_for_outcome"] == 1
    assert horizon["ads_observed_through_horizon"] == 0
    assert horizon["endings_observed_by_horizon"] == 1


def test_no_endings_is_explicitly_insufficient():
    ads = [{"ad_archive_id": "active", "start_date": "2026-01-01"}]
    observations = [_observation("active", 31, "active", "r1")]
    report = build_survival_validation(ads, observations, horizons=(30,))
    assert report["status"] == "insufficient_observed_endings"
    assert report["horizons"]["30"]["status"] == "insufficient_evidence"


def test_fewer_than_five_endings_remains_insufficient():
    ads = [
        {"ad_archive_id": f"ended-{i}", "start_date": "2026-01-01"}
        for i in range(4)
    ]
    observations = [
        _observation(f"ended-{i}", 10, "explicitly_inactive", f"r-{i}")
        for i in range(4)
    ]
    report = build_survival_validation(ads, observations, horizons=(30,))
    assert report["events_observed"] == 4
    assert report["status"] == "insufficient_observed_endings"
