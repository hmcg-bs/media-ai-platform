"""Shared, field-agnostic evaluation helpers for golden-set accuracy checks.

Generalizes the accuracy/confusion-matrix logic that was being duplicated
between phase0_validator.py and price_context_validator.py, so a new
evaluated field (a 4th, 5th, ... category) is a config, not a new script.

Three shapes covered, matching what these golden-set evals actually need:
- Boolean fields (e.g. human_presence): accuracy only.
- Categorical fields (e.g. hook_framework): accuracy + confusion matrix.
- Count fields (e.g. model_count): exact-match accuracy + an off-by-N
  tolerance accuracy (a count that's off by one is a much smaller miss than
  one that's off by five).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_golden_set(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def save_golden_set(golden_set: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(golden_set, indent=2))


def compute_boolean_accuracy(
    golden_set: list[dict[str, Any]], predicted_key: str, expected_key: str
) -> dict[str, Any]:
    """Accuracy for a boolean field. Entries whose expected_key is None
    (not yet labeled) are excluded, not counted as wrong."""
    correct = 0
    total = 0
    for entry in golden_set:
        expected = entry.get(expected_key)
        if expected is None:
            continue
        total += 1
        if entry.get(predicted_key) == expected:
            correct += 1
    return {
        "accuracy": correct / total if total else 0.0,
        "total_evaluated": total,
        "correct": correct,
    }


def compute_judgment_rate(golden_set: list[dict[str, Any]], expected_key: str) -> dict[str, Any]:
    """For a single boolean judgment with no separate predicted value to
    compare against (e.g. "is this extraction correct?", not "does
    prediction X match expected Y?") — the rate at which labeled entries
    were judged True. Entries whose expected_key is None (not yet labeled)
    are excluded. Same result shape as compute_boolean_accuracy so callers
    (e.g. print_boolean_result) can treat both uniformly."""
    labeled = [entry[expected_key] for entry in golden_set if entry.get(expected_key) is not None]
    total = len(labeled)
    correct = sum(1 for v in labeled if v is True)
    return {
        "accuracy": correct / total if total else 0.0,
        "total_evaluated": total,
        "correct": correct,
    }


def compute_categorical_accuracy(
    golden_set: list[dict[str, Any]], predicted_key: str, expected_key: str
) -> dict[str, Any]:
    """Accuracy + confusion matrix for a categorical field. Entries whose
    expected_key is None (not yet labeled) are excluded."""
    correct_counts: Counter[str] = Counter()
    expected_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    mismatches: list[dict[str, Any]] = []

    for entry in golden_set:
        expected = entry.get(expected_key)
        if expected is None:
            continue
        predicted = entry.get(predicted_key)
        expected_counts[expected] += 1
        predicted_counts[predicted] += 1
        if predicted == expected:
            correct_counts[expected] += 1
        else:
            mismatches.append(
                {
                    "ad_id": entry.get("ad_id"),
                    "expected": expected,
                    "predicted": predicted,
                }
            )

    total = sum(expected_counts.values())
    per_category_accuracy = {
        cat: correct_counts.get(cat, 0) / count for cat, count in expected_counts.items()
    }
    overall_accuracy = sum(correct_counts.values()) / total if total else 0.0

    return {
        "accuracy": overall_accuracy,
        "total_evaluated": total,
        "per_category_accuracy": per_category_accuracy,
        "expected_category_counts": dict(expected_counts),
        "predicted_category_counts": dict(predicted_counts),
        "mismatches": mismatches,
    }


def compute_count_accuracy(
    golden_set: list[dict[str, Any]],
    predicted_key: str,
    expected_key: str,
    tolerance: int = 1,
) -> dict[str, Any]:
    """Exact-match accuracy + an off-by-`tolerance` accuracy for a count
    field. Entries whose expected_key is None (not yet labeled) are
    excluded."""
    exact = 0
    within_tolerance = 0
    total = 0
    for entry in golden_set:
        expected = entry.get(expected_key)
        if expected is None:
            continue
        predicted = entry.get(predicted_key)
        total += 1
        if predicted is None:
            continue
        diff = abs(predicted - expected)
        if diff == 0:
            exact += 1
        if diff <= tolerance:
            within_tolerance += 1
    return {
        "exact_accuracy": exact / total if total else 0.0,
        "within_tolerance_accuracy": within_tolerance / total if total else 0.0,
        "tolerance": tolerance,
        "total_evaluated": total,
    }


def print_boolean_result(name: str, result: dict[str, Any]) -> None:
    print(f"\n📊 {name} Accuracy")
    print(
        f"   Accuracy: {result['accuracy']:.1%} "
        f"({result['correct']}/{result['total_evaluated']})"
    )


def print_categorical_result(name: str, result: dict[str, Any]) -> None:
    print(f"\n📊 {name} Accuracy")
    print(f"   Overall accuracy: {result['accuracy']:.1%} ({result['total_evaluated']} evaluated)")
    for cat, acc in result["per_category_accuracy"].items():
        count = result["expected_category_counts"][cat]
        correct = round(acc * count)
        print(f"     {cat}: {acc:.1%} ({correct}/{count})")
    if result["mismatches"]:
        print(f"   Mismatches ({len(result['mismatches'])}):")
        for m in result["mismatches"]:
            print(f"     {m['ad_id']}: expected={m['expected']} predicted={m['predicted']}")


def print_count_result(name: str, result: dict[str, Any]) -> None:
    print(f"\n📊 {name} Accuracy")
    print(f"   Exact accuracy:            {result['exact_accuracy']:.1%}")
    print(
        f"   Within ±{result['tolerance']} accuracy: "
        f"{result['within_tolerance_accuracy']:.1%}"
    )
    print(f"   Evaluated: {result['total_evaluated']}")
