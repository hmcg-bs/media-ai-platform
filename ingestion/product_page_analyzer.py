"""Stage 4c: LLM-based semantic product extraction.

Enriches ProductPage with fields beyond structured data (category, USP, cultural
branding, variants) by analyzing HTML content with Gemini.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ingestion.product_page import ProductPage
from pipeline.clients.replicate_client import ReplicateVisionClient
from pipeline.logger import get_logger

logger = get_logger(__name__)

_TAG_STRIP_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def _extract_visible_text(html: str, max_chars: int = 15000) -> str:
    """Strip an HTML page down to visible text for LLM consumption.

    No HTML parser dependency in this project yet, so this is a plain regex
    strip: drop script/style/noscript blocks entirely, drop remaining tags,
    collapse whitespace. Good enough for giving the model something to read
    instead of nothing — category/USP/breadcrumb text usually survives this.

    max_chars defaults high (15k, ~4k tokens, ~$0.003/call at current Gemini
    pricing) because real product description content is often well past the
    first few thousand characters — cart drawers, nav menus, and search
    modals eat the budget first on most storefront templates (confirmed
    empirically: a real page's actual ingredient/USP copy started at ~char
    4000, entirely missed at the previous 4000-char cap).
    """
    text = _TAG_STRIP_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n", text)
    text = text.strip()
    return text[:max_chars]


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


# Shared verbatim across both prompt builders below so the JSON keys the
# model is told to use can never again drift from SemanticExtraction's
# actual field names — that mismatch (Title Case prompt headers vs.
# snake_case schema fields) was a real, previously-shipped bug: Pydantic
# validated the mismatched response successfully (every field has a
# default) and silently returned an all-empty object with no error.
_SEMANTIC_JSON_INSTRUCTIONS = (
    "Respond only with JSON matching this schema, no other text (use these exact key names):\n"
    '{"product_category": "...", "product_subcategory": "...", "usp": "...", '
    '"cultural_branding": ["..."], "variants_featured": ["..."], '
    '"shows_all_variants": true/false, "price_range": "..."}'
)


def _merge_semantic_extraction(
    partial_product: ProductPage,
    extraction: SemanticExtraction,
    extraction_method: str,
    confidence: float,
) -> ProductPage:
    """setdefault-style merge: the LLM's value only wins when it actually
    found something. A blind overwrite here previously discarded correct
    variants_featured/price_range/shows_all_variants data that an earlier
    tier (e.g. shopify_json's native variants[] parse) had already extracted,
    every time the LLM's own pass came back empty on those fields — confirmed
    live as the dominant cause of variants_featured's corpus-wide coverage
    gap."""
    return partial_product.model_copy(
        update={
            "product_category": extraction.product_category or partial_product.product_category,
            "product_subcategory": extraction.product_subcategory
            or partial_product.product_subcategory,
            "usp": extraction.usp or partial_product.usp,
            "cultural_branding": extraction.cultural_branding or partial_product.cultural_branding,
            "variants_featured": extraction.variants_featured or partial_product.variants_featured,
            "shows_all_variants": extraction.shows_all_variants
            or partial_product.shows_all_variants,
            "price_range": extraction.price_range or partial_product.price_range,
            "extraction_method": extraction_method,
            "confidence": confidence,
        }
    )


def extract_semantic_fields_from_shopify_json(
    shopify_data: dict,
    partial_product: ProductPage,
    ad_context: dict[str, str] | None = None,
) -> ProductPage | None:
    """Cheaper, better-grounded sibling of extract_semantic_fields: builds
    the prompt from Shopify's own structured product JSON (title, vendor,
    tags, product_type, body_html) instead of a full stripped HTML page.
    body_html is already just the product description — no cart/nav/search
    boilerplate to skip past — so it needs a much smaller character budget
    than _extract_visible_text's 15k.

    Returns enriched ProductPage, or None on error / missing product data.
    """
    product = shopify_data.get("product") if isinstance(shopify_data, dict) else None
    if not product or not partial_product.product_name:
        logger.warning("extract_semantic_shopify_skipped", reason="missing_product_or_name")
        return None

    client = ReplicateVisionClient()

    ad_context_str = ""
    if ad_context:
        ad_context_str = "\n**Marketing Context from Ad:**\n"
        if ad_context.get("title"):
            ad_context_str += f"- Title: {ad_context['title']}\n"
        if ad_context.get("body"):
            ad_context_str += f"- Body: {ad_context['body'][:300]}\n"
        if ad_context.get("caption"):
            ad_context_str += f"- Caption: {ad_context['caption']}\n"

    body_html = product.get("body_html") or ""
    description = re.sub(r"<[^>]+>", " ", body_html)
    description = re.sub(r"\s+", " ", description).strip()[:3000]
    tags = product.get("tags", "")

    prompt = f"""Extract semantic product info from this Shopify product listing.

