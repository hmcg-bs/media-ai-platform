"""Tests for ingestion/run_step2_pipeline.py — fully offline: fake stages,
injected fetch_fn, no network/GCP/Replicate calls."""

from __future__ import annotations

import json
import threading
import time
from http.client import IncompleteRead
from pathlib import Path

from ingestion.run_step2_pipeline import fetch_image_bytes, run_step2_pipeline
from pipeline.models.output_schema import PipelineContext
from pipeline.stages.base_stage import BaseStage


class _RecordingStage(BaseStage):
    """Records ad_id + image_bytes length into the result so tests can
    assert the fetch->stage-chain wiring actually happened."""

    name = "recording_stage"

    def process(self, context: PipelineContext) -> PipelineContext:
        assert context.image_bytes is not None, "image_bytes must be pre-supplied"
        context.result.ad_id = context.ad_id
        context.result.imagery_description = f"bytes_len={len(context.image_bytes)}"
        return context


def _fake_fetch_ok(url: str) -> bytes | None:
    return b"fake-image-bytes"


def _fake_fetch_fails(url: str) -> bytes | None:
    return None


class TestFetchImageBytesHandlesIncompleteRead:
    """Regression: Facebook's CDN closing a connection mid-read raises
    http.client.IncompleteRead, which is neither URLError/OSError/ValueError
    — confirmed live: it went uncaught, propagated out of a worker thread,
    and crashed a real ~3-hour run at future.result(). fetch_image_bytes
    must degrade gracefully (return None, log, never raise) like every other
    fetch failure mode already handled here."""

    def test_incomplete_read_returns_none_not_raise(self, monkeypatch) -> None:
        def _raise_incomplete_read(*args, **kwargs):
            raise IncompleteRead(b"partial", 500)

        monkeypatch.setattr(
            "ingestion.run_step2_pipeline.urlopen", _raise_incomplete_read
        )
        result = fetch_image_bytes("https://cdn.example.com/a.jpg")
        assert result is None


class TestRunStep2Pipeline:
    def test_processes_ad_and_writes_json(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "111", "image_urls": ["https://cdn.example.com/a.jpg"]},
        ]))
        out_dir = tmp_path / "out"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()], fetch_fn=_fake_fetch_ok
        )

        assert count == 1
        doc = json.loads((out_dir / "111.json").read_text())
        assert doc["ad_id"] == "111"
        assert doc["imagery_description"] == "bytes_len=16"

    def test_ad_with_no_images_is_skipped_not_dropped(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{"ad_archive_id": "222", "image_urls": []}]))
        out_dir = tmp_path / "out"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()], fetch_fn=_fake_fetch_ok
        )

        assert count == 0
        assert not (out_dir / "222.json").exists()

    def test_fetch_failure_does_not_write_output(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "333", "image_urls": ["https://cdn.example.com/dead.jpg"]},
        ]))
        out_dir = tmp_path / "out"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()], fetch_fn=_fake_fetch_fails
        )

        assert count == 0
        assert not (out_dir / "333.json").exists()

    def test_existing_output_is_skipped_resumability(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "444", "image_urls": ["https://cdn.example.com/a.jpg"]},
        ]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "444.json").write_text('{"ad_id": "444", "already": "here"}')

        calls: list[str] = []

        def tracking_fetch(url: str) -> bytes | None:
            calls.append(url)
            return b"bytes"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()], fetch_fn=tracking_fetch
        )

        assert count == 0
        assert calls == []  # never even attempted a fetch for an already-done ad
        # existing output untouched
        assert json.loads((out_dir / "444.json").read_text())["already"] == "here"

    def test_sample_size_limits_and_is_reproducible(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(20)
        ]))

        out_dir_a = tmp_path / "out_a"
        run_step2_pipeline(
            ads_file, out_dir_a, sample_size=5, seed=42,
            stages=[_RecordingStage()], fetch_fn=_fake_fetch_ok,
        )
        ids_a = sorted(p.stem for p in out_dir_a.glob("*.json"))

        out_dir_b = tmp_path / "out_b"
        run_step2_pipeline(
            ads_file, out_dir_b, sample_size=5, seed=42,
            stages=[_RecordingStage()], fetch_fn=_fake_fetch_ok,
        )
        ids_b = sorted(p.stem for p in out_dir_b.glob("*.json"))

        assert len(ids_a) == 5
        assert ids_a == ids_b  # same seed -> same sample


