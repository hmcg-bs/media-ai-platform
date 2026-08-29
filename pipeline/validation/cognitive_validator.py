"""Stage 5 cognitive-extraction eval: OCR headline correctness, hook_framework
classification accuracy, human_presence/model_count accuracy.

Scope finalized via wayfinder ticket #33 (see the map at GitHub issue #1):
- OCR: is typography_hierarchy.primary_headline actually the ad's real
  visual headline? A single Y/N judgment per ad.
- Hooks: hook_framework (7-class categorization).
- Image: human_presence (Y/N) + model_count.
- Color explicitly excluded — 100% deterministic k-means/luminance math, no
  probabilistic model involved; covered by pipeline/tests/test_stage_03_color.py
  instead, not this framework.

Follows pipeline/validation/phase0_validator.py's established pattern
(sample -> manual label -> evaluate), but the golden set's predicted_* values
come from the 1,250 ads already processed by out/step2/ during the real
production run — no new Gemini calls needed to build it. Ground truth is
filled in by the user with the labeling UI (cognitive_validator.html), not
agent visual-inspection (an earlier draft approach superseded during
wayfinder grilling — the user explicitly wants to do the labeling
themselves).
"""

from __future__ import annotations

import base64
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ingestion.download import _get_extension_from_url
from ingestion.run_step2_pipeline import fetch_image_bytes
from pipeline.validation import eval_core

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STEP2_OUT_DIR = Path(__file__).parent.parent.parent / "out" / "step2"
DEFAULT_ADS_FILE = DATA_DIR / "supplements_enriched.json"
DEFAULT_GOLDEN_SET_PATH = DATA_DIR / "cognitive_golden_set.json"

_EXT_TO_MIME = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}

# Matches HookFramework's real enum values (pipeline/models/output_schema.py).
HOOK_FRAMEWORK_CATEGORIES = (
    "Unknown",
    "Direct Offer",
    "Social Proof",
    "Before/After",
    "PAS",
    "Testimonial",
    "AIDA",
)


