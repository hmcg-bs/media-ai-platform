"""Build a human-review queue for successful Supplements-ad craft references."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.artifacts import atomic_write_json
from pipeline.model_training.success_score import SuccessScoreCalibrator


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_benchmark_review(
    ads: list[dict[str, Any]],
    matrix: list[dict[str, Any]],
    taxonomy_labels: list[dict[str, Any]],
    candidate_count: int = 24,
    minimum_days_active: int = 30,
) -> list[dict[str, Any]]:
    """Rank approved Supplements ads, diversifying pages before refill.

    Proxy rank establishes performance evidence only. Human review remains
    mandatory because model success does not establish visual craft quality.
    """
    approved_ids = {
        str(row["ad_id"])
        for row in taxonomy_labels
        if row.get("ad_id") and row.get("is_supplement") is True
    }
    ad_by_id = {str(ad.get("ad_archive_id") or ""): ad for ad in ads}
    eligible = [
        row
        for row in matrix
        if str(row.get("ad_id") or "") in approved_ids
        and float(row.get("days_active") or 0) >= minimum_days_active
        and ad_by_id.get(str(row.get("ad_id") or ""), {}).get("image_urls")
    ]
    scored = SuccessScoreCalibrator(matrix).transform(eligible) if eligible else []
    scored.sort(key=lambda row: (-float(row["composite_success_score"]), str(row["ad_id"])))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    pages: set[str] = set()
    for require_new_page in (True, False):
        for row in scored:
            ad_id = str(row["ad_id"])
            page_id = str(row.get("page_id") or f"__ad__{ad_id}")
            if ad_id in selected_ids or (require_new_page and page_id in pages):
                continue
            ad = ad_by_id[ad_id]
            selected.append(
                {
                    "ad_id": ad_id,
                    "page_id": str(ad.get("page_id") or ""),
                    "page_name": ad.get("page_name") or "",
                    "product_subcategory": row.get("product_subcategory") or "unknown",
                    "days_active": row.get("days_active"),
                    "collation_count": row.get("collation_count"),
                    "brand_scaling_count": row.get("brand_scaling_count"),
                    "composite_success_score": round(float(row["composite_success_score"]), 5),
                    "image_url": ad["image_urls"][0],
                    "snapshot_url": ad.get("snapshot_url") or "",
                    "approved_for_craft_reference": None,
                    "review_notes": "",
                }
            )
            selected_ids.add(ad_id)
            pages.add(page_id)
            if len(selected) >= candidate_count:
                return selected
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Create benchmark-image human review queue.")
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--taxonomy-labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/supplements_benchmark_review.json"))
    parser.add_argument("--candidate-count", type=int, default=24)
    parser.add_argument("--minimum-days-active", type=int, default=30)
    args = parser.parse_args()
    if args.candidate_count < 8:
        parser.error("--candidate-count must be at least 8")

    candidates = build_benchmark_review(
        json.loads(args.ads.read_text()),
        json.loads(args.matrix.read_text()),
        json.loads(args.taxonomy_labels.read_text()),
        candidate_count=args.candidate_count,
        minimum_days_active=args.minimum_days_active,
    )
    output = {
        "schema_version": "supplements-benchmark-review-v1",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "inputs": {
            "ads_sha256": _sha256(args.ads),
            "matrix_sha256": _sha256(args.matrix),
            "taxonomy_labels_sha256": _sha256(args.taxonomy_labels),
        },
        "selection": {
            "candidate_count": len(candidates),
            "minimum_days_active": args.minimum_days_active,
            "ranking": "composite_success_score; unique advertisers before refill",
            "maximum_approved_references": 8,
        },
        "usage_constraints": [
            "Craft-quality comparison only across art direction, imagery quality, "
            "typography, visual hierarchy, composition, and brand cohesion.",
            "Never copy style, pixels, product, wording, people, scene, or layout.",
            "A proxy-ranked candidate is not usable until "
            "approved_for_craft_reference is explicitly true.",
            "References are not evidence for model directives; directive_aligned is always false.",
        ],
        "candidates": candidates,
    }
    atomic_write_json(args.out, output)
    print(f"Wrote {len(candidates)} review candidates to {args.out}")


if __name__ == "__main__":
    main()
