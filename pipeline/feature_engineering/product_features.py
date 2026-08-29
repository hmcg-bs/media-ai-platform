"""Extract product-based features from enrichment data."""

from __future__ import annotations

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

    Deliberately excludes `price` and `product_category` as raw feature-dict
    keys: price is a segmentation dimension (see calculate_price_tier), and
    product_category is for segmentation only per the plan's own rule — both
    are returned separately by the caller (extract_all_features), not mixed
    into the numeric/categorical feature row itself."""
    if not product_page:
        return {
            "rating": None,
            "shows_all_variants": False,
            "variants_featured_count": 0,
            "cultural_branding_count": 0,
        }

    rating = product_page.get("rating")
    variants = product_page.get("variants_featured", [])
    cultural_branding = product_page.get("cultural_branding", [])

    return {
        "rating": rating,
        "shows_all_variants": product_page.get("shows_all_variants", False),
        "variants_featured_count": len(variants) if variants else 0,
        "cultural_branding_count": len(cultural_branding) if cultural_branding else 0,
    }
