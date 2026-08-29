"""Hand-populated registry of every column in the feature matrix
(pipeline/feature_engineering/build_matrix.py's output) -- what it is,
where it comes from, and any caveat that changes how it should be read.
This is what turns a raw column name into something a human can actually
understand in the validation report; data_quality.py's stats alone can't
carry "this defaults to 0.5, not a real null" or "this is a heuristic, not
ground truth."

Populated directly from this session's own feature-engineering build, not
inferred -- every description/caveat here traces to a real line of code in
pipeline/feature_engineering/*.py or ingestion/utm_features.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FeatureKind = Literal["numeric", "categorical", "boolean", "embedding", "id", "target", "label"]


@dataclass(frozen=True)
class FeatureMeta:
    kind: FeatureKind
    source: str
    description: str
    caveats: str = ""


FEATURE_REGISTRY: dict[str, FeatureMeta] = {
    "ad_id": FeatureMeta("id", "build_matrix.py", "Row identifier (ad_archive_id), not a feature."),
    "price_tier": FeatureMeta(
        "label", "product_features.calculate_price_tier",
        "Fixed-dollar price bucket (budget <$15 / mid $15-35 / premium $35-60 / "
        "luxury_bundle $60+), used for segmentation, not fed as raw price.",
        caveats="'unknown' when price wasn't extracted -- real and common (~29% of "
        "corpus), not a defect.",
    ),

    # --- Text embeddings ---
    "title_embedding": FeatureMeta(
        "embedding", "embeddings.py::extract_embedding_features",
        "768-dim embedding-gemma vector of the ad title.",
        caveats="Empty list [] when title is blank -- expanded to individual NaN "
        "columns downstream (preprocessing.py), never a fabricated zero vector.",
    ),
    "body_embedding": FeatureMeta(
        "embedding", "embeddings.py::extract_embedding_features",
        "768-dim embedding-gemma vector of the ad body copy (first 300 chars).",
    ),
    "usp_embedding": FeatureMeta(
        "embedding", "embeddings.py::extract_embedding_features",
        "768-dim embedding-gemma vector of the landing page's USP text.",
        caveats="Empty for ~64% of rows (confirmed by data_quality.py) -- USP text "
        "itself is a sparser field than title/body.",
    ),

    # --- Text length ---
    "title_length": FeatureMeta(
        "numeric", "text_features.py", "Character length of the ad title."
    ),
    "body_length": FeatureMeta(
        "numeric", "text_features.py", "Character length of the ad body copy."
    ),
    "usp_length": FeatureMeta(
        "numeric", "text_features.py", "Character length of the landing page's USP text."
    ),

    # --- Language signals ---
    "urgency_language": FeatureMeta(
        "boolean", "text_features.py::extract_language_signals",
        "Whether ad text contains urgency keywords (limited, today, hurry, ...).",
    ),
    "social_proof_language": FeatureMeta(
        "boolean", "text_features.py::extract_language_signals",
        "Whether ad text contains social-proof keywords (reviews, bestseller, trusted, ...).",
    ),

    # --- CTA ---
    "has_cta_text": FeatureMeta(
        "boolean", "text_features.py::extract_cta_features", "Whether cta_text is present at all."
    ),
    "cta_type": FeatureMeta(
        "categorical", "text_features.py::extract_cta_features",
        "CTA text mapped to a known type (shop_now, buy_now, learn_more, ...).",
        caveats="'none' when has_cta_text is False -- not a real 8th category, an absence marker.",
    ),

    # --- Positioning ---
    "premium_positioning": FeatureMeta(
        "boolean", "text_features.py::extract_positioning_features",
        "Premium-brand signal: price >= 1.5x category median, OR premium/luxury "
        "language in the ad text.",
        caveats="STRUCTURAL LIMITATION: category_stats (needed for the price-based "
        "branch) is never passed by build_matrix.py -- only the text-keyword branch "
        "can ever fire in practice. Confirmed in code, not yet fixed.",
    ),
    "season_positioning": FeatureMeta(
        "categorical", "text_features.py::extract_positioning_features",
        "Seasonal theme keyword match (holiday/summer/back_to_school/none).",
    ),

    # --- Color ---
    "dominant_color": FeatureMeta(
        "categorical", "color_features.py::extract_color_features",
        "Coarse color-name bucket (red/blue/green/black/white/gray/other) of the "
        "dominant creative hex swatch.",
        caveats="'unknown' when no Step 2 creative_features exist for this ad "
        "(~54% of corpus) -- not a real color observation.",
    ),
    "palette_vibrancy": FeatureMeta(
        "numeric", "color_features.py::compute_palette_vibrancy",
        "Std-dev of HSV saturation across the creative's dominant hex palette.",
        caveats="DEFAULT-FILL, NOT A REAL NULL: defaults to exactly 0.5 when no "
        "palette data exists (~54% of corpus) -- this creates a large artificial "
        "point-mass at 0.5, confirmed by data_quality.py (55% of non-null values).",
    ),
    "psychological_warmth_index": FeatureMeta(
        "numeric", "color_features.py::extract_color_features",
        "1.0 if the dominant color is warm (red/orange-leaning), else 0.0.",
        caveats="DEFAULT-FILL: defaults to 0.0 when no dominant_hex exists, "
        "indistinguishable from a real 'not warm' reading -- same artifact class as "
        "palette_vibrancy.",
    ),
    "contrast_ratio_type": FeatureMeta(
        "categorical", "color_features.py", "Step 2's own text/background contrast classification.",
        caveats="'unknown' default when no Step 2 data (~54% of corpus).",
    ),
    "background_style": FeatureMeta(
        "categorical", "color_features.py", "Step 2's own background-style classification.",
        caveats="'unknown' default when no Step 2 data (~54% of corpus).",
    ),

    # --- Product ---
    "rating": FeatureMeta(
        "numeric", "product_features.py::extract_product_features",
        "Product rating (0-5) from landing-page enrichment.",
        caveats="A real None (not default-filled) when no product_page exists. "
        "Separately: ~45% of the non-null values are exactly 4.8 -- confirmed a real "
        "corpus characteristic (many genuinely well-rated DTC supplement products), "
        "not a pipeline artifact, per this session's earlier rating-bound audit.",
    ),
    "shows_all_variants": FeatureMeta(
        "boolean", "product_features.py",
        "Whether the landing page shows more than one product variant "
        "(len(variants_featured) > 1, computed upstream in ingestion/).",
        caveats="Near-tautological with variants_featured_count (same underlying "
        "data) -- deliberately EXCLUDED from variants_featured_count's own model "
        "input (preprocessing.py::LEAKY_FEATURES_BY_TARGET) to avoid the model "
        "'predicting' the count from a summary of itself. Still valid as a feature "
        "for the other two targets.",
    ),
    "variants_featured_count": FeatureMeta(
        "target", "product_features.py",
        "Count of distinct product variants (SKUs/bundle tiers) shown on the "
        "landing page. P1 target variable -- landing-page product complexity, "
        "distinct from Meta's own collation_count.",
        caveats="Defaults to 0 both when a product genuinely has one SKU AND when "
        "product_page enrichment never ran -- these two cases are NOT "
        "distinguishable from the count alone. Coverage 53.0% real variant data "
        "as of this session's extraction-gap work (issue #35).",
    ),
    "cultural_branding_count": FeatureMeta(
        "numeric", "product_features.py",
        "Count of cultural-branding signals detected on the landing page.",
        caveats="Defaults to 0 when no product_page -- same zero-vs-unknown "
        "conflation as variants_featured_count.",
    ),

    # --- Account / platform ---
    "days_active": FeatureMeta(
        "target", "extractor.py (ad.days_active, computed in ingestion/normalize.py)",
        "Longevity proxy: end_date (or scrape date if still active) minus "
        "start_date. P0 target variable.",
        caveats="RIGHT-CENSORED for most of the corpus: 94.2% of ads share one of "
        "two end_date values (the corpus's own scrape-run dates, not individual "
        "death dates) -- this is why days_active is modeled via Cox survival "
        "analysis (survival.py), not plain regression. Treating it as ground-truth "
        "duration in any other context would be wrong.",
    ),
    "collation_count": FeatureMeta(
        "target", "extractor.py (ad.collation_count, direct Apify passthrough)",
        "Meta's own count of ad-creative variants being tested simultaneously "
        "under the same underlying ad. P1 target variable (Density/testing-"
        "intensity signal).",
        caveats="Confirmed low-variance (median 1.0) via a prior session's "
        "investigation -- a genuine direct passthrough, not a pipeline bug, but a "
        "structurally weak signal. Both regression models on this target show weak "
        "test R2 (around -0.09 to -0.04) consistent with this.",
    ),
    "publisher_count": FeatureMeta(
        "numeric", "extractor.py",
        "Number of distinct publisher_platforms (Facebook/Instagram/...) the ad runs on.",
    ),

    # --- UTM / campaign taxonomy ---
    "has_utm_tracking": FeatureMeta(
        "boolean", "ingestion/utm_features.py::extract_campaign_features",
        "Whether link_url carries any utm_* query parameter at all.",
        caveats="True for only ~3.6% of the corpus (~100/2,736 ads) -- UTM data is "
        "genuinely sparse, not a pipeline gap.",
    ),
    "utm_medium_category": FeatureMeta(
        "categorical", "ingestion/utm_features.py::categorize_utm_medium",
        "utm_medium normalized to dedicated_paid_social / legacy_generic / unknown.",
        caveats="'unknown' for ~99% of rows (sparse UTM coverage, see has_utm_tracking).",
    ),
    "utm_dynamic_naming": FeatureMeta(
        "boolean", "ingestion/utm_features.py::has_dynamic_naming",
        "Whether utm_campaign/utm_content/utm_term contains an unresolved Facebook "
        "dynamic template ({{campaign.id}} etc.).",
    ),
    "utm_content_granularity_score": FeatureMeta(
        "numeric", "ingestion/utm_features.py::content_granularity_score",
        "Count of distinct creative-dimension keywords (hook/cta/ugc/static/...) "
        "named in utm_content.",
        caveats="0 for ~100% of non-null rows (data_quality.py point-mass flag) -- "
        "an artifact of sparse UTM coverage, not a broken feature; among the ~100 "
        "rows with real UTM data, nonzero scores do occur.",
    ),
    "campaign_role_signal": FeatureMeta(
        "categorical", "ingestion/utm_features.py::campaign_role_signal",
        "Best-effort inference of likely_test vs likely_scale campaign intent from "
        "UTM taxonomy.",
        caveats="EXPLICIT HEURISTIC, NOT GROUND TRUTH -- same honesty standard as "
        "subscription_status/price_context elsewhere in this codebase. 'unknown' "
        "for ~97% of rows (sparse UTM coverage). Live-verified this session: showed "
        "up in the days_active Cox model's top-10 covariates with an intuitive "
        "sign (likely_scale associates with lower hazard / longer survival).",
    ),
}

# Every creative_* column shares one source/caveat pattern -- generated
# rather than hand-duplicated 28 times.
_STEP2_CREATIVE_DESCRIPTIONS: dict[str, str] = {
    "copy_block_count": "Number of distinct text/copy blocks detected on the creative.",
    "total_word_count": "Total word count across all copy on the creative.",
    "total_char_count": "Total character count across all copy on the creative.",
    "headline_word_count": "Word count of the primary headline.",
    "headline_char_count": "Character count of the primary headline.",
    "avg_words_per_block": "Average words per detected copy block.",
    "uppercase_ratio": "Fraction of copy text that is uppercase.",
    "exclamation_count": "Count of exclamation marks across all copy.",
    "question_count": "Count of question marks across all copy.",
    "emoji_count": "Count of emoji characters across all copy.",
    "reading_grade_level": "Flesch-Kincaid reading grade level of the copy.",
    "hook_framework": "Marketing hook framework classification (PAS/AIDA/Direct Offer/...).",
    "cta_present": "Content-pattern heuristic for whether a CTA phrase appears in the copy.",
    "claimed_benefits_count": "Count of distinct claimed-benefit phrases detected.",
    "has_price": "Content-pattern heuristic for whether a price appears in the copy.",
    "has_badge": (
        "Whether a trust badge/seal is detected (deterministic default only -- "
        "see docs/blueprints)."
    ),
    "has_legal": "Content-pattern heuristic for whether legal/disclaimer text appears.",
    "headline_to_subtext_scale_ratio": "Relative font-size ratio of headline to subtext.",
    "copy_canvas_coverage": "Fraction of the creative canvas covered by copy text.",
    "asset_canvas_coverage": (
        "Fraction of the creative canvas covered by non-text assets (always default -- "
        "text-only OCR has no image-region concept, see the Phase 2 Bridge notes above)."
    ),
    "whitespace_ratio": "Fraction of the creative canvas that is whitespace.",
    "copy_vs_image_balance": (
        "Ratio of copy coverage to image coverage (always default, same reason as "
        "asset_canvas_coverage)."
    ),
    "text_alignment": "Detected text alignment (left/center/right/mixed).",
    "headline_x_center": "Horizontal center position of the headline (normalized 0-1).",
    "headline_y_center": "Vertical center position of the headline (normalized 0-1).",
    "headline_zone": "Headline's vertical zone-of-thirds position (top/middle/bottom).",
    "n_blocks_top": "Count of copy blocks in the top third of the canvas.",
    "n_blocks_middle": "Count of copy blocks in the middle third of the canvas.",
    "n_blocks_bottom": "Count of copy blocks in the bottom third of the canvas.",
}
for _key, _desc in _STEP2_CREATIVE_DESCRIPTIONS.items():
    _boolean_keys = ("cta_present", "has_price", "has_badge", "has_legal")
    _categorical_keys = ("hook_framework", "text_alignment", "headline_zone")
    FEATURE_REGISTRY[f"creative_{_key}"] = FeatureMeta(
        kind="boolean" if _key in _boolean_keys else (
            "categorical" if _key in _categorical_keys else "numeric"
        ),
        source="pipeline/stages/stage_02_ocr.py (derive_copywriting_features/derive_placement) "
        "via pipeline/models/output_schema.py::flatten_features()",
        description=_desc,
        caveats="None (a real null, not 0) for every ad Step 2 never processed -- "
        "~54% of the corpus. Confirmed by data_quality.py: most non-null "
        "creative_* numeric columns still show ~97-100% point-mass at 0.0 among "
        "the ads that WERE processed, since most creatives simply don't trigger "
        "these content-pattern heuristics (e.g. most ads have zero emoji).",
    )


def get_feature_meta(name: str) -> FeatureMeta:
    """Falls back to a generic 'unregistered' entry rather than raising --
    the registry should never block report generation for a genuinely new
    column someone forgot to register."""
    return FEATURE_REGISTRY.get(
        name,
        FeatureMeta(
            "numeric", "unknown", "Not yet documented in feature_registry.py -- add an entry."
        ),
    )
