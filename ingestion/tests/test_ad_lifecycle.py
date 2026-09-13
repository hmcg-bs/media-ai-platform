from __future__ import annotations

from ingestion.ad_lifecycle import (
    build_initial_observations,
    build_poll_observations,
    resolve_lifecycle,
)


def _ad(ad_id: str = "1") -> dict:
    return {
        "ad_archive_id": ad_id,
        "page_id": "page",
        "start_date": "2026-01-01",
        "is_active": True,
    }


def _raw(ad_id: str = "1", *, active: bool = True, end_date=None) -> dict:
    return {
        "ad_archive_id": ad_id,
        "page_id": "page",
        "start_date": "2026-01-01",
        "end_date": end_date,
        "is_active": active,
        "snapshot": {},
    }


def test_active_corpus_end_date_is_delivery_window_not_stop_evidence():
    ad = {**_ad(), "end_date": "2026-02-01", "ingested_at": "2026-02-01T00:00:00Z"}
    rows = build_initial_observations([ad], "initial", "2026-02-01T00:00:00Z")
    assert rows[0]["observed_status"] == "active"


def test_returned_active_ad_is_positive_observation():
    rows = build_poll_observations([_ad()], [_raw()], "run-1", "2026-02-01T00:00:00Z")
    assert rows[0]["observed_status"] == "active"
    assert rows[0]["poll_complete"] is True


def test_one_missing_poll_does_not_claim_inactivity():
    rows = build_poll_observations([_ad()], [], "run-1", "2026-02-01T00:00:00Z")
    state = resolve_lifecycle(rows, required_consecutive_misses=2)["1"]
    assert state["state"] == "unknown"
    assert state["consecutive_complete_misses"] == 1


def test_two_complete_misses_infer_interval_censored_stop():
    first = build_poll_observations([_ad()], [], "run-1", "2026-02-01T00:00:00Z")
    second = build_poll_observations([_ad()], [], "run-2", "2026-02-02T00:00:00Z")
    state = resolve_lifecycle(first + second, required_consecutive_misses=2)["1"]
    assert state["state"] == "inactive_inferred"
    assert state["event_at"] == "2026-02-01T00:00:00Z"


def test_incomplete_poll_neither_advances_nor_resets_misses():
    first = build_poll_observations([_ad()], [], "run-1", "2026-02-01T00:00:00Z")
    incomplete = build_poll_observations(
        [_ad()], [], "run-2", "2026-02-02T00:00:00Z", poll_complete=False
    )
    state = resolve_lifecycle(first + incomplete, required_consecutive_misses=2)["1"]
    assert state["state"] == "unknown"
    assert state["consecutive_complete_misses"] == 1


def test_missing_ad_is_unknown_when_its_page_has_no_positive_control():
    rows = build_poll_observations(
        [_ad()],
        [],
        "run-1",
        "2026-02-01T00:00:00Z",
        complete_page_ids=set(),
    )
    assert rows[0]["observed_status"] == "unknown"
    assert rows[0]["poll_complete"] is False


def test_explicit_stop_is_immediate_event():
    rows = build_poll_observations(
        [_ad()],
        [_raw(active=False, end_date="2026-01-20")],
        "run-1",
        "2026-02-01T00:00:00Z",
    )
    state = resolve_lifecycle(rows, required_consecutive_misses=2)["1"]
    assert state["state"] == "inactive_explicit"
    assert state["reported_stop_date"] == "2026-01-20"


def test_active_poll_end_date_alone_is_not_explicit_stop():
    rows = build_poll_observations(
        [_ad()],
        [_raw(active=True, end_date="2026-02-01")],
        "run-1",
        "2026-02-01T00:00:00Z",
    )
    assert rows[0]["observed_status"] == "active"