class TestConcurrency:
    """Regression: across-ad concurrency (confirmed live as the dominant
    lever for the ~30-hour projected full-corpus runtime at sequential
    speed) must process every ad correctly, preserve resumability, and never
    let concurrent writes collide — while genuinely running ads in parallel,
    not just accepting the concurrency param without using it."""

    def _slow_fetch(self, delay: float):
        def _fetch(url: str) -> bytes | None:
            time.sleep(delay)
            return b"fake-image-bytes"
        return _fetch

    def test_concurrency_processes_all_ads_correctly(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(10)
        ]))
        out_dir = tmp_path / "out"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()],
            fetch_fn=self._slow_fetch(0.01), concurrency=4,
        )

        assert count == 10
        written_ids = sorted(int(p.stem) for p in out_dir.glob("*.json"))
        assert written_ids == list(range(10))
        for i in range(10):
            doc = json.loads((out_dir / f"{i}.json").read_text())
            assert doc["ad_id"] == str(i)

    def test_concurrency_genuinely_overlaps_not_sequential(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        delay = 0.2
        n_ads = 4
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(n_ads)
        ]))
        out_dir = tmp_path / "out"

        start = time.monotonic()
        run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()],
            fetch_fn=self._slow_fetch(delay), concurrency=n_ads,
        )
        elapsed = time.monotonic() - start
        # Sequential would take ~n_ads*delay (0.8s); concurrent should be ~delay (0.2s).
        assert elapsed < delay * (n_ads / 1.5)

    def test_resumability_preserved_under_concurrency(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(6)
        ]))
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        for i in (0, 2, 4):
            (out_dir / f"{i}.json").write_text(json.dumps({"ad_id": str(i), "already": "here"}))

        fetched_urls: list[str] = []
        lock = threading.Lock()

        def tracking_fetch(url: str) -> bytes | None:
            with lock:
                fetched_urls.append(url)
            return b"bytes"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()],
            fetch_fn=tracking_fetch, concurrency=3,
        )

        assert count == 3  # only 1, 3, 5 — the ones not already done
        assert sorted(fetched_urls) == [
            "https://cdn.example.com/1.jpg",
            "https://cdn.example.com/3.jpg",
            "https://cdn.example.com/5.jpg",
        ]
        for i in (0, 2, 4):
            assert json.loads((out_dir / f"{i}.json").read_text())["already"] == "here"

    def test_no_write_collisions_across_concurrent_ads(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        n_ads = 15
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(n_ads)
        ]))
        out_dir = tmp_path / "out"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()],
            fetch_fn=self._slow_fetch(0.005), concurrency=6,
        )

        assert count == n_ads
        for i in range(n_ads):
            doc = json.loads((out_dir / f"{i}.json").read_text())
            # Each ad's own id landed in its own file — no cross-ad overwrite.
            assert doc["ad_id"] == str(i)


class TestOneAdFailureDoesNotCrashTheBatch:
    """Regression: an unexpected exception from one ad's processing (in
    practice, fetch_image_bytes' now-fixed IncompleteRead gap, but this
    guards the general case) used to propagate out of future.result() and
    abort every other still-queued ad — confirmed live: a real run lost
    everything queued after ~3 healthy hours over exactly this. Other ads
    must still complete; the failing one must be counted, not silently
    dropped or allowed to take the whole batch down with it."""

    def test_sequential_one_ad_raises_others_still_processed(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(5)
        ]))
        out_dir = tmp_path / "out"

        def flaky_fetch(url: str) -> bytes | None:
            if "2.jpg" in url:
                raise IncompleteRead(b"partial", 100)
            return b"fake-image-bytes"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()],
            fetch_fn=flaky_fetch, concurrency=1,
        )

        assert count == 4  # everything except ad "2"
        assert not (out_dir / "2.json").exists()
        for i in (0, 1, 3, 4):
            assert (out_dir / f"{i}.json").exists()

    def test_concurrent_one_ad_raises_others_still_processed(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": str(i), "image_urls": [f"https://cdn.example.com/{i}.jpg"]}
            for i in range(8)
        ]))
        out_dir = tmp_path / "out"

        def flaky_fetch(url: str) -> bytes | None:
            if "3.jpg" in url or "6.jpg" in url:
                raise RuntimeError("simulated freak per-ad failure")
            return b"fake-image-bytes"

        count = run_step2_pipeline(
            ads_file, out_dir, stages=[_RecordingStage()],
            fetch_fn=flaky_fetch, concurrency=4,
        )

        assert count == 6  # everything except ads "3" and "6"
        for i in (3, 6):
            assert not (out_dir / f"{i}.json").exists()
        for i in (0, 1, 2, 4, 5, 7):
            assert (out_dir / f"{i}.json").exists()
