"""Extracts a structured, interpretable "generation guide" from the already-
trained Critique models (Cox survival for `days_active`, XGBoost for
`collation_count`/`variants_featured_count`, and the composite success-score
SHAP model) — the concrete answer to "can we extract information from the
model that can guide image generation."

Two real constraints shape this module, both confirmed against the actual
model outputs (not assumed):

1. Not every top feature is a usable creative directive. A large fraction of
   Cox's top covariates are `_unknown`/`_None` category levels -- these mean
   "this ad has no Step 2 creative_features at all" (a data-availability
   artifact from the corpus's known coverage gaps), not "an unknown color
   causes success." Filtered out entirely, never surfaced as guidance.

2. Not every source gives a *direction*. Cox's coefficients and the composite
   score's SHAP values are signed (do push toward or away from success); plain
   XGBoost `feature_importances_` (used for collation_count/
   variants_featured_count in trainer.py) are gain-based and always
   non-negative -- they say a feature matters, never which way to push it.
   Treated as two different confidence tiers, never conflated.

For SHAP sources, a feature's *direction reliability* is computed as
`abs(mean_signed_shap) / mean_abs_shap` -- close to 1 means the feature
consistently pushes one way across ads; close to 0 means its per-ad pushes
largely cancel out (mixed effects, e.g. `rating`, confirmed unstable earlier
this project when the with/without-embeddings ablations flipped its sign).
Below `MIN_DIRECTION_RELIABILITY`, a feature is kept only as a non-directional
signal, never asserted a direction it doesn't reliably have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_TRAINING_REPORT = DATA_DIR / "model_training_report_fresh.json"
DEFAULT_SUCCESS_SCORE_REPORT = DATA_DIR / "success_score_report_fresh.json"

MIN_DIRECTION_RELIABILITY = 0.10

# Category *levels* that mean "no data", never a real creative choice.
_MISSING_DATA_LEVELS = {"unknown", "none", "nan", ""}

# Dimension name -> bucket. Explicit allowlist rather than inferring from
# name patterns, so a new feature silently landing in the wrong bucket (or
# being silently treated as a creative lever when it's really a campaign-
# operations signal) can't happen without someone deciding where it goes.
_VISUAL_CATEGORICAL_DIMENSIONS = {
    "cta_type", "dominant_color", "hook_framework", "background_style",
    "text_alignment", "contrast_ratio_type", "headline_zone",
}
_VISUAL_NUMERIC_DIMENSIONS = {
    "cta_present", "palette_vibrancy", "psychological_warmth_index",
    "copy_canvas_coverage", "asset_canvas_coverage",
    "rating",  # whether/how prominently to show a rating badge -- a real creative choice
}
_COPY_STYLE_NUMERIC_DIMENSIONS = {
    "uppercase_ratio", "reading_grade_level", "whitespace_ratio",
    "copy_block_count", "cultural_branding_count", "headline_char_count",
    "headline_to_subtext_scale_ratio", "avg_words_per_block", "total_word_count",
    "title_length", "body_length", "headline_word_count", "total_char_count",
}
# Real signal, but describes campaign *operations* (targeting/attribution/
# how many platforms), not anything a generation agent renders as pixels.
_NON_VISUAL_EXCLUDED_DIMENSIONS = {
    "utm_medium_category", "campaign_role_signal", "publisher_count",
    "cluster_variant_rate", "cluster_mean_days_active", "utm_dynamic_naming",
    "utm_content_granularity_score", "has_utm_tracking",
}
# A business/positioning fact worth keeping as *context* for the guide
# (e.g. "for a budget-tier product...") -- never a rendered visual element.
_POSITIONING_CONTEXT_DIMENSIONS = {"price_tier"}


class DirectionalSignal(BaseModel):
    dimension: str
    value: str | None  # category level for categorical dims; None for numeric
    direction: Literal["higher_is_better", "lower_is_better", "unknown"]
    magnitude: float  # abs(coefficient) or abs(mean_signed_shap); for ranking within a bucket
    source: str  # e.g. "cox:days_active", "shap:composite_success_score"


class GenerationGuide(BaseModel):
    visual_directives: list[DirectionalSignal]
    copy_style_directives: list[DirectionalSignal]
    positioning_context: list[DirectionalSignal]
    non_directional_signals: list[str]
    excluded_notes: list[str]


def _parse_feature_name(name: str) -> tuple[str, str, str | None] | None:
    """'categorical__cta_type_shop_now' -> ('categorical', 'cta_type', 'shop_now').
    'numeric__creative_uppercase_ratio' -> ('numeric', 'uppercase_ratio', None).
    Returns None if the prefix isn't numeric__/categorical__ (e.g. an
    embedding column, already filtered by the caller before this is reached
    in practice, but defensive here too)."""
    m = re.match(r"^(numeric|categorical)__(.+)$", name)
    if not m:
        return None
    kind, rest = m.group(1), m.group(2)
    rest = rest.removeprefix("creative_")

    if kind == "numeric":
        return kind, rest, None

    # categorical: the dimension is the longest known-dimension prefix of
    # `rest` -- one-hot names are `{dimension}_{level}` and levels themselves
    # sometimes contain underscores (e.g. "shop_now"), so a naive single
    # rsplit would misparse "cta_type_shop_now" as dimension="cta_type_shop".
    known_dims = (
        _VISUAL_CATEGORICAL_DIMENSIONS
        | _NON_VISUAL_EXCLUDED_DIMENSIONS
        | _POSITIONING_CONTEXT_DIMENSIONS
    )
    for dim in sorted(known_dims, key=len, reverse=True):
        if rest == dim or rest.startswith(dim + "_"):
            value = rest[len(dim) + 1 :] if rest != dim else None
            return kind, dim, value
    return None


def _bucket_for(dimension: str) -> str | None:
    if dimension in _VISUAL_CATEGORICAL_DIMENSIONS or dimension in _VISUAL_NUMERIC_DIMENSIONS:
        return "visual"
    if dimension in _COPY_STYLE_NUMERIC_DIMENSIONS:
        return "copy_style"
    if dimension in _POSITIONING_CONTEXT_DIMENSIONS:
        return "positioning"
    if dimension in _NON_VISUAL_EXCLUDED_DIMENSIONS:
        return "excluded_non_visual"
    return None  # unrecognized dimension -- excluded, not guessed at


def _direction_from_sign(x: float) -> Literal["higher_is_better", "lower_is_better"]:
    return "higher_is_better" if x > 0 else "lower_is_better"


def _from_cox(top_covariates: list[list[Any]], target: str) -> list[dict[str, Any]]:
    """Cox coefficients are already signed and reliable (no confidence-ratio
    check needed the way SHAP needs one) -- a Cox coefficient IS the direction."""
    out = []
    for name, coef in top_covariates:
        parsed = _parse_feature_name(name)
        if parsed is None:
            continue
        _kind, dimension, value = parsed
        if value is not None and value.lower() in _MISSING_DATA_LEVELS:
            continue
        bucket = _bucket_for(dimension)
        if bucket is None or bucket == "excluded_non_visual":
            continue
        out.append({
            "dimension": dimension, "value": value,
            "direction": _direction_from_sign(coef), "magnitude": abs(coef),
            "source": f"cox:{target}", "bucket": bucket,
        })
    return out


def _from_shap(
    entries: list[dict[str, Any]], source_label: str
) -> tuple[list[dict[str, Any]], list[str]]:
    directional: list[dict[str, Any]] = []
    non_directional: list[str] = []
    for e in entries:
        parsed = _parse_feature_name(e["feature"])
        if parsed is None:
            continue
        _kind, dimension, value = parsed
        if value is not None and value.lower() in _MISSING_DATA_LEVELS:
            continue
        bucket = _bucket_for(dimension)
        if bucket is None or bucket == "excluded_non_visual":
            continue
        abs_shap = e["mean_abs_shap"]
        signed_shap = e["mean_signed_shap"]
        reliability = abs(signed_shap) / abs_shap if abs_shap > 0 else 0.0
        label = f"{dimension}" + (f"={value}" if value else "")
        if reliability < MIN_DIRECTION_RELIABILITY:
            non_directional.append(
                f"{label} ({source_label}, |signed|/|abs|={reliability:.2f} -- direction too "
                "inconsistent across ads to trust)"
            )
            continue
        directional.append({
            "dimension": dimension, "value": value,
            "direction": _direction_from_sign(signed_shap), "magnitude": abs_shap,
            "source": source_label, "bucket": bucket,
        })
    return directional, non_directional


def _to_signals(raw: list[dict[str, Any]], bucket: str) -> list[DirectionalSignal]:
    filtered = [r for r in raw if r["bucket"] == bucket]
    filtered.sort(key=lambda r: -r["magnitude"])
    return [
        DirectionalSignal(
            dimension=r["dimension"], value=r["value"],
            direction=r["direction"], magnitude=round(r["magnitude"], 5),
            source=r["source"],
        )
        for r in filtered
    ]


def extract_generation_guide(
    training_report_file: Path = DEFAULT_TRAINING_REPORT,
    success_score_report_file: Path = DEFAULT_SUCCESS_SCORE_REPORT,
) -> GenerationGuide:
    training_report = json.loads(training_report_file.read_text())
    success_score_report = json.loads(success_score_report_file.read_text())

    all_directional: list[dict[str, Any]] = []
    non_directional_notes: list[str] = []
    excluded_notes: list[str] = [
        "Dropped every '_unknown'/'_None' category level (means 'no Step 2 "
        "creative_features for this ad', not a real creative choice).",
        "Dropped campaign-operations dimensions ("
        f"{', '.join(sorted(_NON_VISUAL_EXCLUDED_DIMENSIONS))}) "
        "-- real signal, but not something a generation agent renders as pixels.",
        "Dropped raw embedding-dimension features entirely (uninterpretable as a directive).",
    ]

    # Cox: the only source with a real per-ad-outcome (not just tree-split
    # importance) directional signal for days_active.
    cox = training_report.get("model_results", {}).get("days_active", {}).get("cox_survival", {})
    all_directional += _from_cox(cox.get("top_covariates", []), target="days_active")

    # Composite success-score SHAP (without_embeddings -- interpretable
    # feature names only; the with_embeddings variant's top features are
    # mostly raw embedding dimensions, useless as a rendered directive).
    shap_entries = success_score_report.get("without_embeddings", {}).get(
        "top_features_by_shap", []
    )
    shap_directional, shap_non_directional = _from_shap(
        shap_entries, "shap:composite_success_score"
    )
    all_directional += shap_directional
    non_directional_notes += shap_non_directional

    # collation_count / variants_featured_count: XGBoost feature_importances_
    # only -- real "this matters" signal, but no direction. Recorded
    # separately, never asserted a direction they don't have.
    for target in ("collation_count", "variants_featured_count"):
        top_features = (
            training_report.get("model_results", {}).get(target, {})
            .get("without_embeddings", {}).get("top_features", [])
        )
        for name, importance in top_features:
            parsed = _parse_feature_name(name)
            if parsed is None:
                continue
            _kind, dimension, value = parsed
            if value is not None and value.lower() in _MISSING_DATA_LEVELS:
                continue
            bucket = _bucket_for(dimension)
            if bucket is None or bucket == "excluded_non_visual":
                continue
            label = f"{dimension}" + (f"={value}" if value else "")
            non_directional_notes.append(
                f"{label} (xgboost:{target}, importance={importance:.4f} -- matters, "
                "direction not available from tree feature_importances_)"
            )

    return GenerationGuide(
        visual_directives=_to_signals(all_directional, "visual"),
        copy_style_directives=_to_signals(all_directional, "copy_style"),
        positioning_context=_to_signals(all_directional, "positioning"),
        non_directional_signals=sorted(set(non_directional_notes)),
        excluded_notes=excluded_notes,
    )


def print_guide(guide: GenerationGuide) -> None:
    print("=== Visual directives (direction-reliable) ===")
    for s in guide.visual_directives:
        label = f"{s.dimension}" + (f"={s.value}" if s.value else "")
        print(f"  {s.direction:16s}  {label:40s}  (mag={s.magnitude:.4f}, {s.source})")
    print("\n=== Copy-style directives ===")
    for s in guide.copy_style_directives:
        print(f"  {s.direction:16s}  {s.dimension:40s}  (mag={s.magnitude:.4f}, {s.source})")
    print("\n=== Positioning context (not a visual directive) ===")
    for s in guide.positioning_context:
        label = f"{s.dimension}" + (f"={s.value}" if s.value else "")
        print(f"  {s.direction:16s}  {label:40s}  (mag={s.magnitude:.4f}, {s.source})")
    print(f"\n=== Non-directional signals ({len(guide.non_directional_signals)}) ===")
    for n in guide.non_directional_signals[:15]:
        print(f"  {n}")
    print("\n=== Excluded, for transparency ===")
    for n in guide.excluded_notes:
        print(f"  - {n}")


if __name__ == "__main__":
    print_guide(extract_generation_guide())
