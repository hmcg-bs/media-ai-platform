"""Extract product-based features from enrichment data."""

from __future__ import annotations

import math
from typing import Any

# Fixed-dollar price tiers, matching Phase 1's data-exploration report
# exactly (data/supplements_enriched.json, 2,617-ad filtered set: p10 $11.99,
# median $44.99, p90 $70.00) — user decision: segment by these tiers rather
# than feed raw price into the model as a numeric feature. Fixed boundaries,
# not quartile-based, so tier membership stays stable across reprocessing
# runs instead of shifting whenever the corpus's price distribution moves.
_PRICE_TIER_BOUNDARIES = (15.0, 35.0, 60.0)
_PRICE_TIER_LABELS = ("budget", "mid", "premium", "luxury_bundle")


def calculate_price_tier(price: float | None) -> str:
    """Categorizes price into one of Phase 1's four fixed-dollar tiers:
    budget (<$15), mid ($15-35), premium ($35-60), luxury_bundle ($60+).
    Returns "unknown" when price isn't known — a real, common case (Phase 1
    found price known on only ~72% of the corpus), distinct from any real
    tier."""
    if price is None:
        return "unknown"
    for boundary, label in zip(_PRICE_TIER_BOUNDARIES, _PRICE_TIER_LABELS):
        if price < boundary:
            return label
    return _PRICE_TIER_LABELS[-1]


def extract_product_features(
    product_page: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract product-based features from ProductPage enrichment.

    Deliberately excludes raw `price` and `product_category`: raw prices are
    not comparable across currencies/bundles, while category is a segment.
    The caller adds the stable categorical price tier to the model row."""
    if not product_page:
        return {
            "rating": None,
            "rating_count_log1p": None,
            "has_rating": False,
            "has_product_description": False,
            "product_description_word_count": None,
            "product_usp_word_count": None,
            "subscription_status": "unknown",
            "shows_all_variants": False,
            "variants_featured_count": 0,
            "cultural_branding_count": 0,
        }

    rating = product_page.get("rating")
    rating_count = product_page.get("rating_count")
    description = str(product_page.get("marketing_copy") or "").strip()
    usp = str(product_page.get("usp") or "").strip()
    variants = product_page.get("variants_featured", [])
    cultural_branding = product_page.get("cultural_branding", [])

    return {
        "rating": rating,
        "rating_count_log1p": math.log1p(rating_count) if rating_count is not None else None,
        "has_rating": rating is not None,
        "has_product_description": bool(description or usp),
        "product_description_word_count": len(description.split()) if description else None,
        "product_usp_word_count": len(usp.split()) if usp else None,
        "subscription_status": product_page.get("subscription_status") or "unknown",
        "shows_all_variants": product_page.get("shows_all_variants", False),
        "variants_featured_count": len(variants) if variants else 0,
        "cultural_branding_count": len(cultural_branding) if cultural_branding else 0,
    }
