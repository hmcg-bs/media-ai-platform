"""Product page extraction model and utilities for Stage 4 landing-page analysis.

Extracts product metadata (name, category, price, brand, variants) from ad landing pages
to validate and categorize products for pattern discovery.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SubscriptionStatus = Literal[
    "one_time_only", "subscription_optional", "subscription_required", "unknown"
]


class ProductPage(BaseModel):
    """Extracted product information from a landing page (e-commerce or brand site)."""

    # Product identification
    product_name: str = ""
    product_category: str = ""  # e.g., "Supplements", "Apparel"
    product_subcategory: str = ""  # e.g., "Pre-Workout", "Amino Acids"

    # Brand & pricing
    brand_name: str = ""
    price: float | None = None  # in detected currency; the one-time/standard price
    price_currency: str = "USD"
    price_range: str = ""  # e.g., "$19.99-$29.99" for variable pricing

    # Subscription commerce — a separate axis from price/price_range, since a
    # page can offer both a one-time price and a distinct recurring price
    # (e.g. "$29.95 one-time" vs "$23.96/mo on subscription"). "unknown"
    # (the default) means neither a subscription-app signature nor
    # subscription-indicating text was found — a real coverage ceiling given
    # detection is signature/keyword-based, not proof of absence.
    subscription_status: SubscriptionStatus = "unknown"
    subscription_price: float | None = None  # recurring price, distinct from `price`

    # Marketplace & content
    marketplace_region: str = ""  # e.g. "us", "gb", "de" — which marketplace
    # (e.g. Amazon's per-country storefront) this landing page targets;
    # deterministic (URL domain TLD), empty when not a recognized
    # multi-region marketplace or not yet detected.
    rating: float | None = None  # 0-5 star rating
    rating_count: int | None = None  # number of reviews
    marketing_copy: str = ""  # product description / headline
    usp: str = ""  # unique selling points (e.g., "Vegan, Non-GMO, Lab-Tested")

    # Cultural / brand signals
    cultural_branding: list[str] = Field(
        default_factory=list
    )  # e.g., ["American Made", "European"]

    # Variant context (what variants are visible/featured in the ad)
    variants_featured: list[str] = Field(
        default_factory=list
    )  # e.g., ["Flavor: Strawberry", "Size: 500g"]
    shows_all_variants: bool = False  # True if page displays multiple SKUs/variants

    # Extraction metadata
    extraction_method: str = ""  # "structured_data" | "vision" | "llm" | "fallback"
    confidence: float = 0.0  # 0-1, confidence in extraction quality
    url: str = ""  # the link_url we scraped
    fallback_used: bool = False  # True if only ad title/URL available
