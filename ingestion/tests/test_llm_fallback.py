"""Tests for Tier 5: zone-pruned LLM fallback extraction."""

from __future__ import annotations

from unittest.mock import patch

from ingestion.direct_response_schema import (
    DirectResponseProductData,
    ExtractionTier,
    OfferTier,
    PageMetadata,
    PageType,
    ProductInfo,
    SocialProof,
)
from ingestion.llm_fallback import (
    _SYSTEM_PROMPT,
    _DirectResponseLLMExtraction,
    _LLMOfferTier,
    extract_via_llm,
    validate_against_raw_text,
)


def _patch_llm():
    return patch("ingestion.llm_fallback.ReplicateVisionClient.extract_structured_text")


class TestLLMExtractionToleratesNullFields:
    """Regression coverage for a real bug found via live testing: Gemini
    returns explicit JSON `null` for any field it has no data for —
    including array/string fields the JSON template's prose implied were
    required — not just the numeric fields flagged "or null" in the prompt.
    An earlier, stricter schema failed Pydantic validation on the majority
    of real responses, so Tier 5 silently returned None every time."""

    def test_construction_succeeds_with_all_optional_fields_null(self) -> None:
        # Mirrors a real live response verbatim (product_title/brand_name/
        # key_specs/offer_matrix/guarantee_terms all null, only review_count set).
        extraction = _DirectResponseLLMExtraction(
            product_title=None,
            brand_name=None,
            key_specs=None,
            review_count=134,
            rating_value=None,
            offer_matrix=None,
            guarantee_terms=None,
        )
        assert extraction.review_count == 134
        assert extraction.key_specs is None
        assert extraction.offer_matrix is None

    def test_offer_tier_with_null_fields_is_valid(self) -> None:
        offer = _LLMOfferTier(
            tier_label=None, quantity=None, total_price=39.95,
            price_per_unit=None, currency=None, is_best_value=None,
        )
        assert offer.total_price == 39.95

    def test_extract_via_llm_maps_all_null_response_without_crashing(self) -> None:
        mock_extraction = _DirectResponseLLMExtraction(
            product_title=None, brand_name=None, key_specs=None,
            review_count=134, rating_value=None, offer_matrix=None, guarantee_terms=None,
        )
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("some pruned markdown", "https://store.com/p")

        assert result is not None
        assert result.social_proof.review_count == 134
        assert result.product_info.key_specs == []
        assert result.offer_matrix == []
        assert result.guarantee_terms is None

    def test_offer_matrix_entry_with_null_quantity_and_currency_defaults(self) -> None:
        mock_extraction = _DirectResponseLLMExtraction(
            offer_matrix=[
                _LLMOfferTier(
                    tier_label=None, quantity=None, total_price=19.99,
                    price_per_unit=None, currency=None, is_best_value=None,
                )
            ],
        )
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("some pruned markdown", "https://store.com/p")

        assert result is not None
        assert len(result.offer_matrix) == 1
        offer = result.offer_matrix[0]
        assert offer.quantity == 1  # defaulted from null
        assert offer.currency == "USD"  # defaulted from null
        assert offer.total_price == 19.99


class TestExtractViaLlm:
    def test_multi_tier_pricing_parsed_correctly(self) -> None:
        mock_extraction = _DirectResponseLLMExtraction(
            product_title="Rosabella Beetroot",
            offer_matrix=[
                _LLMOfferTier(
                    tier_label="1 Bottle", quantity=1, total_price=39.95,
                    price_per_unit=39.95, currency="USD",
                ),
                _LLMOfferTier(
                    tier_label="3 Bottles", quantity=3, total_price=89.85,
                    price_per_unit=29.95, currency="USD", is_best_value=True,
                ),
            ],
            review_count=134,
            rating_value=4.8,
        )
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("1 Bottle $39.95, 3 Bottles $89.85", "https://store.com/p")

        assert result is not None
        assert result.extraction_tier == ExtractionTier.TIER_5_LLM
        assert result.page_metadata.page_type == PageType.ADVERTORIAL_FUNNEL
        assert len(result.offer_matrix) == 2
        single = next(o for o in result.offer_matrix if o.quantity == 1)
        bundle = next(o for o in result.offer_matrix if o.quantity == 3)
        assert single.total_price == 39.95
        assert bundle.total_price == 89.85
        assert bundle.is_best_value is True
        assert result.social_proof.review_count == 134

    def test_empty_markdown_returns_none_without_calling_llm(self) -> None:
        with _patch_llm() as mock_llm:
            result = extract_via_llm("   ", "https://store.com/p")
            mock_llm.assert_not_called()
        assert result is None

    def test_llm_exception_returns_none(self) -> None:
        with _patch_llm() as mock_llm:
            mock_llm.side_effect = RuntimeError("replicate down")
            result = extract_via_llm("some markdown content", "https://store.com/p")
        assert result is None

    def test_canonical_url_set_from_original_url(self) -> None:
        mock_extraction = _DirectResponseLLMExtraction(product_title="X")
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("content", "https://STORE.com/p/?utm_source=fb")
        assert result is not None
        assert result.page_metadata.canonical_url == "https://store.com/p"
        assert result.page_metadata.original_url == "https://STORE.com/p/?utm_source=fb"


