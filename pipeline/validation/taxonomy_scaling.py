"""Scale a human-adjudicated taxonomy with a validated binary classifier.

The human review remains the gold standard. Automated predictions can admit
additional rows only after their high-confidence positive precision passes a
fixed lower-confidence-bound gate on the untouched gold sample. Ambiguous rows
are quarantined for human review; automated labels never become performance
outcomes or subcategory evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.validation.product_classifier import CLASSIFIER_SCHEMA_VERSION
from pipeline.validation.taxonomy_adjudication import REPORT_SCHEMA_VERSION

SCALING_REPORT_SCHEMA_VERSION = "supplements-taxonomy-classifier-validation-v1"
AUTO_ACCEPT_CONFIDENCE = 0.80
MIN_EVALUATED = 100
MIN_CLASS_ROWS = 10
MIN_AUTO_POSITIVES = 20
MIN_AUTO_PRECISION_LOWER_95 = 0.90
MIN_AUTO_RECALL_LOWER_95 = 0.80


def _content_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total == 0:
        return 0.0
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = proportion + z**2 / (2 * total)
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
    return (centre - margin) / denominator


def _prediction_provenance(
    classified: list[dict[str, Any]],
) -> tuple[dict[str, str] | None, list[str]]:
    provenances = []
    failures = []
    for ad in classified:
        ad_id = str(ad.get("ad_archive_id") or "<missing>")
        classification = ad.get("classification") or {}
        provenance = classification.get("provenance") or {}
        required = {
            "classifier_schema_version",
            "provider",
            "model",
            "prompt_sha256",
            "implementation_sha256",
        }
        if set(provenance) != required:
            failures.append(f"ad {ad_id} has incomplete classifier provenance")
            continue
        if provenance["classifier_schema_version"] != CLASSIFIER_SCHEMA_VERSION:
            failures.append(f"ad {ad_id} has unsupported classifier schema")
        if any(not provenance[name] for name in required):
            failures.append(f"ad {ad_id} has blank classifier provenance")
        for name in ("prompt_sha256", "implementation_sha256"):
            if len(str(provenance[name])) != 64:
                failures.append(f"ad {ad_id} has invalid {name}")
        provenances.append(provenance)
    unique = {_content_sha256(value): value for value in provenances}
    if len(unique) > 1:
        failures.append("classified rows combine multiple classifier contracts")
    return next(iter(unique.values()), None), failures


def build_classifier_validation_report(
    classified_ads: list[dict[str, Any]],
    adjudication_report: dict[str, Any],
) -> dict[str, Any]:
    """Validate the fixed auto-admission policy against human gold labels."""
    failures = []
    if adjudication_report.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        failures.append("human adjudication report schema is unsupported")
    if adjudication_report.get("status") != "passed":
        failures.append("human adjudication report did not pass")
    gold = adjudication_report.get("gold_labels") or []
    if adjudication_report.get("gold_labels_content_sha256") != _content_sha256(gold):
        failures.append("gold labels do not match their adjudication-report hash")
    gold_by_id = {str(row.get("ad_id") or ""): row for row in gold}
    if len(gold_by_id) != len(gold) or "" in gold_by_id:
        failures.append("gold labels have missing or duplicate ad IDs")
    classified_by_id = {
        str(row.get("ad_archive_id") or ""): row
        for row in classified_ads
        if row.get("ad_archive_id")
    }
    if len(classified_by_id) != len(classified_ads):
        failures.append("classified corpus has missing or duplicate ad IDs")
    sample_rows = [classified_by_id[ad_id] for ad_id in gold_by_id if ad_id in classified_by_id]
    if len(sample_rows) != len(gold_by_id):
        failures.append("classified corpus does not cover every gold-sample ad")
    provenance, provenance_failures = _prediction_provenance(sample_rows)
    failures.extend(provenance_failures)

    tp = fp = fn = tn = 0
    auto_tp = auto_fp = 0
    for ad_id, truth in gold_by_id.items():
        ad = classified_by_id.get(ad_id)
        if ad is None:
            continue
        prediction = ad.get("classification") or {}
        predicted = prediction.get("is_supplement")
        confidence = prediction.get("confidence")
        if not isinstance(predicted, bool) or not isinstance(confidence, (int, float)):
            failures.append(f"ad {ad_id} has an invalid classifier prediction")
            continue
        actual = truth.get("is_supplement")
        if not isinstance(actual, bool):
            failures.append(f"ad {ad_id} has an invalid gold label")
            continue
        if predicted and actual:
            tp += 1
        elif predicted:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
        if predicted and confidence >= AUTO_ACCEPT_CONFIDENCE:
            if actual:
                auto_tp += 1
            else:
                auto_fp += 1

    evaluated = tp + fp + fn + tn
    positives = tp + fn
    negatives = tn + fp
    auto_total = auto_tp + auto_fp
    precision = auto_tp / auto_total if auto_total else 0.0
    precision_lower = _wilson_lower(auto_tp, auto_total)
    recall = auto_tp / positives if positives else 0.0
    recall_lower = _wilson_lower(auto_tp, positives)
    if evaluated < MIN_EVALUATED:
        failures.append(f"only {evaluated} gold rows evaluated; {MIN_EVALUATED} required")
    if positives < MIN_CLASS_ROWS or negatives < MIN_CLASS_ROWS:
        failures.append(f"gold sample needs at least {MIN_CLASS_ROWS} rows from each binary class")
    if auto_total < MIN_AUTO_POSITIVES:
        failures.append(
            f"only {auto_total} high-confidence positives; {MIN_AUTO_POSITIVES} required"
        )
    if precision_lower < MIN_AUTO_PRECISION_LOWER_95:
        failures.append(
            f"high-confidence positive precision lower bound {precision_lower:.3f} "
            f"is below {MIN_AUTO_PRECISION_LOWER_95:.3f}"
        )
    if recall_lower < MIN_AUTO_RECALL_LOWER_95:
        failures.append(
            f"high-confidence positive recall lower bound {recall_lower:.3f} "
            f"is below {MIN_AUTO_RECALL_LOWER_95:.3f}"
        )
    return {
        "report_schema_version": SCALING_REPORT_SCHEMA_VERSION,
        "status": "passed" if not failures else "failed",
        "policy": {
            "auto_accept_confidence": AUTO_ACCEPT_CONFIDENCE,
            "minimum_evaluated": MIN_EVALUATED,
            "minimum_class_rows": MIN_CLASS_ROWS,
            "minimum_auto_positives": MIN_AUTO_POSITIVES,
            "minimum_auto_precision_lower_95": MIN_AUTO_PRECISION_LOWER_95,
            "minimum_auto_recall_lower_95": MIN_AUTO_RECALL_LOWER_95,
            "subcategory_policy": "automated rows carry no subcategory evidence",
        },
        "adjudication_report_sha256": _content_sha256(adjudication_report),
        "classified_corpus_sha256": _content_sha256(classified_ads),
        "classified_gold_rows_sha256": _content_sha256(sample_rows),
        "classifier_contract": provenance,
        "n_evaluated": evaluated,
        "n_positive": positives,
        "n_negative": negatives,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "high_confidence_positive": {
            "n": auto_total,
            "true_positives": auto_tp,
            "false_positives": auto_fp,
            "precision": round(precision, 5),
            "precision_wilson_lower_95": round(precision_lower, 5),
            "recall": round(recall, 5),
            "recall_wilson_lower_95": round(recall_lower, 5),
        },
        "failures": failures,
    }


def apply_validated_classifier(
    ads: list[dict[str, Any]],
    classified_ads: list[dict[str, Any]],
    adjudication_report: dict[str, Any],
    validation_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Admit human positives and validated high-confidence positives only."""
    if validation_report.get("report_schema_version") != SCALING_REPORT_SCHEMA_VERSION:
        raise ValueError("classifier validation report schema is unsupported")
    if validation_report.get("status") != "passed":
        raise ValueError("classifier validation report did not pass")
    if validation_report.get("adjudication_report_sha256") != _content_sha256(adjudication_report):
        raise ValueError("classifier validation does not match human adjudication")
    if validation_report.get("classified_corpus_sha256") != _content_sha256(classified_ads):
        raise ValueError("classified corpus does not match the validation report")
    gold = {str(row["ad_id"]): row for row in adjudication_report.get("gold_labels") or []}
    classified = {str(row.get("ad_archive_id") or ""): row for row in classified_ads}
    if len(classified) != len(classified_ads):
        raise ValueError("classified corpus has missing or duplicate ad IDs")
    sample_rows = [classified[ad_id] for ad_id in gold if ad_id in classified]
    if validation_report.get("classified_gold_rows_sha256") != _content_sha256(sample_rows):
        raise ValueError("full classified corpus does not match validated gold predictions")
    provenance, failures = _prediction_provenance(classified_ads)
    if failures or provenance != validation_report.get("classifier_contract"):
        raise ValueError("full corpus classifier provenance does not match validated contract")

    accepted = []
    review_queue = []
    outcomes: Counter[str] = Counter()
    validation_hash = _content_sha256(validation_report)
    for ad in ads:
        ad_id = str(ad.get("ad_archive_id") or "")
        prediction_row = classified.get(ad_id)
        if prediction_row is None:
            review_queue.append({**ad, "taxonomy_review_reason": "missing_prediction"})
            outcomes["missing_prediction"] += 1
            continue
        prediction = prediction_row.get("classification") or {}
        human = gold.get(ad_id)
        if human is not None:
            if human.get("is_supplement") is True:
                accepted.append(
                    {
                        **ad,
                        "taxonomy_label": {
                            "source": "human_adjudication",
                            "is_supplement": True,
                            "supplement_subcategory": human.get("supplement_subcategory"),
                            "provenance": {
                                **(human.get("taxonomy_provenance") or {}),
                                "adjudication_report_schema_version": adjudication_report.get(
                                    "report_schema_version"
                                ),
                                "approved_labels_content_sha256": adjudication_report.get(
                                    "approved_labels_content_sha256"
                                ),
                                "adjudication_report_sha256": _content_sha256(
                                    adjudication_report
                                ),
                            },
                        },
                    }
                )
                outcomes["accepted_human"] += 1
            else:
                outcomes["rejected_human"] += 1
            continue
        confidence = prediction.get("confidence")
        predicted = prediction.get("is_supplement")
        if not isinstance(predicted, bool) or not isinstance(confidence, (int, float)):
            review_queue.append({**ad, "taxonomy_review_reason": "invalid_prediction"})
            outcomes["invalid_prediction"] += 1
        elif confidence < AUTO_ACCEPT_CONFIDENCE:
            review_queue.append(
                {
                    **ad,
                    "classification": prediction,
                    "taxonomy_review_reason": "below_auto_accept_confidence",
                }
            )
            outcomes["queued_low_confidence"] += 1
        elif predicted:
            accepted.append(
                {
                    **ad,
                    "taxonomy_label": {
                        "source": "validated_classifier",
                        "is_supplement": True,
                        "supplement_subcategory": None,
                        "provenance": {
                            **prediction["provenance"],
                            "confidence": confidence,
                            "classifier_validation_report_sha256": validation_hash,
                            "gold_sample_sha256": adjudication_report.get("sample_sha256"),
                        },
                    },
                }
            )
            outcomes["accepted_classifier"] += 1
        else:
            outcomes["rejected_classifier"] += 1
    summary = {
        "status": "passed",
        "policy": validation_report["policy"],
        "ads_content_sha256": _content_sha256(ads),
        "classified_ads_content_sha256": _content_sha256(classified_ads),
        "classifier_validation_report_sha256": validation_hash,
        "n_accepted": len(accepted),
        "n_review_queue": len(review_queue),
        "outcomes": dict(sorted(outcomes.items())),
        "loop_isolation": "taxonomy labels are inputs, never Meta performance outcomes",
    }
    return accepted, review_queue, summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate and scale the Supplements taxonomy.")
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--classified-ads", type=Path, required=True)
    parser.add_argument("--adjudication-report", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--accepted-ads", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--application-report", type=Path, required=True)
    args = parser.parse_args()
    ads = json.loads(args.ads.read_text())
    classified = json.loads(args.classified_ads.read_text())
    adjudication = json.loads(args.adjudication_report.read_text())
    validation = build_classifier_validation_report(classified, adjudication)
    with exclusive_output(args.validation_report):
        atomic_write_json(args.validation_report, validation)
    if validation["status"] != "passed":
        print(f"Classifier validation failed: {args.validation_report}")
        raise SystemExit(2)
    accepted, review_queue, application = apply_validated_classifier(
        ads, classified, adjudication, validation
    )
    with (
        exclusive_output(args.accepted_ads),
        exclusive_output(args.review_queue),
        exclusive_output(args.application_report),
    ):
        atomic_write_json(args.accepted_ads, accepted)
        atomic_write_json(args.review_queue, review_queue)
        atomic_write_json(args.application_report, application)
    print(
        f"Taxonomy scaled: accepted={len(accepted)} review_queue={len(review_queue)} "
        f"report={args.application_report}"
    )


if __name__ == "__main__":
    main()
