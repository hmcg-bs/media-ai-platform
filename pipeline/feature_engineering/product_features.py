"""Extract product-based features from enrichment data."""

from __future__ import annotations

from typing import Any


def extract_product_features(
    product_page: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract product-based features from ProductPage enrichment."""
    if not product_page:
        return {
            "price": None,
            "price_normalized": None,
            "rating": None,
            "shows_all_variants": False,
            "variants_featured_count": 0,
            "cultural_branding_count": 0,
            "product_category": None,
        }

    price = product_page.get("price")
    rating = product_page.get("rating")
    variants = product_page.get("variants_featured", [])
    cultural_branding = product_page.get("cultural_branding", [])

    return {
        "price": price,
        "price_normalized": None,  # Will be computed per-category
        "rating": rating,
        "shows_all_variants": product_page.get("shows_all_variants", False),
        "variants_featured_count": len(variants) if variants else 0,
        "cultural_branding_count": len(cultural_branding) if cultural_branding else 0,
        "product_category": product_page.get("product_category"),
    }


def normalize_price_per_category(
    price: float | None,
    category_stats: dict[str, float],
) -> float | None:
    """Normalize price by category statistics (mean, std)."""
    if price is None:
        return None

    mean = category_stats.get("mean", 0)
    std = category_stats.get("std", 1)

    if std == 0:
        return 0.0

    return (price - mean) / std


def calculate_price_tier(
    price: float | None,
    category_stats: dict[str, float],
) -> str:
    """Categorize price into tier (budget, mid, premium)."""
    if price is None:
        return "unknown"

    q25 = category_stats.get("q25", 0)
    q75 = category_stats.get("q75", 0)

    if price < q25:
        return "budget"
    elif price < q75:
        return "mid"
    else:
        return "premium"
