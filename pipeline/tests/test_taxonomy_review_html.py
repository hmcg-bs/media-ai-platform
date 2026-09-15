import hashlib
import json
from pathlib import Path

import pytest

from pipeline.validation.taxonomy_review_html import build_taxonomy_review_html


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            [
                {
                    "ad_archive_id": "ad-1",
                    "title": "Full <product> title",
                    "body": "Full body",
                    "link_url": "https://example.test/product",
                    "image_urls": ["https://example.test/image.jpg"],
                }
            ]
        )
    )
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "report_schema_version": "supplements-taxonomy-review-v1",
                "sample_sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
                "minimum_double_review_fraction": 0.2,
                "minimum_cohen_kappa": 0.8,
                "items": [
                    {
                        "ad_id": "ad-1",
                        "page_id": "page-1",
                        "page_name": "Brand",
                        "title": "Short title",
                        "body": "Short body",
                        "search_queries": [],
                        "review_strata": ["candidate:protein"],
                        "snapshot_url": "https://example.test/ad",
                        "primary": {
                            "reviewer_id": None,
                            "reviewed_at": None,
                            "is_supplement": None,
                            "supplement_subcategory": None,
                            "notes": "",
                        },
                        "secondary": None,
                        "adjudication": None,
                    }
                ],
            }
        )
    )
    return review, sample


def test_builds_self_contained_review_with_exact_contract(tmp_path: Path) -> None:
    review, sample = _write_inputs(tmp_path)
    output = tmp_path / "review.html"

    build_taxonomy_review_html(review, sample, output)

    html = output.read_text()
    assert 'id="reviewData" type="application/json"' in html
    assert '"report_schema_version":"supplements-taxonomy-review-v1"' in html
    assert "Full \\u003cproduct> title" in html
    assert "supplements_taxonomy_review_v1.json" in html
    assert "https://cdn" not in html


def test_rejects_sample_that_does_not_match_review_hash(tmp_path: Path) -> None:
    review, sample = _write_inputs(tmp_path)
    sample.write_text("[]")

    with pytest.raises(ValueError, match="sample_sha256"):
        build_taxonomy_review_html(review, sample, tmp_path / "review.html")
