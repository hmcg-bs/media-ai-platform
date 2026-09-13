"""Human-review contract for the Supplements taxonomy gate.

Keyword strata and model classifications may prioritize review, but they are
never labels. This module turns the existing 120-row review template into a
versioned two-reviewer/adjudication artifact and refuses to publish approved
labels until coverage, agreement, and provenance gates pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline.artifacts import atomic_write_json, exclusive_output

SCHEMA_VERSION = "supplements-taxonomy-review-v1"
REPORT_SCHEMA_VERSION = "supplements-taxonomy-adjudication-report-v1"
MIN_DOUBLE_REVIEW_FRACTION = 0.20
MIN_COHEN_KAPPA = 0.80
SUPPLEMENT_SUBCATEGORIES = {
    "collagen",
    "creatine",
    "magnesium",
    "melatonin_sleep",
    "multivitamin",
    "omega_3",
    "pre_workout",
    "probiotic",
    "protein",
    "vitamin",
    "weight_management",
    "other_supplements",
}


class TaxonomyDecision(BaseModel):
    """One human decision. Null fields make the generated template editable."""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str | None = None
    reviewed_at: str | None = None
    is_supplement: bool | None = None
    supplement_subcategory: str | None = None
    notes: str = ""


class TaxonomyAdjudication(TaxonomyDecision):
    rationale: str = ""


class TaxonomyReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_id: str
    page_id: str = ""
    page_name: str = ""
    title: str = ""
    body: str = ""
    search_queries: list[str] = Field(default_factory=list)
    review_strata: list[str] = Field(default_factory=list)
    snapshot_url: str = ""
    primary: TaxonomyDecision = Field(default_factory=TaxonomyDecision)
    secondary: TaxonomyDecision | None = None
    adjudication: TaxonomyAdjudication | None = None


class TaxonomyReviewArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_schema_version: Literal["supplements-taxonomy-review-v1"] = SCHEMA_VERSION
    sample_sha256: str
    minimum_double_review_fraction: float = MIN_DOUBLE_REVIEW_FRACTION
    minimum_cohen_kappa: float = MIN_COHEN_KAPPA
    items: list[TaxonomyReviewItem]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decision_error(decision: TaxonomyDecision, label: str) -> str | None:
    if not decision.reviewer_id or not decision.reviewer_id.strip():
        return f"{label} reviewer_id is required"
    if not decision.reviewed_at:
        return f"{label} reviewed_at is required"
    try:
        datetime.fromisoformat(decision.reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        return f"{label} reviewed_at must be ISO 8601"
    if decision.is_supplement is None:
        return f"{label} is_supplement is required"
    if decision.is_supplement:
        if decision.supplement_subcategory not in SUPPLEMENT_SUBCATEGORIES:
            return (
                f"{label} supplement_subcategory must be one of {sorted(SUPPLEMENT_SUBCATEGORIES)}"
            )
    elif decision.supplement_subcategory is not None:
        return f"{label} non-supplement decision cannot have a supplement_subcategory"
    return None


def _decisions_agree(primary: TaxonomyDecision, secondary: TaxonomyDecision) -> bool:
    return primary.is_supplement == secondary.is_supplement and (
        primary.is_supplement is not True
        or primary.supplement_subcategory == secondary.supplement_subcategory
    )


def _cohen_kappa(pairs: list[tuple[bool, bool]]) -> tuple[float | None, float]:
    """Return Cohen's κ and observed agreement for binary labels."""
    if not pairs:
        return None, 0.0
    observed = sum(left == right for left, right in pairs) / len(pairs)
    left_true = sum(left for left, _right in pairs) / len(pairs)
    right_true = sum(right for _left, right in pairs) / len(pairs)
    expected = left_true * right_true + (1 - left_true) * (1 - right_true)
    if expected == 1.0:
        return None, observed
    return (observed - expected) / (1 - expected), observed


