from __future__ import annotations

import hashlib
import json

from pipeline.validation.taxonomy_scaling import (
    apply_validated_classifier,
    build_classifier_validation_report,
)


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _provenance() -> dict[str, str]:
    return {
        "classifier_schema_version": "supplements-binary-classifier-v1",
        "provider": "replicate",
        "model": "google/gemini-test",
        "prompt_sha256": "a" * 64,
        "implementation_sha256": "b" * 64,
    }


def _fixture(n: int = 120) -> tuple[list[dict], list[dict], dict]:
    ads = []
    classified = []
    gold = []
    for index in range(n):
        ad_id = str(index)
        actual = index < 60
        ads.append({"ad_archive_id": ad_id, "page_id": f"p-{index}"})
        classified.append(
            {
                **ads[-1],
                "classification": {
                    "is_supplement": actual,
                    "confidence": 0.95,
                    "provenance": _provenance(),
                },
            }
        )
        gold.append(
            {
                "ad_id": ad_id,
                "is_supplement": actual,
                "supplement_subcategory": "protein" if actual else None,
                "taxonomy_provenance": {"reviewer_id": "human"},
            }
        )
    adjudication = {
        "report_schema_version": "supplements-taxonomy-adjudication-report-v1",
        "status": "passed",
        "sample_sha256": "c" * 64,
        "gold_labels": gold,
        "gold_labels_content_sha256": _hash(gold),
    }
    return ads, classified, adjudication


def test_classifier_validation_requires_strong_high_confidence_precision():
    _ads, classified, adjudication = _fixture()
    report = build_classifier_validation_report(classified, adjudication)
    assert report["status"] == "passed"
    assert report["high_confidence_positive"]["precision"] == 1.0
    assert report["high_confidence_positive"]["precision_wilson_lower_95"] > 0.9

    for index in range(8):
        classified[60 + index]["classification"]["is_supplement"] = True
    failed = build_classifier_validation_report(classified, adjudication)
    assert failed["status"] == "failed"
    assert any("precision lower bound" in reason for reason in failed["failures"])


def test_classifier_validation_blocks_low_auto_admission_recall():
    _ads, classified, adjudication = _fixture()
    for index in range(15):
        classified[index]["classification"]["confidence"] = 0.70

    report = build_classifier_validation_report(classified, adjudication)

    assert report["status"] == "failed"
    assert any("recall lower bound" in reason for reason in report["failures"])


def test_scaling_preserves_human_gold_and_quarantines_low_confidence_rows():
    ads, classified, adjudication = _fixture()
    for index in range(120, 123):
        ad = {"ad_archive_id": str(index), "page_id": f"p-{index}"}
        ads.append(ad)
        classified.append(
            {
                **ad,
                "classification": {
                    "is_supplement": index != 122,
                    "confidence": 0.95 if index != 121 else 0.70,
                    "provenance": _provenance(),
                },
            }
        )
    validation = build_classifier_validation_report(classified, adjudication)
    accepted, queue, summary = apply_validated_classifier(
        ads, classified, adjudication, validation
    )

    by_id = {ad["ad_archive_id"]: ad for ad in accepted}
    assert by_id["0"]["taxonomy_label"]["source"] == "human_adjudication"
    assert by_id["120"]["taxonomy_label"]["source"] == "validated_classifier"
    assert by_id["120"]["taxonomy_label"]["supplement_subcategory"] is None
    assert [ad["ad_archive_id"] for ad in queue] == ["121"]
    assert "122" not in by_id
    assert summary["outcomes"]["rejected_classifier"] == 1


def test_scaling_rejects_tampered_gold_predictions():
    ads, classified, adjudication = _fixture()
    validation = build_classifier_validation_report(classified, adjudication)
    classified[0]["classification"]["confidence"] = 0.81
    try:
        apply_validated_classifier(ads, classified, adjudication, validation)
    except ValueError as exc:
        assert "classified corpus" in str(exc)
    else:
        raise AssertionError("tampered classifier outputs must fail closed")
