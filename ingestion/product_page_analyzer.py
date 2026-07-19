"""Stage 4c: LLM-based semantic product extraction.

Enriches ProductPage with fields beyond structured data (category, USP, cultural
branding, variants) by analyzing HTML content with Gemini.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ingestion.product_page import ProductPage
from pipeline.clients.genai_client import GenAIClient
from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


class SemanticExtraction(BaseModel):
    """Intermediate schema for LLM extraction of semantic product fields."""

    product_category: str = Field(default="", description="e.g., 'Supplements', 'Apparel'")
    product_subcategory: str = Field(
        default="", description="e.g., 'Pre-Workout', 'Amino Acids'"
    )
    usp: str = Field(
        default="",
        description="Unique selling proposition (max 150 chars): what makes this product special?",
    )
    cultural_branding: list[str] = Field(
        default_factory=list,
        description='e.g., ["American Made", "European", "Vegan"] — brand identity signals',
    )
    variants_featured: list[str] = Field(
        default_factory=list,
        description="e.g., ['Flavor: Strawberry', 'Size: 500g'] — variants shown in ad/page",
    )
    shows_all_variants: bool = Field(
        default=False,
        description="True if product page/ad shows multiple SKUs or a variant selector",
    )
    price_range: str = Field(
        default="",
        description="If multiple prices found: e.g., '$19.99-$29.99'. Otherwise empty.",
    )


def extract_semantic_fields(
    html: str, partial_product: ProductPage
) -> ProductPage | None:
    """Extract semantic fields from HTML using Gemini.

    Args:
        html: Full HTML content of the landing page.
        partial_product: ProductPage with already-extracted structured data.

    Returns:
        Enriched ProductPage with semantic fields filled, or None on error.
    """
    if not html or not partial_product.product_name:
        logger.warning("extract_semantic_skipped", reason="missing_html_or_product_name")
        return None

    settings = get_settings()
    client = GenAIClient()

    # Construct prompt
    prompt = f"""Analyze HTML for a product page and extract semantic information.

**Known product info:**
- Name: {partial_product.product_name}
- Brand: {partial_product.brand_name or "(not known)"}
- Price: ${partial_product.price or "(not known)"} {partial_product.price_currency}
- Rating: {partial_product.rating or "(not known)"}

**Your task:**
1. Category: Broad category (e.g., "Supplements", "Apparel", "Electronics")
2. Subcategory: Specific sub (e.g., "Pre-Workout", "Amino Acids")
3. USP: What makes it special? (max 150 chars)
4. Cultural branding: Brand signals (e.g., "American Made", "Eco-Friendly")
5. Variants featured: Variants mentioned (e.g., "Flavor: Strawberry", "Size: 500g")
6. Shows all variants: Multiple SKUs or variant selector present?
7. Price range: If multiple prices "$X-$Y"; else empty.

Look in: title/description, breadcrumbs, variant selectors, pricing options,
marketing copy with certifications/origins, meta tags.

Respond with JSON matching the schema."""

    try:
        extraction = client.extract_structured_text(
            model=settings.gemini_cheap_model,
            prompt=prompt,
            schema=SemanticExtraction,
        )
    except Exception as e:
        logger.warning(
            "extract_semantic_failed",
            error=str(e),
            product_name=partial_product.product_name,
        )
        return None

    # Merge semantic extraction with partial product
    enriched = partial_product.model_copy(
        update={
            "product_category": extraction.product_category or partial_product.product_category,
            "product_subcategory": extraction.product_subcategory,
            "usp": extraction.usp,
            "cultural_branding": extraction.cultural_branding or [],
            "variants_featured": extraction.variants_featured or [],
            "shows_all_variants": extraction.shows_all_variants,
            "price_range": extraction.price_range or "",
            "extraction_method": "structured_data+llm",
            "confidence": 0.75,  # Slightly lower than structured-only
        }
    )

    logger.debug(
        "extract_semantic_succeeded",
        product_name=enriched.product_name,
        category=enriched.product_category,
        confidence=enriched.confidence,
    )

    return enriched