def create_review_artifact(sample_file: Path, template_file: Path) -> dict[str, Any]:
    """Convert the existing flat blank label template to the v1 review contract."""
    sample = json.loads(sample_file.read_text())
    template = json.loads(template_file.read_text())
    sample_ids = [str(row.get("ad_archive_id") or row.get("ad_id") or "") for row in sample]
    template_by_id = {str(row.get("ad_id") or ""): row for row in template}
    if not sample_ids or any(not ad_id for ad_id in sample_ids):
        raise ValueError("sample must contain a non-empty stable ID for every row")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample contains duplicate ad IDs")
    if set(sample_ids) != set(template_by_id):
        raise ValueError("template ad IDs must exactly match the frozen sample")

    items = []
    for ad_id in sample_ids:
        row = template_by_id[ad_id]
        items.append(
            TaxonomyReviewItem(
                ad_id=ad_id,
                page_id=str(row.get("page_id") or ""),
                page_name=str(row.get("page_name") or ""),
                title=str(row.get("title") or ""),
                body=str(row.get("body") or ""),
                search_queries=list(row.get("search_queries") or []),
                review_strata=list(row.get("review_strata") or []),
                snapshot_url=str(row.get("snapshot_url") or ""),
            )
        )
    artifact = TaxonomyReviewArtifact(sample_sha256=_sha256(sample_file), items=items)
    return artifact.model_dump(mode="json")