def load_step2_predictions(step2_out_dir: Path) -> dict[str, dict[str, Any]]:
    """Reads every <ad_id>.json in step2_out_dir, extracts just the fields
    this eval cares about. Ads whose file fails to parse are skipped, not
    dropped from any caller's larger accounting — this function only ever
    returns what it could actually read."""
    predictions: dict[str, dict[str, Any]] = {}
    for path in sorted(step2_out_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        ad_id = data.get("ad_id")
        if not ad_id:
            continue
        typography = data.get("typography_hierarchy") or {}
        headline = (typography.get("primary_headline") or {}).get("text", "")
        secondary_copy = [
            b.get("text", "") for b in (typography.get("secondary_copy") or [])
        ]
        marketing = data.get("marketing_psychology") or {}
        human = data.get("human_model_analysis") or {}
        predictions[ad_id] = {
            "predicted_headline": headline,
            "predicted_secondary_copy": secondary_copy,
            "predicted_hook_framework": marketing.get("hook_framework", "Unknown"),
            "predicted_human_presence": human.get("human_presence", False),
            "predicted_model_count": human.get("model_count", 0),
        }
    return predictions


def _to_data_uri(image_bytes: bytes, url: str) -> str:
    ext = _get_extension_from_url(url)
    mime = _EXT_TO_MIME.get(ext, "jpeg")
    return f"data:image/{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def build_golden_set(
    ads_file: Path = DEFAULT_ADS_FILE,
    step2_out_dir: Path = STEP2_OUT_DIR,
    per_category: int = 6,
    seed: int = 42,
    fetch_fn: Callable[[str], bytes | None] = fetch_image_bytes,
    existing_golden_set: list[dict[str, Any]] | None = None,
    priority_ad_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Stratified sample across hook_framework's 7 categories, up to
    `per_category` ads per category (fewer if a category has less
    available). predicted_* pre-filled from real, already-computed Step 2
    output — no new Gemini calls. expected_* left None for the user to fill
    in via the labeling UI.

    Each sampled ad's image is fetched and embedded as a base64 data URI
    (image_data_uri) rather than left as a remote image_urls reference —
    confirmed live that hot-linking Facebook's CDN directly from a file://
    page fails in-browser (ad-blockers / file:// origin restrictions / CDN
    URL expiry over a multi-day labeling session) even though the same URLs
    fetch fine server-side.

    Resumable: pass `existing_golden_set` (e.g. loaded from a prior partial
    run) and only the shortfall per category is fetched — already-embedded
    ads are kept as-is and never re-fetched. Confirmed live as necessary:
    Facebook's CDN fetch success rate degraded sharply over a long session
    (thousands of prior requests), so a single `sample` pass can leave
    several categories short; re-running `sample` tops up instead of
    reattempting everything from scratch. `fetch_fn` is injectable for
    offline tests.

    Returns (golden_set, fetched_ok_count, fetch_failed_count) — the last
    two describe just this call's own fetch attempts, for reporting a live
    success-rate signal back to the caller.

    `priority_ad_ids`, when given, are tried before the rest of a category's
    candidates (each tier still shuffled internally). Confirmed live as a
    real efficiency win: image_urls refreshed via a fresh Apify re-scrape
    (ingestion/refresh_image_urls.py) succeed far more often than the
    corpus's original, months-stale URLs — trying refreshed ad_ids first
    means the fetch budget isn't spent working through the stale majority
    before reaching ones known likelier to succeed."""
    predictions = load_step2_predictions(step2_out_dir)
    ads = json.loads(ads_file.read_text())
    ads_by_id = {a.get("ad_archive_id"): a for a in ads}

    existing_golden_set = existing_golden_set or []
    golden_set: list[dict[str, Any]] = list(existing_golden_set)
    already_have_ids: set[str] = {e["ad_id"] for e in existing_golden_set}
    already_have_by_category: dict[str, int] = {}
    for entry in existing_golden_set:
        cat = entry["predicted_hook_framework"]
        already_have_by_category[cat] = already_have_by_category.get(cat, 0) + 1

    by_category: dict[str, list[str]] = {cat: [] for cat in HOOK_FRAMEWORK_CATEGORIES}
    for ad_id, pred in predictions.items():
        category = pred["predicted_hook_framework"]
        if (
            category in by_category
            and ad_id not in already_have_ids
            and ads_by_id.get(ad_id, {}).get("image_urls")
        ):
            by_category[category].append(ad_id)

    rng = random.Random(seed)
    fetched_ok = 0
    fetch_failed = 0
    for category, ad_ids in by_category.items():
        shortfall = per_category - already_have_by_category.get(category, 0)
        if shortfall <= 0:
            continue
        # Try every untried candidate in the category, until the shortfall
        # is filled or the pool is exhausted -- unlike a fixed oversample
        # buffer, this makes real progress even at a low success rate
        # instead of giving up after a small fixed number of attempts.
        # Priority ad_ids (if any) go first, each tier shuffled internally.
        if priority_ad_ids:
            priority = [a for a in ad_ids if a in priority_ad_ids]
            rest = [a for a in ad_ids if a not in priority_ad_ids]
            rng.shuffle(priority)
            rng.shuffle(rest)
            candidate_ids = priority + rest
        else:
            candidate_ids = ad_ids[:]
            rng.shuffle(candidate_ids)
        added_for_category = 0
        for ad_id in candidate_ids:
            if added_for_category >= shortfall:
                break
            ad = ads_by_id[ad_id]
            url = ad["image_urls"][0]
            image_bytes = fetch_fn(url)
            if image_bytes is None:
                fetch_failed += 1
                continue
            fetched_ok += 1
            pred = predictions[ad_id]
            golden_set.append(
                {
                    "ad_id": ad_id,
                    "image_data_uri": _to_data_uri(image_bytes, url),
                    "predicted_headline": pred["predicted_headline"],
                    "predicted_secondary_copy": pred["predicted_secondary_copy"],
                    "predicted_hook_framework": pred["predicted_hook_framework"],
                    "predicted_human_presence": pred["predicted_human_presence"],
                    "predicted_model_count": pred["predicted_model_count"],
                    # Real Facebook ad copy -- entirely separate from the
                    # image, never part of Step 2's OCR output. Confirmed
                    # live this was the bigger "context is lost" gap: these
                    # fields carry real marketing copy the image alone
                    # doesn't show at all.
                    "ad_title": ad.get("title") or "",
                    "ad_body": ad.get("body") or "",
                    "ad_caption": ad.get("caption") or "",
                    "expected_headline_correct": None,
                    "expected_hook_framework": None,
                    "expected_human_presence": None,
                    "expected_model_count": None,
                    "notes": "",
                }
            )
            added_for_category += 1

    return golden_set, fetched_ok, fetch_failed


def evaluate_cognitive_accuracy(golden_set: list[dict[str, Any]]) -> dict[str, Any]:
    """Scores a labeled golden set. Fields left unlabeled (expected_* still
    None) are excluded from that field's metric, not counted as wrong —
    lets partial-labeling progress still produce a meaningful readout."""
    return {
        "headline": eval_core.compute_judgment_rate(golden_set, "expected_headline_correct"),
        "hook_framework": eval_core.compute_categorical_accuracy(
            golden_set, "predicted_hook_framework", "expected_hook_framework"
        ),
        "human_presence": eval_core.compute_boolean_accuracy(
            golden_set, "predicted_human_presence", "expected_human_presence"
        ),
        "model_count": eval_core.compute_count_accuracy(
            golden_set, "predicted_model_count", "expected_model_count", tolerance=1
        ),
    }


def print_evaluation(results: dict[str, Any]) -> None:
    eval_core.print_boolean_result("OCR Headline Correctness", results["headline"])
    eval_core.print_categorical_result("Hook Framework", results["hook_framework"])
    eval_core.print_boolean_result("Human Presence", results["human_presence"])
    eval_core.print_count_result("Model Count", results["model_count"])


def main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.validation.cognitive_validator <command>")
        print("  Commands:")
        print("    sample   - Build a stratified golden set from out/step2/ predictions")
        print("    evaluate - Score a labeled golden set")
        sys.exit(1)

    command = sys.argv[1]

    if command == "sample":
        existing = None
        if DEFAULT_GOLDEN_SET_PATH.exists():
            existing = eval_core.load_golden_set(DEFAULT_GOLDEN_SET_PATH)
            print(f"Resuming: {len(existing)} ads already in {DEFAULT_GOLDEN_SET_PATH}")

        golden_set, fetched_ok, fetch_failed = build_golden_set(existing_golden_set=existing)
        eval_core.save_golden_set(golden_set, DEFAULT_GOLDEN_SET_PATH)

        attempted = fetched_ok + fetch_failed
        rate = f"{fetched_ok}/{attempted} ({fetched_ok / attempted:.0%})" if attempted else "0/0"
        print(f"This run: {rate} image fetches succeeded")

        shortfall = {
            cat: 6 - sum(1 for e in golden_set if e["predicted_hook_framework"] == cat)
            for cat in HOOK_FRAMEWORK_CATEGORIES
        }
        still_short = {cat: n for cat, n in shortfall.items() if n > 0}
        print(f"✅ Golden set: {len(golden_set)} ads total -> {DEFAULT_GOLDEN_SET_PATH}")
        if still_short:
            print(f"   Still short: {still_short} — re-run 'sample' to top up further.")
        else:
            print("   All categories fully stocked.")
        print("   Open pipeline/validation/cognitive_validator.html and load that file to label.")

    elif command == "evaluate":
        golden_set = eval_core.load_golden_set(DEFAULT_GOLDEN_SET_PATH)
        results = evaluate_cognitive_accuracy(golden_set)
        print_evaluation(results)


if __name__ == "__main__":
    main()
