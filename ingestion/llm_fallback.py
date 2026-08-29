"""Tier 5: zone-pruned LLM fallback extraction for advertorial pages that
neither the structured-data cascade (Tiers 1-4) nor builder fingerprinting
(Tier 4.5) resolved.

Follows ingestion/product_page_analyzer.py's established shape: Replicate
Gemini via ReplicateVisionClient.extract_structured_text() (the project's
ADR-008 standing choice), a literal snake_case JSON template embedded in the
prompt (Pydantic won't catch a Title-Case-vs-snake_case field-name drift —
every field has a default — this was a real, previously-shipped bug fixed
once already in product_page_analyzer.py; see _SEMANTIC_JSON_INSTRUCTIONS
there), and a catch-all try/except that degrades to None rather than raising.

validate_against_raw_text() is the hallucination guardrail: numeric claims
(price, review count) that don't appear verbatim in the page's own visible
text get nulled out rather than trusted.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from ingestion.dedupe import canonicalize_url
from ingestion.direct_response_schema import (
    DirectResponseProductData,
    ExtractionTier,
    OfferTier,
    PageMetadata,
    PageType,
    ProductInfo,
    SocialProof,
)
from pipeline.clients.replicate_client import ReplicateVisionClient
from pipeline.logger import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """You are an expert e-commerce data extraction model specializing in
direct-response advertorial landing pages. Extract product details, social proof, multi-tier
pricing offers, and specs from the provided Markdown into the required JSON schema.

Rules:
- Extract numeric values precisely as written (e.g., convert "$29.95" to 29.95).
- For offer matrices, distinguish single-unit pricing from multi-item bundle tiers.
- Do NOT invent or hallucinate data. If a field is not present in the markdown, set it to null.

For every dollar amount you consider including in offer_matrix, classify its price_context —
a page frequently mentions several dollar amounts that are NOT the product's own purchase
price, and including the wrong one is a real, confirmed failure mode:
- "real_offer": this IS the product's actual purchase price (a single-unit or standard price).
- "bundle_price": this IS the product's purchase price, but for a multi-unit bundle/tier (e.g.
  "Buy 2 Get 2 Free", "3-Pack"). For bundle offers, set quantity to the TOTAL number of units
  included, and reason explicitly: if the page states a per-unit price, total_price = quantity
  * price_per_unit; if the page states a total price, price_per_unit = total_price / quantity.
  Populate BOTH total_price and price_per_unit even if only one appears verbatim on the page —
  the arithmetic is legitimate, not a hallucination.