def validate_review_artifact(
    sample_file: Path,
    review_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate M1 and return a report plus fail-closed approved labels."""
    review = TaxonomyReviewArtifact.model_validate(review_payload)
    sample = json.loads(sample_file.read_text())
    expected_ids = [str(row.get("ad_archive_id") or row.get("ad_id") or "") for row in sample]
    item_ids = [item.ad_id for item in review.items]
    failures: list[str] = []
    if review.minimum_double_review_fraction != MIN_DOUBLE_REVIEW_FRACTION:
        failures.append("minimum_double_review_fraction differs from the fixed review policy")
    if review.minimum_cohen_kappa != MIN_COHEN_KAPPA:
        failures.append("minimum_cohen_kappa differs from the fixed review policy")
    if review.sample_sha256 != _sha256(sample_file):
        failures.append("sample_sha256 does not match the frozen sample bytes")
    if len(item_ids) != len(set(item_ids)):
        failures.append("review artifact contains duplicate ad IDs")
    missing_ids = sorted(set(expected_ids) - set(item_ids))
    extra_ids = sorted(set(item_ids) - set(expected_ids))
    if missing_ids:
        failures.append(f"{len(missing_ids)} sampled ads are missing reviews")
    if extra_ids:
        failures.append(f"{len(extra_ids)} review rows are not in the frozen sample")

    double_pairs: list[tuple[bool, bool]] = []
    final_labels: list[dict[str, Any]] = []
    disagreements = 0
    adjudicated = 0
    for item in review.items:
        primary_error = _decision_error(item.primary, "primary")
        if primary_error:
            failures.append(f"ad {item.ad_id}: {primary_error}")
            continue
        final: TaxonomyDecision = item.primary
        source = "primary"
        if item.secondary is not None:
            secondary_error = _decision_error(item.secondary, "secondary")
            if secondary_error:
                failures.append(f"ad {item.ad_id}: {secondary_error}")
                continue
            if item.secondary.reviewer_id == item.primary.reviewer_id:
                failures.append(f"ad {item.ad_id}: primary and secondary reviewers must differ")
                continue
            double_pairs.append(
                (bool(item.primary.is_supplement), bool(item.secondary.is_supplement))
            )
            if not _decisions_agree(item.primary, item.secondary):
                disagreements += 1
                if item.adjudication is None:
                    failures.append(f"ad {item.ad_id}: reviewer disagreement requires adjudication")
                    continue
                adjudication_error = _decision_error(item.adjudication, "adjudication")
                if adjudication_error:
                    failures.append(f"ad {item.ad_id}: {adjudication_error}")
                    continue
                reviewer_ids = {item.primary.reviewer_id, item.secondary.reviewer_id}
                if item.adjudication.reviewer_id in reviewer_ids:
                    failures.append(f"ad {item.ad_id}: adjudicator must be an independent reviewer")
                    continue
                if not item.adjudication.rationale.strip():
                    failures.append(f"ad {item.ad_id}: adjudication rationale is required")
                    continue
                final = item.adjudication
                source = "adjudication"
                adjudicated += 1
        final_labels.append(
            {
                "ad_id": item.ad_id,
                "is_supplement": final.is_supplement,
                "supplement_subcategory": final.supplement_subcategory,
                "notes": final.notes,
                "taxonomy_provenance": {
                    "source": source,
                    "reviewer_id": final.reviewer_id,
                    "reviewed_at": final.reviewed_at,
                    "review_schema_version": SCHEMA_VERSION,
                    "sample_sha256": review.sample_sha256,
                },
            }
        )

    n_expected = len(expected_ids)
    double_fraction = len(double_pairs) / n_expected if n_expected else 0.0
    if double_fraction < MIN_DOUBLE_REVIEW_FRACTION:
        failures.append(
            "double-review coverage "
            f"{double_fraction:.3f} is below {MIN_DOUBLE_REVIEW_FRACTION:.3f}"
        )
    kappa, observed_agreement = _cohen_kappa(double_pairs)
    if kappa is None:
        failures.append("Cohen's kappa is not estimable; double reviews need both label classes")
    elif kappa < MIN_COHEN_KAPPA:
        failures.append(f"Cohen's kappa {kappa:.3f} is below {MIN_COHEN_KAPPA:.3f}")
    if disagreements != adjudicated:
        failures.append("not every reviewer disagreement has valid independent adjudication")
    if len(final_labels) != n_expected:
        failures.append(f"only {len(final_labels)} of {n_expected} sampled ads have final labels")

    passed = not failures
    approved = [row for row in final_labels if row["is_supplement"] is True] if passed else []
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "computed_at": datetime.now(tz=UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "sample_sha256": review.sample_sha256,
        "review_artifact_sha256": _content_sha256(review_payload),
        "approved_labels_content_sha256": _content_sha256(approved),
        "n_sampled": n_expected,
        "n_final_labels": len(final_labels),
        "n_approved_supplements": len(approved),
        "n_double_reviewed": len(double_pairs),
        "double_review_fraction": round(double_fraction, 4),
        "minimum_double_review_fraction": MIN_DOUBLE_REVIEW_FRACTION,
        "observed_binary_agreement": round(observed_agreement, 4),
        "cohen_kappa": round(kappa, 4) if kappa is not None else None,
        "minimum_cohen_kappa": MIN_COHEN_KAPPA,
        "n_disagreements": disagreements,
        "n_adjudicated": adjudicated,
        "failures": failures,
    }
    return report, approved


def verify_approved_taxonomy(
    report_payload: dict[str, Any], approved_labels: list[dict[str, Any]]
) -> None:
    """Reject labels not bound to a passing adjudication report."""
    if report_payload.get("report_schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("taxonomy adjudication report schema is unsupported")
    if report_payload.get("status") != "passed":
        raise ValueError("taxonomy adjudication report did not pass")
    if report_payload.get("approved_labels_content_sha256") != _content_sha256(approved_labels):
        raise ValueError("approved taxonomy labels do not match the adjudication report")
    if len(approved_labels) != report_payload.get("n_approved_supplements"):
        raise ValueError("approved taxonomy label count does not match the report")
    for row in approved_labels:
        provenance = row.get("taxonomy_provenance") or {}
        if row.get("is_supplement") is not True:
            raise ValueError("approved taxonomy file may contain only explicit Supplements labels")
        if provenance.get("sample_sha256") != report_payload.get("sample_sha256"):
            raise ValueError("approved taxonomy label is missing matching sample provenance")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or validate Supplements taxonomy review.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="Create the versioned human-review artifact")
    init.add_argument("--sample", type=Path, required=True)
    init.add_argument("--template", type=Path, required=True)
    init.add_argument("--out", type=Path, required=True)
    validate = subparsers.add_parser("validate", help="Apply M1 and publish approved labels")
    validate.add_argument("--sample", type=Path, required=True)
    validate.add_argument("--review", type=Path, required=True)
    validate.add_argument("--report", type=Path, required=True)
    validate.add_argument("--approved-labels", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.sample, args.template if args.command == "init" else args.review):
        if not path.exists():
            parser.error(f"input not found: {path}")
    if args.command == "init":
        with exclusive_output(args.out):
            atomic_write_json(args.out, create_review_artifact(args.sample, args.template))
        print(f"Created taxonomy review artifact: {args.out}")
        return

    review_payload = json.loads(args.review.read_text())
    report, approved = validate_review_artifact(args.sample, review_payload)
    with exclusive_output(args.report), exclusive_output(args.approved_labels):
        atomic_write_json(args.report, report)
        atomic_write_json(args.approved_labels, approved)
    print(
        f"Taxonomy adjudication {report['status']}: approved={len(approved)} report={args.report}"
    )
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
