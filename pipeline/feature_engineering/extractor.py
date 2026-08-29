"""Main feature extractor orchestrating all feature categories."""

from __future__ import annotations

from typing import Any

from ingestion.utm_features import extract_campaign_features
from pipeline.clients.replicate_client import EmbeddingClient
from pipeline.feature_engineering.color_features import (
    compute_palette_vibrancy,
    extract_color_features,
)
from pipeline.feature_engineering.embeddings import extract_embedding_features
from pipeline.feature_engineering.product_features import (
    calculate_price_tier,
    extract_product_features,
)
from pipeline.feature_engineering.text_features import (
    extract_cta_features,
    extract_language_signals,
    extract_positioning_features,
    extract_text_length_features,
)

# Step 2's ExtractionResult.flatten_features() field names (pipeline/models/
# output_schema.py) — copywriting + placement metrics from the creative
# *image* itself, not available anywhere in the ingestion corpus on its own.
# Prefixed with "creative_" in the final feature dict so any column is
# immediately recognizable as Step-2-sourced when inspecting the matrix.
_STEP2_FLATTENED_KEYS = (
    "copy_block_count",
    "total_word_count",
    "total_char_count",
    "headline_word_count",
    "headline_char_count",
    "avg_words_per_block",
    "uppercase_ratio",
    "exclamation_count",
    "question_count",
    "emoji_count",
    "reading_grade_level",
    "hook_framework",
    "cta_present",
    "claimed_benefits_count",
    "has_price",
    "has_badge",
    "has_legal",
    "headline_to_subtext_scale_ratio",
    "copy_canvas_coverage",
    "asset_canvas_coverage",
    "whitespace_ratio",
    "copy_vs_image_balance",
    "text_alignment",
    "headline_x_center",
    "headline_y_center",
    "headline_zone",
    "n_blocks_top",
    "n_blocks_middle",
    "n_blocks_bottom",
)


def extract_all_features(
    ad: dict[str, Any],
    creative_features: dict[str, Any] | None = None,
    category_stats: dict[str, Any] | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Extract all features from an ad record.

    Args:
        ad: Normalized CompetitorAd dict with optional ProductPage enrichment
        creative_features: this ad's merged Step 2 output — flatten_features()
            plus color_profile's 4 fields (dominant_hex_palette,
            background_hex, contrast_ratio_type, background_style), keyed by
            ad_id by the caller. None when Step 2 hasn't been run for this ad
            yet — every creative_* feature falls back to its own null/unknown
            default rather than being silently omitted from the row.
        category_stats: Category-level statistics for price normalization (optional)
        embedding_client: injectable for offline tests / a shared client
            across a batch run; defaults to a real EmbeddingClient (real
            Replicate API calls — see extract_embedding_features).

    Returns:
        (features, price_tier) — price_tier is returned separately from the
        feature dict, not as one of its keys: it's a segmentation label (see
        product_features.calculate_price_tier), not a model input. Same
        treatment product_category already got ("for segmentation only").
    """
    features = {}
    creative_features = creative_features or {}
    product_page_raw = ad.get("product_page")
    usp = product_page_raw.get("usp") if isinstance(product_page_raw, dict) else None

    # 1. TEXT EMBEDDINGS (3 features)
    embeddings = extract_embedding_features(
        title=ad.get("title"),
        body=ad.get("body"),
        usp=usp,
        client=embedding_client,
    )
    features.update(embeddings)

    # 2. TEXT LENGTH FEATURES (3 features)
    text_lengths = extract_text_length_features(
        title=ad.get("title"),
        body=ad.get("body"),
        usp=usp,
    )
    features.update(text_lengths)

    # 3. LANGUAGE SIGNALS (2 features)
    full_text = " ".join(filter(None, [ad.get("title"), ad.get("body"), usp]))
    language_signals = extract_language_signals(full_text if full_text else None)
    features.update(language_signals)

    # 4. CTA FEATURES (2 features)
    cta_features = extract_cta_features(ad.get("cta_text"))
    features.update(cta_features)

    # 5. POSITIONING FEATURES (2 features)
    price = product_page_raw.get("price") if isinstance(product_page_raw, dict) else None
    category_median_price = (
        category_stats.get("price_median") if category_stats else None
    )
    positioning = extract_positioning_features(
        price=price,
        category_median_price=category_median_price,
        text=full_text if full_text else None,
    )
    features.update(positioning)

    # 6. COLOR FEATURES (5 features) — real Step 2 ColorProfile data when
    # available (background_hex as the "dominant" swatch, falling back to
    # the first entry of the extracted palette; palette_vibrancy computed
    # from the whole palette, not a direct Step 2 field). Both null/unknown
    # when creative_features wasn't supplied, matching every other
    # not-yet-processed field's behavior rather than a hardcoded guess.
    hex_palette = creative_features.get("dominant_hex_palette") or []
    dominant_hex = creative_features.get("background_hex") or (
        hex_palette[0] if hex_palette else None
    )
    color_features = extract_color_features(
        dominant_hex=dominant_hex,
        palette_vibrancy=compute_palette_vibrancy(hex_palette),
        contrast_ratio_type=creative_features.get("contrast_ratio_type"),
        background_style=creative_features.get("background_style"),
    )
    features.update(color_features)

    # 7. PRODUCT FEATURES (4 features — price/product_category excluded,
    # both segmentation-only; price_tier computed and returned separately)
    product_features = extract_product_features(product_page_raw)
    features.update(product_features)
    price_tier = calculate_price_tier(price)

    # 8. ACCOUNT FEATURES (2 features)
    features.update({
        "days_active": ad.get("days_active", 0),
        "collation_count": ad.get("collation_count", 0),
    })

    # 9. PLATFORM FEATURES (1 feature)
    platforms = ad.get("publisher_platforms", [])
    features["publisher_count"] = len(platforms) if platforms else 0

    # 10. STEP 2 CREATIVE TEXT/PLACEMENT FEATURES (~28 features)
    for key in _STEP2_FLATTENED_KEYS:
        features[f"creative_{key}"] = creative_features.get(key)

    # 11. CAMPAIGN-TAXONOMY FEATURES (5 features) — parsed from link_url's
    # UTM params, X-axis signals describing campaign setup/intent (test vs
    # scale), not ad performance itself. See ingestion/utm_features.py.
    features.update(extract_campaign_features(ad.get("link_url")))

    return features, price_tier
