"""Phase 0: Product category validation for supplement ads."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from pipeline.validation.product_classifier import batch_classify_ads


def filter_ads_with_images(all_ads: list[dict]) -> list[dict]:
    """
    Keep only ads with at least one usable image.

    Video ads and ads with neither image_urls nor video_urls are out of
    scope: v1 does not extract or model video creative, and zero-media ads
    have no creative to classify or extract features from either way.
    """
    return [ad for ad in all_ads if ad.get("image_urls")]


_REVIEW_PATTERNS = {
    "possible_pet": re.compile(r"\b(dog|cat|pet|canine|feline|puppy)\b", re.I),
    "possible_topical": re.compile(
        r"\b(serum|cream|balm|moisturi[sz]er|face scrub|topical|wrinkle)\b", re.I
    ),
    "possible_equipment": re.compile(
        r"\b(shaker bottle|blenderbottle|gym equipment|weighted sled)\b", re.I
    ),
}
_SUBCATEGORY_REVIEW_PATTERNS = {
    "collagen": re.compile(r"\bcollagen\b", re.I),
    "creatine": re.compile(r"\bcreatine\b", re.I),
    "magnesium": re.compile(r"\bmagnesium\b", re.I),
    "melatonin_sleep": re.compile(r"\b(melatonin|sleep (?:aid|support|gumm))\b", re.I),
    "multivitamin": re.compile(r"\b(multivitamin|multi-vitamin)\b", re.I),
    "omega_3": re.compile(r"\b(omega[- ]?3|fish oil)\b", re.I),
    "pre_workout": re.compile(r"\bpre[- ]?workout\b", re.I),
    "probiotic": re.compile(r"\bprobiotic\b", re.I),
    "protein": re.compile(r"\b(protein powder|whey protein|plant protein)\b", re.I),
    "vitamin": re.compile(r"\bvitamin(?:s| [a-z0-9]+)?\b", re.I),
    "weight_management": re.compile(r"\b(weight loss|fat burn|appetite)\b", re.I),
}


def taxonomy_review_strata(ad: dict) -> set[str]:
    """Return provenance/risk strata for manual sampling, never model labels."""
    text = " ".join(str(ad.get(key) or "") for key in ("page_name", "title", "body"))
    strata = {f"query:{str(query).lower()}" for query in ad.get("search_queries") or [] if query}
    strata.update(name for name, pattern in _REVIEW_PATTERNS.items() if pattern.search(text))
    # Older corpora predate per-ad query provenance. Text-derived strata are
    # sampling aids only and must never be treated as taxonomy ground truth.
    strata.update(
        f"candidate:{name}"
        for name, pattern in _SUBCATEGORY_REVIEW_PATTERNS.items()
        if pattern.search(text)
    )
    return strata or {"query:unknown"}


def stratified_validation_sample(
    ads: list[dict], sample_size: int = 120, seed: int = 42
) -> list[dict]:
    """Round-robin across query provenance and contamination-risk strata."""
    eligible = filter_ads_with_images(ads)
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = {}
    for ad in eligible:
        for stratum in taxonomy_review_strata(ad):
            buckets.setdefault(stratum, []).append(ad)
    for rows in buckets.values():
        rng.shuffle(rows)

    selected: list[dict] = []
    seen: set[str] = set()
    ordered_strata = sorted(buckets)
    while len(selected) < min(sample_size, len(eligible)):
        progressed = False
        for stratum in ordered_strata:
            while buckets[stratum]:
                ad = buckets[stratum].pop()
                ad_id = str(ad.get("ad_archive_id") or "")
                if ad_id and ad_id not in seen:
                    selected.append(ad)
                    seen.add(ad_id)
                    progressed = True
                    break
            if len(selected) >= sample_size:
                break
        if not progressed:
            break
    return selected


def create_validation_sample(
    input_json: str,
    sample_size: int = 120,
    seed: int = 42,
    stratified: bool = True,
) -> dict:
    """
    Create a stratified validation sample from scraped ads by default.

    Only ads with at least one image are eligible (video/zero-media ads are
    out of scope — see filter_ads_with_images).

    Returns dict with:
      - sample: list of ad dicts
      - count: number of ads sampled
      - total: total ads in dataset (post image-filter)
      - excluded_no_image: number of ads dropped for lacking images
    """
    print(f"Loading ads from {input_json}...")
    with open(input_json) as f:
        all_ads = json.load(f)

    raw_total = len(all_ads)
    image_ads = filter_ads_with_images(all_ads)
    excluded = raw_total - len(image_ads)
    print(
        f"✅ Loaded {raw_total} ads ({len(image_ads)} with images, "
        f"{excluded} excluded — video-only or no media)"
    )

    total = len(image_ads)
    sample = (
        stratified_validation_sample(image_ads, sample_size, seed)
        if stratified
        else random.Random(seed).sample(image_ads, min(sample_size, total))
    )
    print(f"✅ Sampled {len(sample)} image ads for validation")

    return {
        "sample": sample,
        "count": len(sample),
        "total": total,
        "excluded_no_image": excluded,
    }


def save_validation_sample(
    sample_data: dict,
    output_json: str,
) -> None:
    """Save validation sample to JSON for manual labeling."""
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(sample_data["sample"], f, indent=2)

    print(f"✅ Saved {sample_data['count']} validation ads to {output_json}")
    print("   Please manually label as 'supplement' or 'non-supplement' in ground_truth.json")


def create_ground_truth_template(
    input_json: str,
    output_json: str,
) -> None:
    """Create template for manual ground truth labels."""
    with open(input_json) as f:
        sample = json.load(f)

    # Create template with empty labels
    template = [
        {
            "ad_index": i,
            "ad_id": str(ad.get("ad_archive_id") or ""),
            "page_id": str(ad.get("page_id") or ""),
            "page_name": ad.get("page_name", ""),
            "title": ad.get("title", "")[:50],
            "body": ad.get("body", "")[:50],
            "cta_text": ad.get("cta_text", ""),
            "search_queries": ad.get("search_queries", []),
            "review_strata": sorted(taxonomy_review_strata(ad)),
            "snapshot_url": ad.get("snapshot_url", ""),
            "is_supplement": None,  # User fills this: true or false
            "supplement_subcategory": None,
            "notes": "",
        }
        for i, ad in enumerate(sample)
    ]

    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(template, f, indent=2)

    print(f"✅ Created ground truth template: {output_json}")
    print("   Instructions: Fill 'is_supplement' (true/false) for each ad")


def classify_validation_sample(
    sample_json: str,
    checkpoint_path: str | None = None,
    resume: bool = False,
    max_workers: int = 1,
    checkpoint_every: int = 3,
) -> list[dict]:
    """Classify the validation sample using LLM.

    If checkpoint_path is given, progress is flushed to disk periodically.
    With resume=True, ads already present at checkpoint_path (matched by
    ad_archive_id) are skipped instead of re-classified — useful if a
    previous run was interrupted (killed process, session teardown).
    """
    print(f"\nClassifying {sample_json} with Gemini Flash...")

    with open(sample_json) as f:
        sample = json.load(f)

    already_classified = {}
    if resume and checkpoint_path and Path(checkpoint_path).exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        already_classified = {ad["ad_archive_id"]: ad for ad in prior if ad.get("ad_archive_id")}
        print(f"   Resuming: {len(already_classified)} ads already classified in {checkpoint_path}")

    classified = batch_classify_ads(
        sample,
        batch_size=10,
        checkpoint_path=checkpoint_path,
        checkpoint_every=checkpoint_every,
        already_classified=already_classified,
        max_workers=max_workers,
    )
    print(f"✅ Classified {len(classified)} ads")

    return classified


def evaluate_classification_accuracy(
    classified_ads: list[dict],
    ground_truth_json: str,
) -> dict:
    """
    Evaluate LLM classification against manual ground truth.

    Returns dict with:
      - accuracy: float (0.0-1.0)
      - precision: float (true positives / predicted positives)
      - recall: float (true positives / actual positives)
      - confusion_matrix: dict with TP, FP, TN, FN
      - borderline_cases: list of ads with 0.6-0.8 confidence
    """
    print(f"\nEvaluating against ground truth: {ground_truth_json}")

    with open(ground_truth_json) as f:
        ground_truth = json.load(f)

    if not ground_truth or not any(gt.get("is_supplement") is not None for gt in ground_truth):
        print("❌ Ground truth labels not yet filled. Cannot evaluate.")
        print(f"   Please label {ground_truth_json} first.")
        return None

    # Prefer stable ad IDs. Keep positional fallback for old templates.
    gt_by_id = {
        str(gt["ad_id"]): gt["is_supplement"]
        for gt in ground_truth
        if gt.get("ad_id") and gt.get("is_supplement") is not None
    }
    gt_by_index = {
        gt["ad_index"]: gt["is_supplement"]
        for gt in ground_truth
        if not gt.get("ad_id") and gt.get("is_supplement") is not None
    }

    if not gt_by_id and not gt_by_index:
        print("❌ No ground truth labels found.")
        return None

    # Evaluate predictions
    tp = fp = tn = fn = 0
    borderline = []

    for i, ad in enumerate(classified_ads):
        ad_id = str(ad.get("ad_archive_id") or "")
        if ad_id in gt_by_id:
            true_is_supplement = gt_by_id[ad_id]
        elif i in gt_by_index:
            true_is_supplement = gt_by_index[i]
        else:
            continue

        pred_is_supplement = ad["classification"]["is_supplement"]
        confidence = ad["classification"]["confidence"]

        if 0.6 <= confidence < 0.8:
            borderline.append(
                {
                    "ad_index": i,
                    "title": ad.get("title", "")[:50],
                    "confidence": confidence,
                    "prediction": pred_is_supplement,
                    "ground_truth": true_is_supplement,
                }
            )

        if pred_is_supplement and true_is_supplement:
            tp += 1
        elif pred_is_supplement and not true_is_supplement:
            fp += 1
        elif not pred_is_supplement and true_is_supplement:
            fn += 1
        else:  # not pred and not true
            tn += 1

    total = tp + fp + tn + fn
    accuracy = (tp + tn) / total if total > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0

    results = {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "confusion_matrix": {
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
        },
        "borderline_cases": borderline,
        "total_evaluated": total,
    }

    print("\n📊 Classification Accuracy Results:")
    print(f"   Accuracy:  {accuracy:.2%}")
    print(f"   Precision: {precision:.2%}")
    print(f"   Recall:    {recall:.2%}")
    print(f"   Evaluated: {total} ads")
    if borderline:
        print(f"   Borderline cases (0.6-0.8 confidence): {len(borderline)}")

    return results


def apply_manual_taxonomy_gate(
    ads: list[dict],
    labels: list[dict],
    adjudication_report: dict | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Fail closed: only explicitly human-approved Supplements ads pass."""
    labels_by_id = {
        str(row.get("ad_id") or ""): row
        for row in labels
        if row.get("ad_id") and row.get("is_supplement") is not None
    }
    accepted = []
    counts = {"accepted": 0, "rejected": 0, "unlabeled": 0}
    for ad in ads:
        label = labels_by_id.get(str(ad.get("ad_archive_id") or ""))
        if label is None:
            counts["unlabeled"] += 1
        elif label["is_supplement"] is True:
            provenance = dict(label.get("taxonomy_provenance") or {})
            if adjudication_report is not None:
                provenance.update(
                    {
                        "adjudication_report_schema_version": adjudication_report.get(
                            "report_schema_version"
                        ),
                        "approved_labels_content_sha256": adjudication_report.get(
                            "approved_labels_content_sha256"
                        ),
                    }
                )
            accepted.append(
                {
                    **ad,
                    "taxonomy_label": {
                        "source": "human_adjudication",
                        "is_supplement": True,
                        "supplement_subcategory": label.get("supplement_subcategory"),
                        "notes": label.get("notes", ""),
                        "provenance": provenance,
                    },
                }
            )
            counts["accepted"] += 1
        else:
            counts["rejected"] += 1
    return accepted, counts


