"""Tests for ingestion/merge_step2_features.py (fully offline — writes real
ExtractionResult JSON fixtures to a tmp_path, no network/GCP/Replicate)."""

from __future__ import annotations

import json
from pathlib import Path

from ingestion.merge_step2_features import load_step2_results, merge_step2_into_corpus
from pipeline.models.output_schema import ExtractionResult, HookFramework


def _write_extraction_result(
    out_dir: Path,
    ad_id: str,
    hook_framework: HookFramework = HookFramework.PAS,
    copy_block_count: int = 2,
) -> None:
    result = ExtractionResult(ad_id=ad_id)
    result.marketing_psychology.hook_framework = hook_framework
    result.copywriting_features.copy_block_count = copy_block_count
    result.color_profile.background_hex = "#CFEDDB"
    result.color_profile.dominant_hex_palette = ["#CFEDDB", "#D8F89E"]
    result.color_profile.contrast_ratio_type = "High"
    result.color_profile.background_style = "Studio"
    (out_dir / f"{ad_id}.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2))


class TestLoadStep2Results:
    def test_loads_flattened_features_plus_color_profile(self, tmp_path: Path) -> None:
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_extraction_result(step2_dir, "ad_1")

        results = load_step2_results(step2_dir)

        assert "ad_1" in results
        assert results["ad_1"]["hook_framework"] == "PAS"
        assert results["ad_1"]["copy_block_count"] == 2
        assert results["ad_1"]["background_hex"] == "#CFEDDB"
        assert results["ad_1"]["dominant_hex_palette"] == ["#CFEDDB", "#D8F89E"]
        assert results["ad_1"]["contrast_ratio_type"] == "High"
        assert results["ad_1"]["background_style"] == "Studio"

    def test_malformed_json_skipped_gracefully(self, tmp_path: Path) -> None:
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        (step2_dir / "broken.json").write_text("{not valid json")
        _write_extraction_result(step2_dir, "ad_ok")

        results = load_step2_results(step2_dir)

        assert "ad_ok" in results
        assert len(results) == 1

    def test_empty_dir_returns_empty_dict(self, tmp_path: Path) -> None:
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        assert load_step2_results(step2_dir) == {}


class TestMergeStep2IntoCorpus:
    def test_matching_ad_gets_creative_features(self, tmp_path: Path) -> None:
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_extraction_result(step2_dir, "111")

        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{"ad_archive_id": "111", "title": "Test"}]))

        out_file = tmp_path / "merged.json"
        total, matched = merge_step2_into_corpus(ads_file, step2_dir, out_file)

        assert total == 1
        assert matched == 1
        result = json.loads(out_file.read_text())
        assert result[0]["creative_features"]["hook_framework"] == "PAS"
        assert result[0]["title"] == "Test"  # existing fields preserved

    def test_non_matching_ad_has_no_creative_features_key(self, tmp_path: Path) -> None:
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_extraction_result(step2_dir, "999")

        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{"ad_archive_id": "111", "title": "Test"}]))

        out_file = tmp_path / "merged.json"
        total, matched = merge_step2_into_corpus(ads_file, step2_dir, out_file)

        assert total == 1
        assert matched == 0
        result = json.loads(out_file.read_text())
        assert "creative_features" not in result[0]

    def test_same_ad_count_and_order_preserved(self, tmp_path: Path) -> None:
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_extraction_result(step2_dir, "2")

        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([
            {"ad_archive_id": "1"},
            {"ad_archive_id": "2"},
            {"ad_archive_id": "3"},
        ]))

        out_file = tmp_path / "merged.json"
        merge_step2_into_corpus(ads_file, step2_dir, out_file)

        result = json.loads(out_file.read_text())
        assert [ad["ad_archive_id"] for ad in result] == ["1", "2", "3"]
