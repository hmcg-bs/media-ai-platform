"""Price-context classification eval.

Measures how accurately the Tier 5 LLM (ingestion/llm_fallback.py) classifies
price-like mentions as the product's real price (real_offer/bundle_price) vs.
noise (shipping/promo banners, rhetorical/competitor prices, cart-subtotal
widgets) -- see docs/extraction-failure-modes.md for the failure modes this
guards against.

Follows the pipeline/validation/phase0_validator.py pattern: classify, then
evaluate against hand-labeled ground truth, plain JSON/stdout output. The
golden set (data/price_context_golden_set.json) was built by re-fetching the
26 real pages diagnosed in extraction-gap Round 1 (GitHub issue #28) and
hand-labeling expected_price_context/expected_recoverable from the actually
captured pruned-markdown content -- see build script referenced in that
issue's history. No live fetches happen in this module; classification runs
against the already-captured markdown.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ingestion.llm_fallback import (
    _DIRECT_RESPONSE_JSON_INSTRUCTIONS,
    _SYSTEM_PROMPT,
    _DirectResponseLLMExtraction,
)
from pipeline.clients.replicate_client import ReplicateVisionClient

DATA_DIR = Path(__file__).parent.parent.parent / "data"
GOLDEN_SET_PATH = DATA_DIR / "price_context_golden_set.json"
DEFAULT_CLASSIFIED_PATH = DATA_DIR / "price_context_classified.json"

_DISCARDED_PRICE_CONTEXTS = frozenset(
    {"shipping_or_promo_banner", "rhetorical_or_competitor_price", "cart_subtotal_widget"}
)


def load_golden_set(path: Path = GOLDEN_SET_PATH) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def classify_golden_set(
    golden_set: list[dict],
    checkpoint_path: Path | None = None,
    resume: bool = False,
) -> list[dict]:
    """Runs the real Tier 5 prompt/schema against each golden-set entry's
    already-captured pruned markdown -- no new page fetches.

    Deliberately bypasses extract_via_llm()'s own offer_matrix filtering:
    every raw price_context classification (kept or discarded in
    production) needs to stay visible here for per-category scoring, not
    just the survivors.
    """
    results: dict[str, dict] = {}
    if resume and checkpoint_path and checkpoint_path.exists():
        with open(checkpoint_path) as f:
            results = {r["url"]: r for r in json.load(f)}
        print(f"Resuming: {len(results)} already classified")

    client = ReplicateVisionClient()
    remaining = [e for e in golden_set if e["url"] not in results]
    for i, entry in enumerate(remaining, 1):
        url = entry["url"]
        prompt = f"""{_SYSTEM_PROMPT}

**Page content (Markdown, pruned to the hero/offer, social-proof, and specs zones):**
{entry["pruned_markdown"]}