def apply_product_filter(
    input_json: str,
    output_json: str,
    confidence_threshold: float = 0.6,
    checkpoint_path: str | None = None,
    resume: bool = False,
    max_workers: int = 1,
    checkpoint_every: int = 20,
) -> dict:
    """
    Apply product classification to full dataset.
    Filters to supplement products only.

    checkpoint_path/resume/max_workers work the same as
    classify_validation_sample — see batch_classify_ads for details. For a
    dataset this size (thousands of ads), max_workers > 1 and a non-None
    checkpoint_path are strongly recommended.

    Returns dict with:
      - filtered_count: number of supplement ads
      - non_supplement_count: number of non-supplement ads
      - borderline_count: number of borderline (0.6-0.8 confidence) ads
      - output_file: path to filtered output JSON
    """
    print("\n🔄 Applying product filter to full dataset...")
    print(f"   Input: {input_json}")

    with open(input_json) as f:
        raw_ads = json.load(f)

    all_ads = filter_ads_with_images(raw_ads)
    excluded_no_image = len(raw_ads) - len(all_ads)
    print(
        f"   Total ads: {len(raw_ads)} ({len(all_ads)} with images, "
        f"{excluded_no_image} excluded — video-only or no media)"
    )

    already_classified = {}
    if resume and checkpoint_path and Path(checkpoint_path).exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        already_classified = {ad["ad_archive_id"]: ad for ad in prior if ad.get("ad_archive_id")}
        print(f"   Resuming: {len(already_classified)} ads already classified in {checkpoint_path}")

    # Classify all ads
    classified = batch_classify_ads(
        all_ads,
        checkpoint_path=checkpoint_path,
        checkpoint_every=checkpoint_every,
        already_classified=already_classified,
        max_workers=max_workers,
    )

    # Separate by classification
    supplements = []
    non_supplements = []
    borderline = []

    for ad in classified:
        classification = ad.get("classification", {})
        is_supplement = classification.get("is_supplement", False)
        confidence = classification.get("confidence", 0.5)

        if 0.6 <= confidence < 0.8:
            borderline.append(ad)
        elif is_supplement:
            supplements.append(ad)
        else:
            non_supplements.append(ad)

    # Save filtered dataset
    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(supplements, f, indent=2)

    print("\n✅ Phase 0 Complete!")
    print(f"   Supplements: {len(supplements)} ads ({len(supplements) / len(all_ads):.1%})")
    print(
        f"   Non-supplements: {len(non_supplements)} ads "
        f"({len(non_supplements) / len(all_ads):.1%})"
    )
    print(
        f"   Borderline (0.6-0.8 confidence): {len(borderline)} ads "
        f"({len(borderline) / len(all_ads):.1%})"
    )
    print(f"   Saved to: {output_json}")

    return {
        "filtered_count": len(supplements),
        "non_supplement_count": len(non_supplements),
        "borderline_count": len(borderline),
        "excluded_no_image": excluded_no_image,
        "output_file": str(output_path),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.validation.phase0_validator <command>")
        print("  Commands:")
        print("    sample <input_json> - Create 120-ad stratified validation set")
        print("    classify <sample_json> - Classify validation sample")
        print("    evaluate <sample_json> <ground_truth_json> - Evaluate accuracy")
        print("    apply <input_json> - Apply filter to full dataset")
        sys.exit(1)

    DATA_DIR = Path(__file__).parent.parent.parent / "data"

    command = sys.argv[1]

    def _max_workers_arg(default: int = 1) -> int:
        for arg in sys.argv:
            if arg.startswith("--workers="):
                return int(arg.split("=", 1)[1])
        return default

    if command == "sample":
        input_json = sys.argv[2] if len(sys.argv) > 2 else str(DATA_DIR / "supplements_ads.json")
        data = create_validation_sample(input_json)
        save_validation_sample(
            data,
            str(DATA_DIR / "supplements_validation_120.json"),
        )
        create_ground_truth_template(
            str(DATA_DIR / "supplements_validation_120.json"),
            str(DATA_DIR / "supplements_ground_truth_template.json"),
        )

    if command == "classify":
        sample_json = (
            sys.argv[2] if len(sys.argv) > 2 else str(DATA_DIR / "supplements_validation_100.json")
        )
        # Derive output path from the input filename so classifying a
        # different/test sample can't silently clobber another run's output
        # (this overwrote the validated 100-sample result once already).
        output_path = Path(sample_json).with_name(Path(sample_json).stem + "_classified.json")
        resume = "--resume" in sys.argv
        classified = classify_validation_sample(
            sample_json,
            checkpoint_path=str(output_path),
            resume=resume,
            max_workers=_max_workers_arg(),
        )
        print(f"✅ Saved classified results to {output_path}")

    elif command == "evaluate":
        classified_json = (
            sys.argv[2]
            if len(sys.argv) > 2
            else str(DATA_DIR / "supplements_validation_100_classified.json")
        )
        ground_truth_json = (
            sys.argv[3] if len(sys.argv) > 3 else str(DATA_DIR / "supplements_ground_truth.json")
        )
        with open(classified_json) as f:
            classified_ads = json.load(f)
        evaluate_classification_accuracy(classified_ads, ground_truth_json)

    elif command == "apply":
        input_json = sys.argv[2] if len(sys.argv) > 2 else str(DATA_DIR / "supplements_ads.json")
        resume = "--resume" in sys.argv
        output_path = DATA_DIR / "supplements_filtered.json"
        checkpoint_path = DATA_DIR / "supplements_filtered_classified_checkpoint.json"
        apply_product_filter(
            input_json,
            str(output_path),
            checkpoint_path=str(checkpoint_path),
            resume=resume,
            max_workers=_max_workers_arg(),
        )