class TestComparisonTablePromptGuidance:
    """Extraction-gap Round 2: comparison-table pages (a 'Top 5 Supplements'
    review ranking several competing products) confirmed live as a
    recurring cause of missed prices — the model needs explicit guidance to
    extract only the featured product's own price, not a compared
    competitor's. No deterministic way to verify LLM behavior in a unit
    test; this documents the prompt actually carries the instruction (see
    also the live smoke test in extraction-gap Round 2's own verification)."""

    def test_prompt_instructs_featured_product_disambiguation(self) -> None:
        assert "comparison" in _SYSTEM_PROMPT.lower()
        assert "featured" in _SYSTEM_PROMPT.lower()
        assert "rhetorical_or_competitor_price" in _SYSTEM_PROMPT


class TestPriceContextFiltering:
    """Phase 0.5h: price_context lets the LLM itself classify every price-
    like mention it considers, so offer_matrix only keeps entries that are
    actually the product's own price — defense-in-depth alongside (not a
    replacement for) zone_pruner.py's regex-based zone selection."""

    def _extraction_with_offer(
        self, price_context: str | None, **overrides
    ) -> _DirectResponseLLMExtraction:
        offer_kwargs = {
            "tier_label": "1 Item", "quantity": 1, "total_price": 39.95,
            "price_per_unit": 39.95, "currency": "USD", "price_context": price_context,
        }
        offer_kwargs.update(overrides)
        return _DirectResponseLLMExtraction(offer_matrix=[_LLMOfferTier(**offer_kwargs)])

    def test_real_offer_is_kept(self) -> None:
        mock_extraction = self._extraction_with_offer("real_offer")
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("Buy now for $39.95", "https://store.com/p")
        assert result is not None
        assert len(result.offer_matrix) == 1
        assert result.offer_matrix[0].total_price == 39.95

    def test_bundle_price_is_kept(self) -> None:
        mock_extraction = self._extraction_with_offer("bundle_price")
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("Buy 2 Get 2 Free", "https://store.com/p")
        assert result is not None
        assert len(result.offer_matrix) == 1

    def test_unclassified_none_is_kept(self) -> None:
        """Backward compatible: a model response that doesn't classify
        (price_context=None, the field's default) is trusted, not discarded
        — filtering only removes explicitly-bad classifications."""
        mock_extraction = self._extraction_with_offer(None)
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("Buy now for $39.95", "https://store.com/p")
        assert result is not None
        assert len(result.offer_matrix) == 1

    def test_shipping_or_promo_banner_is_discarded(self) -> None:
        mock_extraction = self._extraction_with_offer("shipping_or_promo_banner")
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("Free shipping on orders over $50", "https://store.com/p")
        assert result is not None
        assert result.offer_matrix == []

    def test_rhetorical_or_competitor_price_is_discarded(self) -> None:
        mock_extraction = self._extraction_with_offer("rhetorical_or_competitor_price")
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("Spending $1,029 on Ozempic", "https://store.com/p")
        assert result is not None
        assert result.offer_matrix == []

    def test_cart_subtotal_widget_is_discarded(self) -> None:
        mock_extraction = self._extraction_with_offer("cart_subtotal_widget", total_price=0.0)
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("Subtotal $0.00", "https://store.com/p")
        assert result is not None
        assert result.offer_matrix == []

    def test_mixed_offers_only_keeps_real_ones(self) -> None:
        mock_extraction = _DirectResponseLLMExtraction(
            offer_matrix=[
                _LLMOfferTier(
                    tier_label="shipping banner", quantity=1, total_price=50.0,
                    price_context="shipping_or_promo_banner",
                ),
                _LLMOfferTier(
                    tier_label="1 Bottle", quantity=1, total_price=39.95,
                    price_per_unit=39.95, currency="USD", price_context="real_offer",
                ),
                _LLMOfferTier(
                    tier_label="Buy 2 Get 2 Free", quantity=4, total_price=58.92,
                    price_per_unit=14.73, currency="USD", price_context="bundle_price",
                ),
            ]
        )
        with _patch_llm() as mock_llm:
            mock_llm.return_value = mock_extraction
            result = extract_via_llm("mixed content", "https://store.com/p")
        assert result is not None
        assert len(result.offer_matrix) == 2
        assert {o.total_price for o in result.offer_matrix} == {39.95, 58.92}


