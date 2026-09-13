from __future__ import annotations

import json
from pathlib import Path

from pipeline.validation.phase0_validator import apply_manual_taxonomy_gate
from pipeline.validation.taxonomy_adjudication import (
    create_review_artifact,
    validate_review_artifact,
    verify_approved_taxonomy,
)


def _files(tmp_path: Path, n: int = 10) -> tuple[Path, Path]:
    sample = [{"ad_archive_id": str(i), "page_id": f"p{i}", "title": f"Ad {i}"} for i in range(n)]
    template = [
        {
            "ad_id": str(i),
            "page_id": f"p{i}",
            "page_name": "Brand",
            "title": f"Ad {i}",
            "body": "",
            "search_queries": [],
            "review_strata": ["query:unknown"],
        }
        for i in range(n)
    ]
    sample_file = tmp_path / "sample.json"
    template_file = tmp_path / "template.json"
    sample_file.write_text(json.dumps(sample))
    template_file.write_text(json.dumps(template))
    return sample_file, template_file


def _decision(reviewer: str, value: bool, category: str | None = None) -> dict:
    return {
        "reviewer_id": reviewer,
        "reviewed_at": "2026-09-12T10:00:00Z",
        "is_supplement": value,
        "supplement_subcategory": category,
        "notes": "",
    }


def test_create_review_artifact_binds_exact_sample_bytes(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    assert artifact["report_schema_version"] == "supplements-taxonomy-review-v1"
    assert len(artifact["sample_sha256"]) == 64
    assert [item["ad_id"] for item in artifact["items"]] == [str(i) for i in range(10)]
    assert artifact["items"][0]["primary"]["is_supplement"] is None


def test_incomplete_template_fails_closed_without_approved_labels(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    report, approved = validate_review_artifact(sample, artifact)
    assert report["status"] == "failed"
    assert approved == []
    assert any("primary reviewer_id" in reason for reason in report["failures"])


def test_reviewers_cannot_lower_fixed_quality_thresholds(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    artifact["minimum_double_review_fraction"] = 0
    artifact["minimum_cohen_kappa"] = 0
    report, approved = validate_review_artifact(sample, artifact)
    assert report["status"] == "failed"
    assert approved == []
    assert any("fixed review policy" in reason for reason in report["failures"])


def test_passing_reviews_require_coverage_kappa_and_emit_provenance(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    for i, item in enumerate(artifact["items"]):
        is_supplement = i % 2 == 0
        category = "protein" if is_supplement else None
        item["primary"] = _decision("primary", is_supplement, category)
        if i < 2:
            item["secondary"] = _decision("secondary", is_supplement, category)

    report, approved = validate_review_artifact(sample, artifact)
    assert report["status"] == "passed"
    assert report["double_review_fraction"] == 0.2
    assert report["cohen_kappa"] == 1.0
    assert len(approved) == 5
    assert approved[0]["taxonomy_provenance"]["sample_sha256"] == artifact["sample_sha256"]
    verify_approved_taxonomy(report, approved)
    ads = [{"ad_archive_id": str(i)} for i in range(10)]
    gated, _counts = apply_manual_taxonomy_gate(ads, approved, report)
    provenance = gated[0]["taxonomy_label"]["provenance"]
    assert provenance["approved_labels_content_sha256"] == report["approved_labels_content_sha256"]
    assert (
        provenance["adjudication_report_schema_version"]
        == "supplements-taxonomy-adjudication-report-v1"
    )


def test_modified_approved_labels_are_rejected(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    for i, item in enumerate(artifact["items"]):
        is_supplement = i % 2 == 0
        category = "protein" if is_supplement else None
        item["primary"] = _decision("primary", is_supplement, category)
        if i < 2:
            item["secondary"] = _decision("secondary", is_supplement, category)
    report, approved = validate_review_artifact(sample, artifact)
    approved[0]["supplement_subcategory"] = "creatine"

    try:
        verify_approved_taxonomy(report, approved)
    except ValueError as exc:
        assert "do not match" in str(exc)
    else:
        raise AssertionError("modified labels must fail closed")


def test_subcategory_disagreement_requires_independent_adjudication(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    for i, item in enumerate(artifact["items"]):
        is_supplement = i % 2 == 0
        category = "protein" if is_supplement else None
        item["primary"] = _decision("primary", is_supplement, category)
        if i < 2:
            item["secondary"] = _decision(
                "secondary", is_supplement, "creatine" if i == 0 else category
            )

    report, approved = validate_review_artifact(sample, artifact)
    assert report["status"] == "failed"
    assert approved == []
    assert any("requires adjudication" in reason for reason in report["failures"])


def test_adjudication_must_not_be_by_either_original_reviewer(tmp_path: Path):
    sample, template = _files(tmp_path)
    artifact = create_review_artifact(sample, template)
    for i, item in enumerate(artifact["items"]):
        is_supplement = i % 2 == 0
        category = "protein" if is_supplement else None
        item["primary"] = _decision("primary", is_supplement, category)
        if i < 2:
            item["secondary"] = _decision(
                "secondary", not is_supplement, None if is_supplement else "protein"
            )
            item["adjudication"] = {
                **_decision("primary", is_supplement, category),
                "rationale": "Checked the destination product.",
            }

    report, approved = validate_review_artifact(sample, artifact)
    assert report["status"] == "failed"
    assert approved == []
    assert any("independent reviewer" in reason for reason in report["failures"])