{_DIRECT_RESPONSE_JSON_INSTRUCTIONS}"""
        try:
            extraction = client.extract_structured_text(
                prompt=prompt, schema=_DirectResponseLLMExtraction
            )
            offers = [o.model_dump() for o in (extraction.offer_matrix or [])]
        except Exception as e:  # noqa: BLE001 — one bad page shouldn't kill the eval run
            offers = []
            print(f"[{i}/{len(remaining)}] {url} -> ERROR {e!r}")
        results[url] = {"url": url, "offers": offers}
        print(f"[{i}/{len(remaining)}] {url} -> {len(offers)} offer(s): "
              f"{[o.get('price_context') for o in offers]}")
        if checkpoint_path:
            with open(checkpoint_path, "w") as f:
                json.dump(list(results.values()), f, indent=2)

    return list(results.values())


def evaluate_price_context_accuracy(
    golden_set: list[dict],
    classified: list[dict],
) -> dict[str, Any]:
    """Two metrics, deliberately kept separate:

    - recoverable_accuracy: across ALL golden-set entries, does whether a
      real price ends up recoverable (>=1 kept real_offer/bundle_price
      offer with a total_price) match expected_recoverable? This is the
      end-to-end metric that matters for corpus coverage.
    - per_category_accuracy: restricted to entries where
      expected_price_context is not null (i.e. a real price-like mention
      genuinely exists in the captured content to classify) -- entries
      where the golden set expects no price content at all (a
      zone-selection miss, or a genuinely price-less page) have no
      category to classify and are excluded from this metric, not
      penalized as a miss.
    """
    classified_by_url = {c["url"]: c for c in classified}
    expected_counts: Counter[str] = Counter()
    correct_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    recoverable_correct = 0
    mismatches: list[dict] = []

    for entry in golden_set:
        url = entry["url"]
        expected_category = entry["expected_price_context"]
        expected_recoverable = entry["expected_recoverable"]
        result = classified_by_url.get(url)
        offers = result["offers"] if result else []

        kept = [o for o in offers if o.get("price_context") not in _DISCARDED_PRICE_CONTEXTS]
        actual_recoverable = any(o.get("total_price") is not None for o in kept)
        if actual_recoverable == expected_recoverable:
            recoverable_correct += 1

        contexts = [o.get("price_context") for o in offers if o.get("price_context")]
        predicted_category = Counter(contexts).most_common(1)[0][0] if contexts else None

        if expected_category is not None:
            expected_counts[expected_category] += 1
            predicted_counts[predicted_category or "none_classified"] += 1
            if predicted_category == expected_category:
                correct_counts[expected_category] += 1
            else:
                mismatches.append(
                    {"url": url, "expected": expected_category, "predicted": predicted_category}
                )

    category_total = sum(expected_counts.values())
    category_accuracy = {
        cat: correct_counts.get(cat, 0) / count for cat, count in expected_counts.items()
    }
    overall_category_accuracy = (
        sum(correct_counts.values()) / category_total if category_total else 0.0
    )
    recoverable_accuracy = recoverable_correct / len(golden_set) if golden_set else 0.0

    results = {
        "total_evaluated": len(golden_set),
        "category_eval_count": category_total,
        "recoverable_accuracy": recoverable_accuracy,
        "overall_category_accuracy": overall_category_accuracy,
        "per_category_accuracy": category_accuracy,
        "expected_category_counts": dict(expected_counts),
        "predicted_category_counts": dict(predicted_counts),
        "mismatches": mismatches,
    }

    print("\n📊 Price-Context Classification Accuracy")
    print(f"   Recoverable-price accuracy: {recoverable_accuracy:.1%} ({len(golden_set)} pages)")
    print(
        f"   Overall category accuracy:  {overall_category_accuracy:.1%} "
        f"({category_total} pages with a real price mention to classify)"
    )
    for cat, acc in category_accuracy.items():
        print(f"     {cat}: {acc:.1%} ({correct_counts.get(cat, 0)}/{expected_counts[cat]})")
    if mismatches:
        print(f"   Mismatches ({len(mismatches)}):")
        for m in mismatches:
            print(f"     {m['url'][:70]}: expected={m['expected']} predicted={m['predicted']}")

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.validation.price_context_validator <command>")
        print("  Commands:")
        print("    classify [--resume] - Run Tier 5 classification against the golden set")
        print("    evaluate            - Score classified output against ground truth")
        sys.exit(1)

    command = sys.argv[1]
    resume = "--resume" in sys.argv

    if command == "classify":
        golden = load_golden_set()
        classify_golden_set(golden, checkpoint_path=DEFAULT_CLASSIFIED_PATH, resume=resume)
        print(f"✅ Saved classified results to {DEFAULT_CLASSIFIED_PATH}")

    elif command == "evaluate":
        golden = load_golden_set()
        with open(DEFAULT_CLASSIFIED_PATH) as f:
            classified_results = json.load(f)
        evaluate_price_context_accuracy(golden, classified_results)