class TestValidateAgainstRawText:
    def _sample_data(
        self, review_count: int | None, total_price: float | None
    ) -> DirectResponseProductData:
        return DirectResponseProductData(
            page_metadata=PageMetadata(
                canonical_url="https://store.com/p",
                original_url="https://store.com/p",
                page_type=PageType.ADVERTORIAL_FUNNEL,
                content_hash="abc",
            ),
            product_info=ProductInfo(),
            social_proof=SocialProof(review_count=review_count, rating_value=None),
            offer_matrix=[
                OfferTier(
                    tier_label="1 Bottle", quantity=1, total_price=total_price,
                    price_per_unit=total_price, currency="USD",
                )
            ],
            extraction_tier=ExtractionTier.TIER_5_LLM,
        )

    def test_value_present_in_raw_text_is_kept(self) -> None:
        data = self._sample_data(review_count=134, total_price=39.95)
        raw_text = "Excellent, 134 reviews. 1 Bottle just $39.95 today."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.social_proof.review_count == 134
        assert validated.offer_matrix[0].total_price == 39.95

    def test_hallucinated_review_count_is_nulled(self) -> None:
        data = self._sample_data(review_count=99999, total_price=39.95)
        raw_text = "Excellent, 134 reviews. 1 Bottle just $39.95 today."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.social_proof.review_count is None

    def test_hallucinated_price_is_nulled(self) -> None:
        data = self._sample_data(review_count=134, total_price=999.99)
        raw_text = "Excellent, 134 reviews. 1 Bottle just $39.95 today."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.offer_matrix[0].total_price is None
        assert validated.offer_matrix[0].price_per_unit is None

    def test_none_values_pass_through_unchanged(self) -> None:
        data = self._sample_data(review_count=None, total_price=None)
        validated = validate_against_raw_text(data, "irrelevant text")
        assert validated.social_proof.review_count is None
        assert validated.offer_matrix[0].total_price is None

    def test_derived_total_price_trusted_when_price_per_unit_grounded(self) -> None:
        """Regression: bundle/tiered offer pages ("Buy 2 Get 2 Free") almost
        never show the multiplied total price verbatim — only the per-unit
        discounted price. The LLM correctly computes total_price = quantity
        * price_per_unit, but that computed number was previously treated
        as unsupported (not literally on the page) and rejected, wiping out
        price_per_unit too even though *it* was independently grounded.
        Confirmed live: the largest single cause of guardrail-rejected but
        legitimate bundle prices in the extraction-gap Round 1 diagnosis."""
        data = DirectResponseProductData(
            page_metadata=PageMetadata(
                canonical_url="https://store.com/p",
                original_url="https://store.com/p",
                page_type=PageType.ADVERTORIAL_FUNNEL,
                content_hash="abc",
            ),
            product_info=ProductInfo(),
            social_proof=SocialProof(),
            offer_matrix=[
                OfferTier(
                    tier_label="Buy 2 + Get 2 FREE", quantity=4, total_price=58.92,
                    price_per_unit=14.73, currency="USD",
                )
            ],
            extraction_tier=ExtractionTier.TIER_5_LLM,
        )
        raw_text = "Buy 2 + Get 2 FREE — $14.73/ea. Most Popular."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.offer_matrix[0].total_price == 58.92
        assert validated.offer_matrix[0].price_per_unit == 14.73

    def test_total_price_still_rejected_when_neither_total_nor_per_unit_grounded(self) -> None:
        data = DirectResponseProductData(
            page_metadata=PageMetadata(
                canonical_url="https://store.com/p",
                original_url="https://store.com/p",
                page_type=PageType.ADVERTORIAL_FUNNEL,
                content_hash="abc",
            ),
            product_info=ProductInfo(),
            social_proof=SocialProof(),
            offer_matrix=[
                OfferTier(
                    tier_label="Buy 2 + Get 2 FREE", quantity=4, total_price=58.92,
                    price_per_unit=14.73, currency="USD",
                )
            ],
            extraction_tier=ExtractionTier.TIER_5_LLM,
        )
        raw_text = "No prices mentioned anywhere on this page at all."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.offer_matrix[0].total_price is None
        assert validated.offer_matrix[0].price_per_unit is None

    def test_no_rejections_returns_equivalent_data(self) -> None:
        data = self._sample_data(review_count=134, total_price=39.95)
        raw_text = "134 reviews, $39.95"
        validated = validate_against_raw_text(data, raw_text)
        assert validated == data

    def test_editorial_out_of_10_score_rejected_as_rating(self) -> None:
        """Regression: a Phase 1 data-exploration pass found 47 real corpus
        ads with rating_value > 5.0 (mean pulled to 5.04 on a 0-5 field),
        heavily clustered at exactly 9.8 across many distinct "Top 5 X
        Supplements" review/comparison-listicle domains — an editorial
        "Editor's Score: 9.8/10" being extracted into a field meant for a
        customer 5-star rating. The 9.8 is genuinely printed on the page
        (would pass the not-hallucinated check), so this needs its own
        range check, checked before the hallucination check."""
        data = DirectResponseProductData(
            page_metadata=PageMetadata(
                canonical_url="https://store.com/p",
                original_url="https://store.com/p",
                page_type=PageType.ADVERTORIAL_FUNNEL,
                content_hash="abc",
            ),
            product_info=ProductInfo(),
            social_proof=SocialProof(rating_value=9.8),
            offer_matrix=[],
            extraction_tier=ExtractionTier.TIER_5_LLM,
        )
        raw_text = "Editor's Score: 9.8/10 — our top pick this year."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.social_proof.rating_value is None

    def test_valid_star_rating_still_accepted(self) -> None:
        data = DirectResponseProductData(
            page_metadata=PageMetadata(
                canonical_url="https://store.com/p",
                original_url="https://store.com/p",
                page_type=PageType.ADVERTORIAL_FUNNEL,
                content_hash="abc",
            ),
            product_info=ProductInfo(),
            social_proof=SocialProof(rating_value=4.8),
            offer_matrix=[],
            extraction_tier=ExtractionTier.TIER_5_LLM,
        )
        raw_text = "4.8 out of 5 stars, 134 reviews."
        validated = validate_against_raw_text(data, raw_text)
        assert validated.social_proof.rating_value == 4.8

    def test_european_decimal_comma_price_is_not_wrongly_nulled(self) -> None:
        """Regression: blanket comma-stripping (raw_text.replace(",", ""))
        previously treated a European decimal-comma price ("39,95") as a
        thousands separator, turning it into "3995" — a correctly-extracted
        LLM price of 39.95 then failed the substring check and was wrongly
        rejected as a hallucination."""
        data = self._sample_data(review_count=None, total_price=39.95)
        raw_text = "Prijs: 39,95 EUR vandaag besteld"
        validated = validate_against_raw_text(data, raw_text)
        assert validated.offer_matrix[0].total_price == 39.95

    def test_thousands_separator_still_handled_alongside_decimal_comma(self) -> None:
        """The original thousands-separator normalization (1,420 -> 1420)
        must keep working alongside the new decimal-comma normalization."""
        data = self._sample_data(review_count=1420, total_price=None)
        raw_text = "Loved by 1,420 happy customers"
        validated = validate_against_raw_text(data, raw_text)
        assert validated.social_proof.review_count == 1420
