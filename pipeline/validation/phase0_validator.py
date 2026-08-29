"""Phase 0: Product category validation for supplement ads."""

from __future__ import annotations

import json
import random
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


def create_validation_sample(
    input_json: str,
    sample_size: int = 100,
    seed: int = 42,
) -> dict:
    """
    Create a random validation sample from scraped ads.

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
    print(f"✅ Loaded {raw_total} ads ({len(image_ads)} with images, {excluded} excluded — video-only or no media)")

    total = len(image_ads)
    random.seed(seed)
    sample = random.sample(image_ads, min(sample_size, total))
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

    with open(output_path, 'w') as f:
        json.dump(sample_data["sample"], f, indent=2)

    print(f"✅ Saved {sample_data['count']} validation ads to {output_json}")
    print(f"   Please manually label as 'supplement' or 'non-supplement' in ground_truth.json")


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
            "title": ad.get("title", "")[:50],
            "body": ad.get("body", "")[:50],
            "cta_text": ad.get("cta_text", ""),
            "is_supplement": None,  # User fills this: true or false
            "notes": "",
        }
        for i, ad in enumerate(sample)
    ]

    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(template, f, indent=2)

    print(f"✅ Created ground truth template: {output_json}")
    print(f"   Instructions: Fill 'is_supplement' (true/false) for each ad")


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
        already_classified = {
            ad["ad_archive_id"]: ad for ad in prior if ad.get("ad_archive_id")
        }
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

    if not ground_truth or not ground_truth[0].get("is_supplement") is not None:
        print("❌ Ground truth labels not yet filled. Cannot evaluate.")
        print(f"   Please label {ground_truth_json} first.")
        return None

    # Build lookup: ad_index -> is_supplement (ground truth)
    gt_lookup = {
        gt["ad_index"]: gt["is_supplement"]
        for gt in ground_truth
        if gt["is_supplement"] is not None
    }

    if not gt_lookup:
        print("❌ No ground truth labels found.")
        return None

    # Evaluate predictions
    tp = fp = tn = fn = 0
    borderline = []

    for i, ad in enumerate(classified_ads[:len(gt_lookup)]):
        if i not in gt_lookup:
            continue

        pred_is_supplement = ad["classification"]["is_supplement"]
        true_is_supplement = gt_lookup[i]
        confidence = ad["classification"]["confidence"]

        if 0.6 <= confidence < 0.8:
            borderline.append({
                "ad_index": i,
                "title": ad.get("title", "")[:50],
                "confidence": confidence,
                "prediction": pred_is_supplement,
                "ground_truth": true_is_supplement,
            })

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

    print(f"\n📊 Classification Accuracy Results:")
    print(f"   Accuracy:  {accuracy:.2%}")
    print(f"   Precision: {precision:.2%}")
    print(f"   Recall:    {recall:.2%}")
    print(f"   Evaluated: {total} ads")
    if borderline:
        print(f"   Borderline cases (0.6-0.8 confidence): {len(borderline)}")

    return results


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
    print(f"\n🔄 Applying product filter to full dataset...")
    print(f"   Input: {input_json}")

    with open(input_json) as f:
        raw_ads = json.load(f)

    all_ads = filter_ads_with_images(raw_ads)
    excluded_no_image = len(raw_ads) - len(all_ads)
    print(f"   Total ads: {len(raw_ads)} ({len(all_ads)} with images, {excluded_no_image} excluded — video-only or no media)")

    already_classified = {}
    if resume and checkpoint_path and Path(checkpoint_path).exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        already_classified = {
            ad["ad_archive_id"]: ad for ad in prior if ad.get("ad_archive_id")
        }
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

    with open(output_path, 'w') as f:
        json.dump(supplements, f, indent=2)

    print(f"\n✅ Phase 0 Complete!")
    print(f"   Supplements: {len(supplements)} ads ({len(supplements)/len(all_ads):.1%})")
    print(f"   Non-supplements: {len(non_supplements)} ads ({len(non_supplements)/len(all_ads):.1%})")
    print(f"   Borderline (0.6-0.8 confidence): {len(borderline)} ads ({len(borderline)/len(all_ads):.1%})")
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
        print("    sample <input_json> - Create 100-sample validation set")
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
            str(DATA_DIR / "supplements_validation_100.json"),
        )
        create_ground_truth_template(
            str(DATA_DIR / "supplements_validation_100.json"),
            str(DATA_DIR / "supplements_ground_truth_template.json"),
        )

    if command == "classify":
        sample_json = sys.argv[2] if len(sys.argv) > 2 else str(DATA_DIR / "supplements_validation_100.json")
        # Derive output path from the input filename so classifying a
        # different/test sample can't silently clobber another run's output
        # (this overwrote the validated 100-sample result once already).
        output_path = Path(sample_json).with_name(
            Path(sample_json).stem + "_classified.json"
        )
        resume = "--resume" in sys.argv
        classified = classify_validation_sample(
            sample_json,
            checkpoint_path=str(output_path),
            resume=resume,
            max_workers=_max_workers_arg(),
        )
        print(f"✅ Saved classified results to {output_path}")

    elif command == "evaluate":
        classified_json = sys.argv[2] if len(sys.argv) > 2 else str(DATA_DIR / "supplements_validation_100_classified.json")
        ground_truth_json = sys.argv[3] if len(sys.argv) > 3 else str(DATA_DIR / "supplements_ground_truth.json")
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