- "shipping_or_promo_banner": a shipping threshold or promo banner amount (e.g. "Free shipping
  on orders over $50"), NOT the product's own price. Do not include as an offer.
- "rhetorical_or_competitor_price": a price mentioned rhetorically or about a DIFFERENT/
  competitor product (e.g. "Spending $1,029 on Ozempic every month"), NOT this product's own
  price. Do not include as an offer.
- "cart_subtotal_widget": a cart/checkout subtotal display (often "$0.00" on an empty cart),
  NOT a listed product price. Do not include as an offer.

Some pages are review/comparison-table content ranking several competing products against each
other (e.g. "Top 5 Supplements", a comparison chart with a column per product). On these pages,
identify the ONE featured/primary product — usually the top-ranked entry, the one with a "Buy
Now"/"Visit Site"/"Our Pick"/"#1" callout, or the one the rest of the page's copy is written
about — and classify only THAT product's own price as "real_offer" (or "bundle_price"). Prices
shown for the other ranked/compared products are "rhetorical_or_competitor_price" — do not
include them as offers even though they appear in the same table/list structure.

Only include offer_matrix entries whose price_context is "real_offer" or "bundle_price".

rating_value must be a customer star rating on a 0-5 scale (e.g. "4.8 out of 5 stars",
"4.7/5"). Review/comparison sites ("Top 5 X Supplements") often also print an editorial
score out of 10 (e.g. "Editor's Score: 9.8/10", "Overall Rating: 9.6") — that is NOT a
5-star rating and must NOT be put in rating_value; leave rating_value null if the only
rating-like number on the page is an out-of-10 (or other non-5-point) editorial score."""


class _LLMOfferTier(BaseModel):
    tier_label: str | None = None
    quantity: int | None = None
    total_price: float | None = None
    price_per_unit: float | None = None
    currency: str | None = None
    is_best_value: bool | None = None
    price_context: (
        Literal[
            "real_offer",
            "bundle_price",
            "shipping_or_promo_banner",
            "rhetorical_or_competitor_price",
            "cart_subtotal_widget",
        ]
        | None
    ) = None


class _DirectResponseLLMExtraction(BaseModel):
    """LLM-facing schema — only the fields the model should produce.
    page_metadata/extraction_tier are computed in code, not by the model.

    Every field is Optional even where the prompt's JSON template implies a
    required type (e.g. key_specs/offer_matrix as arrays) — confirmed live
    against the real model: it emits explicit JSON `null` for any field it
    has no data for, including array/string fields, not just the `... or
    null`-flagged numeric ones. A stricter schema here means Pydantic
    validation fails on the *majority* of real responses (confirmed: every
    call in an initial live smoke test failed this way) and Tier 5 silently
    returns None every time via the catch-all below — never a signal, just
    silent 100% failure. Same bug class as the Title-Case-vs-snake_case
    issue already fixed once in product_page_analyzer.py: trust the model's
    real behavior over the prompt's implied shape."""

    product_title: str | None = None
    brand_name: str | None = None
    key_specs: list[str] | None = None
    review_count: int | None = None
    rating_value: float | None = None
    offer_matrix: list[_LLMOfferTier] | None = None
    guarantee_terms: str | None = None


# Shared verbatim with the prompt below so the JSON keys the model is told to
# use can never drift from _DirectResponseLLMExtraction's actual field names
# — see module docstring for why this matters.
_DIRECT_RESPONSE_JSON_INSTRUCTIONS = (
    "Respond only with JSON matching this schema, no other text (use these exact key names). "
    "Any field may be null if that information isn't present in the text:\n"
    '{"product_title": "..." or null, "brand_name": "..." or null, '
    '"key_specs": ["..."] or null, "review_count": 123 or null, "rating_value": 4.5 or null, '
    '"offer_matrix": [{"tier_label": "...", "quantity": 1, "total_price": 39.95, '
    '"price_per_unit": 39.95, "currency": "USD", "is_best_value": true/false, '
    '"price_context": "real_offer"|"bundle_price"|"shipping_or_promo_banner"'
    '|"rhetorical_or_competitor_price"|"cart_subtotal_widget"}] or null, '
    '"guarantee_terms": "..." or null}'
)


# price_context values that mean "this dollar amount is not the product's
# own purchase price" — discarded before building offer_matrix rather than
# trusted just because the model returned a well-formed OfferTier for it.
_DISCARDED_PRICE_CONTEXTS = frozenset(
    {"shipping_or_promo_banner", "rhetorical_or_competitor_price", "cart_subtotal_widget"}
)


def extract_via_llm(pruned_markdown: str, url: str) -> DirectResponseProductData | None:
    """Runs the Tier 5 LLM extraction over already-pruned Markdown (see
    ingestion/zone_pruner.py). Never raises — returns None on any failure,
    matching product_page_analyzer.py's degrade-gracefully pattern. Caller
    is expected to run validate_against_raw_text() on a non-None result
    before trusting it, and to fill in page_metadata.content_hash from the
    original raw HTML (not available here — this function only sees the
    pruned Markdown, kept minimal/testable)."""
    if not pruned_markdown.strip():
        logger.warning("extract_direct_response_llm_skipped", url=url, reason="empty_markdown")
        return None

    client = ReplicateVisionClient()

    prompt = f"""{_SYSTEM_PROMPT}

**Page content (Markdown, pruned to the hero/offer, social-proof, and specs zones):**
{pruned_markdown}

{_DIRECT_RESPONSE_JSON_INSTRUCTIONS}"""

    try:
        extraction = client.extract_structured_text(
            prompt=prompt, schema=_DirectResponseLLMExtraction
        )
    except Exception as e:  # noqa: BLE001 — one bad page shouldn't kill the batch
        logger.warning("extract_direct_response_llm_failed", url=url, error=str(e))
        return None

    kept_offers: list[OfferTier] = []
    for o in extraction.offer_matrix or []:
        # Logged for every candidate, kept or not — this is the production
        # telemetry half of the price-context guardrail (the golden-set eval
        # in pipeline/validation/price_context_validator.py is the offline
        # half): lets a future audit query real category frequency instead
        # of only ever seeing the filtered survivors.
        logger.info(
            "price_context_classified",
            url=url,
            price_context=o.price_context,
            total_price=o.total_price,
            price_per_unit=o.price_per_unit,
        )
        if o.price_context in _DISCARDED_PRICE_CONTEXTS:
            continue
        kept_offers.append(
            OfferTier(
                tier_label=o.tier_label or "",
                quantity=o.quantity if o.quantity is not None else 1,
                total_price=o.total_price,
                price_per_unit=o.price_per_unit,
                currency=o.currency or "USD",
                is_best_value=bool(o.is_best_value),
            )
        )

    data = DirectResponseProductData(
        page_metadata=PageMetadata(
            canonical_url=canonicalize_url(url),
            original_url=url,
            page_type=PageType.ADVERTORIAL_FUNNEL,
            content_hash="",
            builder_detected=None,
        ),
        product_info=ProductInfo(
            title=extraction.product_title or None,
            brand_name=extraction.brand_name or None,
            key_specs=extraction.key_specs or [],
        ),
        social_proof=SocialProof(
            review_count=extraction.review_count, rating_value=extraction.rating_value
        ),
        offer_matrix=kept_offers,
        guarantee_terms=extraction.guarantee_terms or None,
        extraction_tier=ExtractionTier.TIER_5_LLM,
    )

    logger.info(
        "extract_direct_response_llm_succeeded",
        url=url,
        num_offers=len(data.offer_matrix),
        review_count=data.social_proof.review_count,
    )
    return data


_DECIMAL_COMMA_RE = re.compile(r"(\d),(\d{2})(?!\d)")


def _number_appears_in_text(value: float | int, raw_text: str) -> bool:
    """Checks whether `value` appears verbatim in raw_text, tolerant of
    '.0'-suffixed floats (39.0 -> "39") and thousands separators in the
    source text (1,420 -> 1420).

    Checks two normalizations, not one: blanket comma-stripping alone
    (the original behavior, correct for thousands separators like "1,420")
    previously also destroyed European decimal-comma prices — "39,95"
    became "3995" and a correctly-extracted 39.95 was wrongly rejected as
    a hallucination. A comma followed by exactly two digits with no further
    digit after (and not a 3-digit thousands group) is treated as a decimal
    point in the second normalization instead.
    """
    normalized_variants = (
        raw_text.replace(",", ""),
        _DECIMAL_COMMA_RE.sub(r"\1.\2", raw_text),
    )
    if isinstance(value, float) and value.is_integer():
        candidates = (str(int(value)), f"{value:.2f}", str(value))
    elif isinstance(value, float):
        candidates = (f"{value:.2f}", str(value))
    else:
        candidates = (str(value),)
    return any(c in normalized for normalized in normalized_variants for c in candidates)


def validate_against_raw_text(
    data: DirectResponseProductData, raw_text: str
) -> DirectResponseProductData:
    """Hallucination guardrail: nulls out review_count/rating_value and any
    offer's total_price that doesn't appear as a substring of the page's own
    visible text, rather than trusting the model's numbers outright."""
    updates: dict = {}

    social_proof = data.social_proof
    social_updates: dict = {}
    if social_proof.review_count is not None and not _number_appears_in_text(
        social_proof.review_count, raw_text
    ):
        logger.warning(
            "direct_response_guardrail_rejected",
            url=data.page_metadata.original_url,
            field="review_count",
            reason="not_in_raw_text",
        )
        social_updates["review_count"] = None
    if social_proof.rating_value is not None and not (0 <= social_proof.rating_value <= 5):
        # Range check, independent of the hallucination check below: a real
        # number genuinely printed on the page isn't necessarily a 5-star
        # rating — review/comparison-listicle sites ("Top 5 X Supplements")
        # commonly print an editorial "9.8/10" score, which the LLM has no
        # way to distinguish from a customer star rating unless told to.
        # Confirmed live in production data: 47 ads had rating_value > 5.0,
        # heavily clustered at exactly 9.8 across many distinct "Top 5"
        # review-site domains (top5remedies.com, herwellnessdaily.com,
        # etc.) — corrupting the corpus's rating mean above the scale's own
        # maximum (5.04 average on a 0-5 field). The 9.8 itself is real
        # (would pass the not-hallucinated check below), just semantically
        # the wrong kind of number for this field.
        logger.warning(
            "direct_response_guardrail_rejected",
            url=data.page_metadata.original_url,
            field="rating_value",
            reason="out_of_range",
        )
        social_updates["rating_value"] = None
    elif social_proof.rating_value is not None and not _number_appears_in_text(
        social_proof.rating_value, raw_text
    ):
        logger.warning(
            "direct_response_guardrail_rejected",
            url=data.page_metadata.original_url,
            field="rating_value",
            reason="not_in_raw_text",
        )
        social_updates["rating_value"] = None
    if social_updates:
        updates["social_proof"] = social_proof.model_copy(update=social_updates)

    new_offers: list[OfferTier] = []
    offers_changed = False
    for offer in data.offer_matrix:
        offer_updates: dict = {}
        total_grounded = offer.total_price is not None and _number_appears_in_text(
            offer.total_price, raw_text
        )
        per_unit_grounded = offer.price_per_unit is not None and _number_appears_in_text(
            offer.price_per_unit, raw_text
        )
        # total_price is frequently a legitimate quantity * price_per_unit
        # computation for bundle/tiered offers ("Buy 2 Get 2 Free" pages
        # show only the per-unit discounted price, never the multiplied
        # total) — trust it whenever price_per_unit is independently
        # grounded in the page's own text, rather than treating "not
        # verbatim on the page" as proof of hallucination. Confirmed live
        # (extraction-gap Round 1): this was the largest single cause of
        # guardrail-rejected-but-legitimate bundle prices.
        if offer.total_price is not None and not total_grounded and not per_unit_grounded:
            logger.warning(
                "direct_response_guardrail_rejected",
                url=data.page_metadata.original_url,
                field="offer_matrix.total_price",
                reason="not_in_raw_text",
            )
            offer_updates["total_price"] = None
            offer_updates["price_per_unit"] = None
        if offer_updates:
            offers_changed = True
            new_offers.append(offer.model_copy(update=offer_updates))
        else:
            new_offers.append(offer)
    if offers_changed:
        updates["offer_matrix"] = new_offers

    if not updates:
        return data
    return data.model_copy(update=updates)
