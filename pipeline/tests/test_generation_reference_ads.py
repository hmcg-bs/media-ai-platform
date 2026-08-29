"""Tests for pipeline/generation/reference_ads.py -- retrieval of real,
top-performing ads by composite success score. Uses temp files + a patched
urlopen, no real network/filesystem dependency on the actual corpus."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pipeline.generation.reference_ads import get_top_reference_ads


def _matrix_row(ad_id: str, days_active: int, collation: int, variants: int, **extra) -> dict:
    row = {
        "ad_id": ad_id, "days_active": days_active, "collation_count": collation,
        "variants_featured_count": variants,
    }
    row.update(extra)
    return row


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
            results = get_top_reference_ads(n=2, matrix_file=matrix_file, ads_file=ads_file)

        assert len(results) == 2
        assert results[0].ad_id == "high"
        assert results[0].composite_score > results[1].composite_score

    def test_excludes_ads_without_creative_features(self, corpus_files):
        matrix_file, ads_file = corpus_files
        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"imgdata")):
            results = get_top_reference_ads(n=10, matrix_file=matrix_file, ads_file=ads_file)

        assert all(r.ad_id != "no_features" for r in results)
        assert len(results) == 2  # only "high" and "mid" have real creative_features

    def test_skips_stale_image_without_raising(self, corpus_files):
        matrix_file, ads_file = corpus_files

        def flaky_urlopen(req, timeout=8):
            if "high" in req.full_url:
                raise TimeoutError("stale CDN URL")
            return _FakeUrlResponse(b"imgdata")

        with patch("urllib.request.urlopen", side_effect=flaky_urlopen):
            results = get_top_reference_ads(n=2, matrix_file=matrix_file, ads_file=ads_file)

        assert all(r.ad_id != "high" for r in results)
        assert len(results) == 1  # only "mid" survives
