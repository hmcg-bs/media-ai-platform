from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.model_training.observation_windows import (
    build_observation_window,
    verify_observation_window,
    write_immutable_window,
)


def _write_ads(path: Path, first_id: int, observed_at: str) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "ad_archive_id": str(first_id + i),
                    "page_id": f"page-{first_id + i}",
                    "ingested_at": observed_at,
                }
                for i in range(2)
            ]
        )
    )


def test_future_window_must_be_proven_later_and_have_new_ads(tmp_path: Path):
    baseline_ads = tmp_path / "baseline.json"
    future_ads = tmp_path / "future.json"
    _write_ads(baseline_ads, 1, "2026-09-01T00:00:00Z")
    _write_ads(future_ads, 3, "2026-10-01T00:00:00Z")
    baseline = build_observation_window(baseline_ads)
    future = build_observation_window(future_ads, previous_window=baseline)

    assert baseline["role"] == "baseline"
    assert future["role"] == "future_holdout"
    assert future["temporal_order_status"] == "after_previous_window"
    assert future["n_new_ads"] == 2
    assert future["eligible_for_old_model_evaluation"] is True


def test_unordered_future_window_fails_closed(tmp_path: Path):
    baseline_ads = tmp_path / "baseline.json"
    future_ads = tmp_path / "future.json"
    _write_ads(baseline_ads, 1, "2026-09-01T00:00:00Z")
    _write_ads(future_ads, 3, "2026-08-01T00:00:00Z")
    baseline = build_observation_window(baseline_ads)
    future = build_observation_window(future_ads, previous_window=baseline)
    assert future["eligible_for_old_model_evaluation"] is False
    assert future["temporal_order_status"] == "not_proven_after_previous_window"


def test_window_rejects_duplicate_ad_ids(tmp_path: Path):
    ads = tmp_path / "ads.json"
    ads.write_text(json.dumps([{"ad_archive_id": "1"}, {"ad_archive_id": "1"}]))
    with pytest.raises(ValueError, match="duplicate"):
        build_observation_window(ads)


def test_frozen_window_is_idempotent_but_not_replaceable(tmp_path: Path):
    ads = tmp_path / "ads.json"
    out = tmp_path / "window.json"
    _write_ads(ads, 1, "2026-09-01T00:00:00Z")
    manifest = build_observation_window(ads)
    write_immutable_window(out, manifest)
    write_immutable_window(out, manifest)

    with pytest.raises(FileExistsError, match="immutable"):
        write_immutable_window(out, {**manifest, "n_ads": 999})


def test_window_verification_rejects_tampered_membership(tmp_path: Path):
    ads = tmp_path / "ads.json"
    _write_ads(ads, 1, "2026-09-01T00:00:00Z")
    manifest = build_observation_window(ads)
    verify_observation_window(manifest, ads)
    manifest["ad_ids"][0] = "changed"
    with pytest.raises(ValueError, match="membership"):
        verify_observation_window(manifest, ads)


def test_window_verification_rejects_tampered_eligibility(tmp_path: Path):
    baseline_ads = tmp_path / "baseline.json"
    future_ads = tmp_path / "future.json"
    _write_ads(baseline_ads, 1, "2026-09-01T00:00:00Z")
    _write_ads(future_ads, 3, "2026-10-01T00:00:00Z")
    baseline = build_observation_window(baseline_ads)
    future = build_observation_window(future_ads, previous_window=baseline)
    future["eligible_for_old_model_evaluation"] = False
    with pytest.raises(ValueError, match="derived field mismatch"):
        verify_observation_window(future, future_ads, previous_window=baseline)
