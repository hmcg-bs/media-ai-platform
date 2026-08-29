"""Tests for pipeline/validation/cognitive_validator.py — fully offline:
fake out/step2/ output written to tmp_path, no real API/network calls."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.validation.cognitive_validator import (
    HOOK_FRAMEWORK_CATEGORIES,
    build_golden_set,
    evaluate_cognitive_accuracy,
    load_step2_predictions,
)


def _write_step2_result(
    out_dir: Path,
    ad_id: str,
    hook_framework: str = "Unknown",
    headline: str = "",
    secondary_copy: list[str] | None = None,
    human_presence: bool = False,
    model_count: int = 0,
) -> None:
    doc = {
        "ad_id": ad_id,
        "typography_hierarchy": {
            "primary_headline": {"text": headline},
            "secondary_copy": [{"text": t} for t in (secondary_copy or [])],
        },
        "marketing_psychology": {"hook_framework": hook_framework},
        "human_model_analysis": {
            "human_presence": human_presence,
            "model_count": model_count,
        },
    }
    (out_dir / f"{ad_id}.json").write_text(json.dumps(doc))


class TestLoadStep2Predictions:
    def test_extracts_expected_fields(self, tmp_path):
        _write_step2_result(
            tmp_path, "1", hook_framework="PAS", headline="Buy Now",
            secondary_copy=["Free shipping", "Limited time"],
            human_presence=True, model_count=2,
        )
        predictions = load_step2_predictions(tmp_path)
        assert predictions["1"] == {
            "predicted_headline": "Buy Now",
            "predicted_secondary_copy": ["Free shipping", "Limited time"],
            "predicted_hook_framework": "PAS",
            "predicted_human_presence": True,
            "predicted_model_count": 2,
        }

    def test_skips_unparseable_files(self, tmp_path):
        (tmp_path / "broken.json").write_text("not valid json{{{")
        _write_step2_result(tmp_path, "1")
        predictions = load_step2_predictions(tmp_path)
        assert list(predictions.keys()) == ["1"]

    def test_missing_nested_fields_default_gracefully(self, tmp_path):
        (tmp_path / "2.json").write_text(json.dumps({"ad_id": "2"}))
        predictions = load_step2_predictions(tmp_path)
        assert predictions["2"]["predicted_hook_framework"] == "Unknown"
        assert predictions["2"]["predicted_human_presence"] is False
        assert predictions["2"]["predicted_model_count"] == 0
        assert predictions["2"]["predicted_secondary_copy"] == []


def _fake_fetch_ok(url: str) -> bytes | None:
    return b"fake-image-bytes"


class TestBuildGoldenSet:
    def _setup(self, tmp_path, per_category_available=3):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads = []
        for category in HOOK_FRAMEWORK_CATEGORIES:
            for i in range(per_category_available):
                ad_id = f"{category.replace('/', '_')}_{i}"
                _write_step2_result(step2_dir, ad_id, hook_framework=category)
                ads.append({
                    "ad_archive_id": ad_id,
                    "image_urls": [f"https://cdn.example.com/{ad_id}.jpg"],
                })
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))
        return ads_file, step2_dir

    def test_stratifies_across_all_categories(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_fake_fetch_ok,
        )
        categories_seen = {e["predicted_hook_framework"] for e in golden_set}
        assert categories_seen == set(HOOK_FRAMEWORK_CATEGORIES)
        assert failed == 0

    def test_caps_at_per_category_limit(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_fake_fetch_ok,
        )
        assert len(golden_set) == 2 * len(HOOK_FRAMEWORK_CATEGORIES)
        assert ok == len(golden_set)

    def test_takes_all_available_when_fewer_than_cap(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)  # 3 per category available
        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=10, seed=1,
            fetch_fn=_fake_fetch_ok,
        )
        assert len(golden_set) == 3 * len(HOOK_FRAMEWORK_CATEGORIES)

    def test_expected_fields_start_null(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=1, seed=1,
            fetch_fn=_fake_fetch_ok,
        )
        for entry in golden_set:
            assert entry["expected_headline_correct"] is None
            assert entry["expected_hook_framework"] is None
            assert entry["expected_human_presence"] is None
            assert entry["expected_model_count"] is None

    def test_excludes_ads_without_images(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_step2_result(step2_dir, "no_image", hook_framework="PAS")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{"ad_archive_id": "no_image", "image_urls": []}]))

        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=5, fetch_fn=_fake_fetch_ok
        )
        assert golden_set == []

    def test_same_seed_is_reproducible(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        a, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=7,
            fetch_fn=_fake_fetch_ok,
        )
        b, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=7,
            fetch_fn=_fake_fetch_ok,
        )
        assert [e["ad_id"] for e in a] == [e["ad_id"] for e in b]

    def test_embeds_image_as_data_uri(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path, per_category_available=1)
        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=1, seed=1,
            fetch_fn=_fake_fetch_ok,
        )
        for entry in golden_set:
            assert entry["image_data_uri"].startswith("data:image/")
            assert "base64," in entry["image_data_uri"]
            assert "image_urls" not in entry

    def test_includes_real_ad_copy_from_corpus(self, tmp_path):
        """Regression: the labeling UI only showed Step 2's OCR headline,
        losing all the real Facebook ad copy (title/body/caption) that's
        entirely separate from the image itself -- confirmed live as the
        bigger "context is lost" gap while labeling."""
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_step2_result(step2_dir, "1", hook_framework="PAS")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{
            "ad_archive_id": "1",
            "image_urls": ["https://cdn.example.com/1.jpg"],
            "title": "Real Title",
            "body": "Real body copy",
            "caption": "example.com",
        }]))

        golden_set, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=1,
            fetch_fn=_fake_fetch_ok,
        )
        assert golden_set[0]["ad_title"] == "Real Title"
        assert golden_set[0]["ad_body"] == "Real body copy"
        assert golden_set[0]["ad_caption"] == "example.com"

    def test_missing_ad_copy_fields_default_to_empty_string(self, tmp_path):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        _write_step2_result(step2_dir, "1", hook_framework="PAS")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([{
            "ad_archive_id": "1",
            "image_urls": ["https://cdn.example.com/1.jpg"],
        }]))

        golden_set, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=1,
            fetch_fn=_fake_fetch_ok,
        )
        assert golden_set[0]["ad_title"] == ""
        assert golden_set[0]["ad_body"] == ""
        assert golden_set[0]["ad_caption"] == ""

    def test_skips_ads_whose_image_fetch_fails(self, tmp_path):
        # 5 available per category, but fetch always fails -> nothing added,
        # every attempt counted as a failure.
        ads_file, step2_dir = self._setup(tmp_path, per_category_available=5)

        def _fake_fetch_fails(url: str) -> bytes | None:
            return None

        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_fake_fetch_fails,
        )
        assert golden_set == []
        assert ok == 0
        assert failed == 5 * len(HOOK_FRAMEWORK_CATEGORIES)  # every candidate tried

    def test_tries_every_candidate_not_just_a_small_buffer(self, tmp_path):
        # 5 available per category; only the last-tried one (by shuffled
        # order) would ever succeed -- with per_category=4, the old fixed
        # oversample-by-3 design could exhaust its buffer before reaching a
        # working candidate. This must still reach per_category=4 by trying
        # the whole pool.
        ads_file, step2_dir = self._setup(tmp_path, per_category_available=5)

        def _flaky_fetch(url: str) -> bytes | None:
            return None if url.endswith("_0.jpg") else b"fake-image-bytes"

        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=4, seed=1,
            fetch_fn=_flaky_fetch,
        )
        counts = {}
        for entry in golden_set:
            cat = entry["predicted_hook_framework"]
            counts[cat] = counts.get(cat, 0) + 1
        for cat in HOOK_FRAMEWORK_CATEGORIES:
            assert counts.get(cat, 0) == 4
            assert not any(e["ad_id"].endswith("_0") for e in golden_set)


class TestBuildGoldenSetResumability:
    """Regression: Facebook CDN fetch success rate degraded sharply over a
    long session (confirmed live: ~90% -> ~75% -> ~14% success across three
    checks the same night) -- a single sample pass can leave several
    categories short after burning significant time. Re-running must top up
    the shortfall instead of re-fetching (and re-paying the failure cost
    for) ads that already succeeded."""

    def _setup(self, tmp_path, per_category_available=5):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads = []
        for category in HOOK_FRAMEWORK_CATEGORIES:
            for i in range(per_category_available):
                ad_id = f"{category.replace('/', '_')}_{i}"
                _write_step2_result(step2_dir, ad_id, hook_framework=category)
                ads.append({
                    "ad_archive_id": ad_id,
                    "image_urls": [f"https://cdn.example.com/{ad_id}.jpg"],
                })
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))
        return ads_file, step2_dir

    def test_existing_entries_are_kept_and_never_refetched(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)
        fetch_calls: list[str] = []

        def _tracking_fetch(url: str) -> bytes | None:
            fetch_calls.append(url)
            return b"fake-image-bytes"

        first, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_tracking_fetch,
        )
        fetch_calls.clear()

        second, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_tracking_fetch, existing_golden_set=first,
        )
        assert len(second) == len(first)
        assert ok == 0 and failed == 0  # nothing needed fetching -- already full
        assert fetch_calls == []
        assert {e["ad_id"] for e in second} == {e["ad_id"] for e in first}

    def test_tops_up_shortfall_from_a_partial_run(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path)

        # First pass: only 1 succeeds per category (rest fail).
        def _mostly_fails(url: str) -> bytes | None:
            return b"fake-image-bytes" if url.endswith("_0.jpg") else None

        partial, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=3, seed=1,
            fetch_fn=_mostly_fails,
        )
        assert len(partial) == len(HOOK_FRAMEWORK_CATEGORIES)  # 1 per category

        # Second pass: everything succeeds now -- should top up to 3/category
        # without disturbing the 1 already present per category.
        topped_up, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=3, seed=2,
            fetch_fn=_fake_fetch_ok, existing_golden_set=partial,
        )
        counts = {}
        for entry in topped_up:
            cat = entry["predicted_hook_framework"]
            counts[cat] = counts.get(cat, 0) + 1
        for cat in HOOK_FRAMEWORK_CATEGORIES:
            assert counts[cat] == 3
        # The original "_0" entries from the partial run are still present.
        original_ids = {e["ad_id"] for e in partial}
        topped_up_ids = {e["ad_id"] for e in topped_up}
        assert original_ids.issubset(topped_up_ids)

    def test_full_category_needs_no_new_fetch_attempts(self, tmp_path):
        ads_file, step2_dir = self._setup(tmp_path, per_category_available=2)
        full, _, _ = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_fake_fetch_ok,
        )

        def _explode_if_called(url: str) -> bytes | None:
            raise AssertionError("should not fetch — category is already full")

        result, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=2, seed=1,
            fetch_fn=_explode_if_called, existing_golden_set=full,
        )
        assert len(result) == len(full)


class TestBuildGoldenSetPriorityAdIds:
    """Regression: confirmed live that freshly-refreshed image_urls (via a
    real Apify re-scrape) succeed far more often than the corpus's original,
    months-stale ones. Trying priority ad_ids first means the fetch budget
    isn't spent working through the stale majority before reaching ones
    known likelier to succeed."""

    def _setup(self, tmp_path, per_category_available=5):
        step2_dir = tmp_path / "step2"
        step2_dir.mkdir()
        ads = []
        for category in HOOK_FRAMEWORK_CATEGORIES:
            for i in range(per_category_available):
                ad_id = f"{category.replace('/', '_')}_{i}"
                _write_step2_result(step2_dir, ad_id, hook_framework=category)
                ads.append({
                    "ad_archive_id": ad_id,
                    "image_urls": [f"https://cdn.example.com/{ad_id}.jpg"],
                })
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps(ads))
        return ads_file, step2_dir

    def test_priority_ids_tried_before_the_rest(self, tmp_path):
        # Only the "_4" ad (the last index) succeeds in each category; if
        # priority ad_ids are genuinely tried first, the golden set should
        # land on it after very few attempts instead of working through
        # indices 0-3 first.
        ads_file, step2_dir = self._setup(tmp_path)
        priority_ids = {f"{cat.replace('/', '_')}_4" for cat in HOOK_FRAMEWORK_CATEGORIES}

        def _only_priority_succeeds(url: str) -> bytes | None:
            return b"fake-image-bytes" if url.endswith("_4.jpg") else None

        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=1, seed=1,
            fetch_fn=_only_priority_succeeds, priority_ad_ids=priority_ids,
        )
        assert len(golden_set) == len(HOOK_FRAMEWORK_CATEGORIES)
        assert all(e["ad_id"].endswith("_4") for e in golden_set)
        # Priority-first means minimal wasted attempts: at most one failure
        # per category (a non-priority ad shuffled ahead within its own
        # tier is impossible here since priority is a separate, earlier tier).
        assert failed == 0

    def test_non_priority_ids_still_tried_as_fallback(self, tmp_path):
        # Priority ad_id always fails; a non-priority one must still succeed.
        ads_file, step2_dir = self._setup(tmp_path)
        priority_ids = {f"{cat.replace('/', '_')}_0" for cat in HOOK_FRAMEWORK_CATEGORIES}

        def _priority_fails_rest_ok(url: str) -> bytes | None:
            return None if url.endswith("_0.jpg") else b"fake-image-bytes"

        golden_set, ok, failed = build_golden_set(
            ads_file=ads_file, step2_out_dir=step2_dir, per_category=1, seed=1,
            fetch_fn=_priority_fails_rest_ok, priority_ad_ids=priority_ids,
        )
        assert len(golden_set) == len(HOOK_FRAMEWORK_CATEGORIES)
        assert not any(e["ad_id"].endswith("_0") for e in golden_set)


class TestEvaluateCognitiveAccuracy:
    def test_returns_all_four_categories(self):
        golden_set = [
            {
                "ad_id": "1",
                "predicted_headline": "Buy Now",
                "predicted_hook_framework": "PAS",
                "predicted_human_presence": True,
                "predicted_model_count": 1,
                "expected_headline_correct": True,
                "expected_hook_framework": "PAS",
                "expected_human_presence": True,
                "expected_model_count": 1,
            }
        ]
        results = evaluate_cognitive_accuracy(golden_set)
        assert set(results.keys()) == {
            "headline", "hook_framework", "human_presence", "model_count"
        }
        assert results["headline"]["accuracy"] == 1.0
        assert results["hook_framework"]["accuracy"] == 1.0
        assert results["human_presence"]["accuracy"] == 1.0
        assert results["model_count"]["exact_accuracy"] == 1.0

    def test_unlabeled_golden_set_returns_zero_evaluated_not_crash(self):
        golden_set = [
            {
                "ad_id": "1",
                "predicted_headline": "",
                "predicted_hook_framework": "Unknown",
                "predicted_human_presence": False,
                "predicted_model_count": 0,
                "expected_headline_correct": None,
                "expected_hook_framework": None,
                "expected_human_presence": None,
                "expected_model_count": None,
            }
        ]
        results = evaluate_cognitive_accuracy(golden_set)
        assert results["headline"]["total_evaluated"] == 0
        assert results["hook_framework"]["total_evaluated"] == 0