**Known product info:**
- Name: {partial_product.product_name}
- Vendor/Brand: {partial_product.brand_name or "(not known)"}
- Shopify product_type (low-trust — sometimes contains A/B-test bucket names, not a real category): {product.get("product_type", "(none)")}
- Shopify tags (often a better category signal than product_type): {tags or "(none)"}
- Price: ${partial_product.price or "(not known)"} {partial_product.price_currency}{ad_context_str}

**Product description:**
{description}

**Extract these fields:**

1. **Category:** Broad category (Supplements, Apparel, Electronics, Furniture, etc.) — judge from tags/description, don't just copy product_type verbatim
2. **Subcategory:** Specific sub (Pre-Workout, Amino Acids, Wooden Furniture, etc.)
3. **USP:** What makes it special? Max 150 chars (Vegan, Non-GMO, Lab-tested, etc.)
4. **Cultural branding:** Brand signals as list (American Made, Hand-crafted, Eco, etc.)
5. **Variants featured:** Variants as list (Flavor: Strawberry, Size: 500g, etc.)
6. **Shows all variants:** Boolean - multiple SKUs or variant selector present?
7. **Price range:** If multiple prices "$X-$Y"; else empty.

{_SEMANTIC_JSON_INSTRUCTIONS}"""

    try:
        extraction = client.extract_structured_text(prompt=prompt, schema=SemanticExtraction)
    except Exception as e:
        logger.warning(
            "extract_semantic_shopify_failed", error=str(e), product_name=partial_product.product_name
        )
        return None

    enriched = _merge_semantic_extraction(
        partial_product, extraction, extraction_method="shopify_json+llm", confidence=0.85
    )
    logger.debug(
        "extract_semantic_shopify_succeeded",
        product_name=enriched.product_name,
        category=enriched.product_category,
        confidence=enriched.confidence,
    )
    return enriched


def extract_semantic_fields(
    html: str,
    partial_product: ProductPage,
    ad_context: dict[str, str] | None = None,
) -> ProductPage | None:
    """Extract semantic fields from HTML using Gemini, with optional ad context.

    Args:
        html: Full HTML content of the landing page.
        partial_product: ProductPage with already-extracted structured data.
        ad_context: Optional ad marketing context: {"title": "...", "body": "...", "caption": "..."}

    Returns:
        Enriched ProductPage with semantic fields filled, or None on error.
    """
    if not html or not partial_product.product_name:
        logger.warning("extract_semantic_skipped", reason="missing_html_or_product_name")
        return None

    client = ReplicateVisionClient()

    # Build ad context section if provided
    ad_context_str = ""
    if ad_context:
        ad_context_str = "\n**Marketing Context from Ad:**\n"
        if ad_context.get("title"):
            ad_context_str += f"- Title: {ad_context['title']}\n"
        if ad_context.get("body"):
            ad_context_str += f"- Body: {ad_context['body'][:300]}\n"
        if ad_context.get("caption"):
            ad_context_str += f"- Caption: {ad_context['caption']}\n"

    page_text = _extract_visible_text(html)

    # Construct prompt
    prompt = f"""Extract semantic product info from this HTML page.

**Known product info:**
- Name: {partial_product.product_name}
- Brand: {partial_product.brand_name or "(not known)"}
- Price: ${partial_product.price or "(not known)"} {partial_product.price_currency}
- Rating: {partial_product.rating or "(not known)"}{ad_context_str}

**Page text (visible content, tags stripped):**
{page_text}

**Extract these fields:**

1. **Category:** Broad category (Supplements, Apparel, Electronics, Furniture, etc.)
2. **Subcategory:** Specific sub (Pre-Workout, Amino Acids, Wooden Furniture, etc.)
3. **USP:** What makes it special? Max 150 chars (Vegan, Non-GMO, Lab-tested, etc.)
4. **Cultural branding:** Brand signals as list (American Made, Hand-crafted, Eco, etc.)
5. **Variants featured:** Variants as list (Flavor: Strawberry, Size: 500g, etc.)
6. **Shows all variants:** Boolean - multiple SKUs or variant selector present?
7. **Price range:** If multiple prices "$X-$Y"; else empty.

Look for this information in:
- Product title and description
- Category breadcrumbs or navigation
- Variant selectors (dropdowns, radio buttons, tabs)
- Price options for different sizes/colors/flavors
- Marketing copy mentioning certifications, origins, craftsmanship
- Meta tags and structured text

{_SEMANTIC_JSON_INSTRUCTIONS}"""

    try:
        extraction = client.extract_structured_text(
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

    enriched = _merge_semantic_extraction(
        partial_product, extraction, extraction_method="structured_data+llm", confidence=0.75
    )

    logger.debug(
        "extract_semantic_succeeded",
        product_name=enriched.product_name,
        category=enriched.product_category,
        confidence=enriched.confidence,
    )

    return enriched
