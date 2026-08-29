"""Schema for direct-response "advertorial" funnel page extraction (Tier 4.5/5).

Intermediate/diagnostic result type paralleling ZenRowsProductData's role in
zenrows_scraper.py — not a ProductPage replacement. Custom page-builder
(PageFly/Zipify/GemPages/ReConvert) and fully-custom-theme funnel pages carry
richer structure (multi-tier pricing offers, page-builder provenance) than
ProductPage's flat price/rating fields capture. The bridge into ProductPage
lives in zenrows_scraper.py (_merge_direct_response_into + to_product_page_updates)
— the single canonical merge path, not duplicated here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PageType(StrEnum):
    STANDARD_ECOM = "standard_ecom"
    ADVERTORIAL_FUNNEL = "advertorial_funnel"


class ExtractionTier(StrEnum):
    TIER_1_4_STRUCTURED = "tier_1_4_structured"
    TIER_4_5_BUILDER = "tier_4_5_builder"
    TIER_5_LLM = "tier_5_llm"


class PageMetadata(BaseModel):
    canonical_url: str
    original_url: str
    page_type: PageType
    content_hash: str
    builder_detected: str | None = None


class ProductInfo(BaseModel):
    title: str | None = None
    brand_name: str | None = None
    key_specs: list[str] = Field(default_factory=list)


class SocialProof(BaseModel):
    review_count: int | None = None
    rating_value: float | None = None


class OfferTier(BaseModel):
    tier_label: str
    quantity: int
    total_price: float | None = None
    price_per_unit: float | None = None
    currency: str
    is_best_value: bool = False


class DirectResponseProductData(BaseModel):
    page_metadata: PageMetadata
    product_info: ProductInfo = Field(default_factory=ProductInfo)
    social_proof: SocialProof = Field(default_factory=SocialProof)
    offer_matrix: list[OfferTier] = Field(default_factory=list)
    guarantee_terms: str | None = None
    extraction_tier: ExtractionTier


def best_offer(offer_matrix: list[OfferTier]) -> OfferTier | None:
    """Prefer the flagged best-value tier; else the single-unit (qty=1) tier;
    else the cheapest per-unit price — never the highest total_price, which
    would silently pick a bulk bundle as "the" price."""
    if not offer_matrix:
        return None
    for offer in offer_matrix:
        if offer.is_best_value:
            return offer
    for offer in offer_matrix:
        if offer.quantity == 1:
            return offer
    priced = [o for o in offer_matrix if o.price_per_unit is not None]
    if priced:
        return min(priced, key=lambda o: o.price_per_unit)  # type: ignore[arg-type,return-value]
    return offer_matrix[0]
