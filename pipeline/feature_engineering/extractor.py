"""Main feature extractor orchestrating all feature categories."""

from __future__ import annotations

from typing import Any

from pipeline.feature_engineering.color_features import extract_color_features
from pipeline.feature_engineering.embeddings import extract_embedding_features
from pipeline.feature_engineering.product_features import extract_product_features
from pipeline.feature_engineering.text_features import (
    extract_cta_features,
    extract_language_signals,
    extract_positioning_features,
    extract_text_length_features,
)


def extract_all_features(
    ad: dict[str, Any],
    category_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Extract all 94 features from an ad record.

    Args:
        ad: Normalized CompetitorAd dict with optional ProductPage enrichment
        category_stats: Category-level statistics for price normalization (optional)

    Returns:
        Dict with all extracted features
    """
    features = {}

    # 1. TEXT EMBEDDINGS (4 features)
    embeddings = extract_embedding_features(
        title=ad.get("title"),
        body=ad.get("body"),
        usp=ad.get("product_page", {}).get("usp") if isinstance(ad.get("product_page"), dict) else None,
    )
    features.update(embeddings)

    # 2. TEXT LENGTH FEATURES (3 features)
    text_lengths = extract_text_length_features(
        title=ad.get("title"),
        body=ad.get("body"),
        usp=ad.get("product_page", {}).get("usp") if isinstance(ad.get("product_page"), dict) else None,
    )
    features.update(text_lengths)

    # 3. LANGUAGE SIGNALS (2 features)
    full_text = " ".join(
        filter(None, [
            ad.get("title"),
            ad.get("body"),
            ad.get("product_page", {}).get("usp") if isinstance(ad.get("product_page"), dict) else None,
        ])
    )
    language_signals = extract_language_signals(full_text if full_text else None)
    features.update(language_signals)

    # 4. CTA FEATURES (2 features)
    cta_features = extract_cta_features(ad.get("cta_text"))
    features.update(cta_features)

    # 5. POSITIONING FEATURES (2 features)
    product_page = ad.get("product_page")
    price = product_page.get("price") if isinstance(product_page, dict) else None
    category_median_price = (
        category_stats.get("price_median") if category_stats else None
    )
    positioning = extract_positioning_features(
        price=price,
        category_median_price=category_median_price,
        text=full_text if full_text else None,
    )
    features.update(positioning)

    # 6. COLOR FEATURES (5 features)
    # Placeholder: would come from Step 2 creative analysis
    color_features = extract_color_features(
        dominant_hex="#808080",  # Placeholder
        palette_vibrancy=0.5,  # Placeholder
        contrast_ratio_type="medium",  # Placeholder
        background_style="unknown",  # Placeholder
    )
    features.update(color_features)

    # 7. PRODUCT FEATURES (6 features)
    product_features = extract_product_features(product_page)
    features.update(product_features)

    # 8. ACCOUNT FEATURES (2 features)
    features.update({
        "days_active": ad.get("days_active", 0),
        "collation_count": ad.get("collation_count", 0),
    })

    # 9. PLATFORM FEATURES (1 feature)
    platforms = ad.get("publisher_platforms", [])
    features["publisher_count"] = len(platforms) if platforms else 0

    return features
