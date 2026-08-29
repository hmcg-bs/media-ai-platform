"""Tests for pipeline/feature_engineering/build_matrix.py — fully offline:
fake out/step2/ output + fake EmbeddingClient, no real API/network calls."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.clients.replicate_client import EmbeddingClient
from pipeline.feature_engineering.build_matrix import build_feature_matrix


def _fake_embedding_client() -> EmbeddingClient:
    def run(model, inputs):
        return [0.1, 0.2, 0.3]

    return EmbeddingClient(run=run)


def _write_step2_result(out_dir: Path, ad_id: str, hook_framework: str = "PAS") -> None:
    doc = {
        "ad_id": ad_id,
        "technical_metadata": {"aspect_ratio": "1:1"},
        "copywriting_features": {},
        "placement": {},
        "marketing_psychology": {"hook_framework": hook_framework},
        "color_profile": {"dominant_hex_palette": ["#FF0000"]},
    }
    (out_dir / f"{ad_id}.json").write_text(json.dumps(doc))


class TestBuildFeatureMatrix:
    def _setup(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads = [
            {
                "ad_archive_id": "1",
                "title": "Buy Now",
                "body": "Great supplement",
                "days_active": 30,
                "collation_count": 1,
                "publisher_platforms": ["facebook"],
                "product_page": {"price": 19.99},
            },
            {
                "ad_archive_id": "2",
                "title": "Try Today",
                "body": "Another great supplement",
                "days_active": 10,
                "collation_count": 2,
                "publisher_platforms": ["facebook", "instagram"],
                "product_page": {"price": 49.99},
            },
        ]
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))
        _write_step2_result(step2_dir, "1", hook_framework="PAS")
        _write_step2_result(step2_dir, "2", hook_framework="Direct Offer")
        return ads_file, step2_dir

    def test_returns_one_row_per_ad(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        rows, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
        )
        assert len(rows) == 2
        assert summary["row_count"] == 2
        assert {r["ad_id"] for r in rows} == {"1", "2"}

    def test_price_tier_present_but_not_a_feature_key(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        rows, _ = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
        )
        row1 = next(r for r in rows if r["ad_id"] == "1")
        assert row1["price_tier"] == "mid"  # $19.99 is in the $15-35 band
        assert "price" not in row1

    def test_creative_features_wired_in_from_step2(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        rows, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
        )
        row1 = next(r for r in rows if r["ad_id"] == "1")
        assert row1["creative_hook_framework"] == "PAS"
        assert summary["with_creative_features"] == 2

    def test_summary_reports_distributions(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        _, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
        )
        assert summary["price_tier_distribution"] == {"mid": 1, "premium": 1}
        assert summary["creative_hook_framework_distribution"] == {
            "PAS": 1, "Direct Offer": 1,
        }

    def test_sample_size_limits_and_is_reproducible(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads = []
        for i in range(10):
            ads.append({"ad_archive_id": str(i), "title": f"Ad {i}"})
            _write_step2_result(step2_dir, str(i))
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))

        rows_a, _ = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir, sample_size=3, seed=7,
            embedding_client=_fake_embedding_client(),
        )
        rows_b, _ = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir, sample_size=3, seed=7,
            embedding_client=_fake_embedding_client(),
        )
        assert len(rows_a) == 3
        assert [r["ad_id"] for r in rows_a] == [r["ad_id"] for r in rows_b]

    def test_ad_without_ad_archive_id_is_skipped(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{"title": "no id here"}]))

        rows, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
        )
        assert rows == []
        assert summary["row_count"] == 0

    def test_ad_without_step2_data_still_gets_a_row(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{"ad_archive_id": "no_step2", "title": "X"}]))

        rows, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
        )
        assert len(rows) == 1
        assert rows[0]["creative_hook_framework"] is None
        assert summary["with_creative_features"] == 0


class TestBuildFeatureMatrixResilience:
    """Regression: a real full-corpus run crashed with zero rows written
    after ~935 ads' worth of paid embedding calls, because one ad's
    unretryable network error propagated out of the per-ad loop and no
    progress had been persisted anywhere. build_feature_matrix must (a)
    never let one ad's extraction failure kill the whole run, and (b)
    support resuming from already-built rows without re-paying for them."""

    def _setup(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads = [
            {"ad_archive_id": "1", "title": "Buy Now", "product_page": {"price": 19.99}},
            {"ad_archive_id": "2", "title": "Try Today", "product_page": {"price": 49.99}},
            {"ad_archive_id": "3", "title": "Also Buy", "product_page": {"price": 9.99}},
        ]
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))
        for ad_id in ("1", "2", "3"):
            _write_step2_result(step2_dir, ad_id)
        return ads_file, step2_dir

    def test_one_ad_extraction_failure_does_not_kill_the_run(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)

        def flaky_run(model, inputs):
            if inputs.get("text") == "Try Today":
                raise ConnectionError("simulated unretryable network failure")
            return [0.1, 0.2, 0.3]

        rows, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=EmbeddingClient(run=flaky_run),
        )
        assert {r["ad_id"] for r in rows} == {"1", "3"}
        assert summary["row_count"] == 2
        assert summary["failed"] == 1

    def test_resumes_from_existing_rows_without_reprocessing_them(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        calls: list[str] = []

        def counting_run(model, inputs):
            calls.append(inputs.get("text"))
            return [0.1, 0.2, 0.3]

        existing_rows = [{"ad_id": "1", "price_tier": "mid", "creative_hook_framework": "PAS"}]
        rows, summary = build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=EmbeddingClient(run=counting_run),
            existing_rows=existing_rows,
        )
        assert {r["ad_id"] for r in rows} == {"1", "2", "3"}
        assert summary["row_count"] == 3
        assert "Buy Now" not in calls  # ad "1" was skipped, never re-embedded

    def test_checkpoint_path_gets_flushed_during_the_run(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        checkpoint = tmp_path / "checkpoint.json"

        build_feature_matrix(
            ads_file=ads_file, step2_out_dir=step2_dir,
            embedding_client=_fake_embedding_client(),
            checkpoint_path=checkpoint, checkpoint_every=1,
        )
        assert checkpoint.exists()
        checkpointed = json.loads(checkpoint.read_text())
        assert {r["ad_id"] for r in checkpointed} == {"1", "2", "3"}
