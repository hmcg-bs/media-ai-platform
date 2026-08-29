"""Tests for pipeline/generation/reference_ads.py -- retrieval of real,
top-performing ads by composite success score, ranked by directive alignment
first (Round 7: reference ads must be genuine embodiments of the guide's own
top directives, not just any high scorer). Uses temp files + a patched
urlopen, no real network/filesystem dependency on the actual corpus."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.reference_ads import get_top_reference_ads


def _matrix_row(ad_id: str, days_active: int, collation: int, variants: int, **extra) -> dict:
    row = {
        "ad_id": ad_id, "days_active": days_active, "collation_count": collation,
        "variants_featured_count": variants,
    }
    row.update(extra)
    return row


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


def _empty_guide() -> GenerationGuide:
    return _guide_with()


@pytest.fixture
def corpus_files(tmp_path):
    rows = [
        _matrix_row("high", 200, 5, 4, dominant_color="blue", background_style="Busy"),
        _matrix_row("mid", 50, 1, 1, dominant_color="gray", background_style="Busy"),
        _matrix_row("no_features", 200, 5, 4),  # missing dominant_color/background_style
    ]
    ads = [
        {"ad_archive_id": "high", "image_urls": ["https://example.com/high.jpg"]},
        {"ad_archive_id": "mid", "image_urls": ["https://example.com/mid.jpg"]},
        {"ad_archive_id": "no_features", "image_urls": ["https://example.com/nf.jpg"]},
    ]
    matrix_file = tmp_path / "matrix.json"
    ads_file = tmp_path / "ads.json"
    matrix_file.write_text(json.dumps(rows))
    ads_file.write_text(json.dumps(ads))
    return matrix_file, ads_file


class _FakeUrlResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestGetTopReferenceAds:
    def test_returns_highest_scoring_ads_first(self, corpus_files):
        matrix_file, ads_file = corpus_files
        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"imgdata")):
            results = get_top_reference_ads(
                _empty_guide(), n=2, matrix_file=matrix_file, ads_file=ads_file
            )

        assert len(results) == 2
        assert results[0].ad_id == "high"
        assert results[0].composite_score > results[1].composite_score

    def test_excludes_ads_without_creative_features(self, corpus_files):
        matrix_file, ads_file = corpus_files
        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"imgdata")):
            results = get_top_reference_ads(
                _empty_guide(), n=10, matrix_file=matrix_file, ads_file=ads_file
            )

        assert all(r.ad_id != "no_features" for r in results)
        assert len(results) == 2  # only "high" and "mid" have real creative_features

    def test_skips_stale_image_without_raising(self, corpus_files):
        matrix_file, ads_file = corpus_files

        def flaky_urlopen(req, timeout=8):
            if "high" in req.full_url:
                raise TimeoutError("stale CDN URL")
            return _FakeUrlResponse(b"imgdata")

        with patch("urllib.request.urlopen", side_effect=flaky_urlopen):
            results = get_top_reference_ads(
                _empty_guide(), n=2, matrix_file=matrix_file, ads_file=ads_file
            )

        assert all(r.ad_id != "high" for r in results)
        assert len(results) == 1  # only "mid" survives


class TestDirectiveAlignmentRanking:
    def test_prefers_directive_aligned_ad_over_higher_score_misaligned_one(self, tmp_path):
        """Regression for the exact problem this round fixes: a reference ad
        must be a genuine embodiment of what the guide found, not just any
        top scorer -- "green" here scores higher but doesn't have the trait
        the guide's directive calls out, while "aligned" does."""
        rows = [
            _matrix_row(
                "misaligned", 300, 10, 6, dominant_color="green", background_style="Studio",
            ),
            _matrix_row(
                "aligned", 100, 2, 2, dominant_color="blue", background_style="Busy",
            ),
        ]
        ads = [
            {"ad_archive_id": "misaligned", "image_urls": ["https://example.com/m.jpg"]},
            {"ad_archive_id": "aligned", "image_urls": ["https://example.com/a.jpg"]},
        ]
        matrix_file, ads_file = tmp_path / "matrix.json", tmp_path / "ads.json"
        matrix_file.write_text(json.dumps(rows))
        ads_file.write_text(json.dumps(ads))

        guide = _guide_with(
            DirectionalSignal(
                dimension="background_style", value="Busy", direction="higher_is_better",
                magnitude=0.1, source="shap:composite_success_score",
            )
        )

        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"imgdata")):
            results = get_top_reference_ads(guide, n=1, matrix_file=matrix_file, ads_file=ads_file)

        assert len(results) == 1
        assert results[0].ad_id == "aligned"
        assert results[0].alignment_score == 1

    def test_lower_is_better_match_counts_as_negative_alignment(self, tmp_path):
        rows = [
            _matrix_row("studio", 300, 10, 6, dominant_color="green", background_style="Studio"),
            _matrix_row("busy", 100, 2, 2, dominant_color="green", background_style="Busy"),
        ]
        ads = [
            {"ad_archive_id": "studio", "image_urls": ["https://example.com/s.jpg"]},
            {"ad_archive_id": "busy", "image_urls": ["https://example.com/b.jpg"]},
        ]
        matrix_file, ads_file = tmp_path / "matrix.json", tmp_path / "ads.json"
        matrix_file.write_text(json.dumps(rows))
        ads_file.write_text(json.dumps(ads))

        guide = _guide_with(
            DirectionalSignal(
                dimension="background_style", value="Studio", direction="lower_is_better",
                magnitude=0.1, source="cox:days_active",
            )
        )

        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"imgdata")):
            results = get_top_reference_ads(guide, n=2, matrix_file=matrix_file, ads_file=ads_file)

        assert results[0].ad_id == "busy"  # alignment 0 beats "studio"'s alignment -1
        assert results[1].ad_id == "studio"
        assert results[1].alignment_score == -1

    def test_relaxes_min_alignment_when_too_few_ads_clear_the_bar(self, corpus_files):
        """If no real ad in the corpus actually embodies a directive (a real,
        possible corpus-coverage gap), degrade gracefully to score-only
        ranking rather than returning nothing to compare against."""
        matrix_file, ads_file = corpus_files
        guide = _guide_with(
            DirectionalSignal(
                dimension="dominant_color", value="purple", direction="higher_is_better",
                magnitude=0.1, source="shap:composite_success_score",
            )
        )

        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"imgdata")):
            results = get_top_reference_ads(guide, n=2, matrix_file=matrix_file, ads_file=ads_file)

        assert len(results) == 2  # relaxed back to all candidates, not zero
