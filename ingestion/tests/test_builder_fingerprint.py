from ingestion.builder_fingerprint import (
    BUILDER_SIGNATURES,
    detect_builder,
    extract_via_builder_fingerprint,
    get_amazon_region,
)
from ingestion.direct_response_schema import ExtractionTier, PageType

_NO_SCHEMA_ORG_NOTE = "no <script type='application/ld+json'> or itemprop markup anywhere"

_PAGEFLY_HTML = """
<html><head><title>Rosabella Beetroot</title></head>
<body class="pf-container">
  <div class="pagefly-container">
    <h1>Rosabella Organic Beetroot</h1>
    <div class="offer-grid">
      <div class="offer-card">1 Bottle $39.95</div>
      <div class="offer-card">3 Bottles $89.85 Best Value</div>
    </div>
    <div class="reviews-block">'Excellent' | 134 reviews | 4.8 stars</div>
  </div>
</body></html>
"""

_ZIPIFY_HTML = """
<html><head><title>Happy Liver Supplement</title></head>
<body>
  <div class="zp-container">
    <h1>Happy Liver 13-in-1</h1>
    <div class="zipify-pages-offer">
      <div class="tier-card">1 Pack $49.00</div>
      <div class="tier-card">3 Packs $99.00 Most Popular</div>
    </div>
    <div class="social">2,340 reviews, 4.6 stars</div>
  </div>
</body></html>
"""

_GEMPAGES_HTML = """
<html><head><title>Wellness Gummies</title></head>
<body>
  <div class="gempages-container">
    <div class="gf_style">
      <h1>Wellness Gummies</h1>
      <div class="pricing-plan">1 Box $24.99</div>
      <div class="pricing-plan">2 Boxes $44.99 Best Seller</div>
      <div>512 reviews</div>
    </div>
  </div>
</body></html>
"""

_RECONVERT_HTML = """
<html><head><title>Sleep Aid Drops</title></head>
<body>
  <div class="reconvert-content">
    <div class="reconvert_funnel">
      <h1>Sleep Aid Drops</h1>
      <div class="bundle-offer">1 Item $19.99</div>
      <div class="bundle-offer">2 Items $34.99</div>
      <div>rating 4.9 stars, 88 reviews</div>
    </div>
  </div>
</body></html>
"""

_NO_BUILDER_HTML = (
    "<html><body><h1>Generic Store Page</h1><p>Nothing special here.</p></body></html>"
)


class TestBuilderSignatures:
    def test_all_four_builders_defined(self) -> None:
        assert set(BUILDER_SIGNATURES) == {"pagefly", "zipify", "gempages", "reconvert"}


class TestDetectBuilder:
    def test_detects_pagefly(self) -> None:
        assert detect_builder(_PAGEFLY_HTML) == "pagefly"

    def test_detects_zipify(self) -> None:
        assert detect_builder(_ZIPIFY_HTML) == "zipify"

    def test_detects_gempages(self) -> None:
        assert detect_builder(_GEMPAGES_HTML) == "gempages"

    def test_detects_reconvert(self) -> None:
        assert detect_builder(_RECONVERT_HTML) == "reconvert"

    def test_no_signature_returns_none(self) -> None:
        assert detect_builder(_NO_BUILDER_HTML) is None

    def test_cdn_host_signature_alone_matches(self) -> None:
        html = '<html><body><script src="https://cdn.pagefly.io/x.js"></script></body></html>'
        assert detect_builder(html) == "pagefly"


class TestExtractViaBuilderFingerprint:
    def test_pagefly_extracts_offer_matrix_and_social_proof(self) -> None:
        result = extract_via_builder_fingerprint(_PAGEFLY_HTML, "https://store.com/p")
        assert result is not None
        assert result.page_metadata.builder_detected == "pagefly"
        assert result.page_metadata.page_type == PageType.ADVERTORIAL_FUNNEL
        assert result.extraction_tier == ExtractionTier.TIER_4_5_BUILDER
        assert len(result.offer_matrix) == 2
        labels = {o.quantity for o in result.offer_matrix}
        assert labels == {1, 3}
        best = next(o for o in result.offer_matrix if o.is_best_value)
        assert best.quantity == 3
        assert result.social_proof.review_count == 134
        assert result.social_proof.rating_value == 4.8

    def test_zipify_extracts_offer_matrix(self) -> None:
        result = extract_via_builder_fingerprint(_ZIPIFY_HTML, "https://store.com/p")
        assert result is not None
        assert result.page_metadata.builder_detected == "zipify"
        prices = {o.total_price for o in result.offer_matrix}
        assert 49.0 in prices and 99.0 in prices
        assert result.social_proof.review_count == 2340

    def test_gempages_extracts_offer_matrix(self) -> None:
        result = extract_via_builder_fingerprint(_GEMPAGES_HTML, "https://store.com/p")
        assert result is not None
        assert result.page_metadata.builder_detected == "gempages"
        assert result.social_proof.review_count == 512

    def test_reconvert_extracts_offer_matrix(self) -> None:
        result = extract_via_builder_fingerprint(_RECONVERT_HTML, "https://store.com/p")
        assert result is not None
        assert result.page_metadata.builder_detected == "reconvert"
        assert result.social_proof.rating_value == 4.9
        assert result.social_proof.review_count == 88

    def test_no_builder_returns_none(self) -> None:
        assert extract_via_builder_fingerprint(_NO_BUILDER_HTML, "https://store.com/p") is None

    def test_malformed_out_of_range_rating_match_rejected(self) -> None:
        """Regression: _RATING_VALUE_RE requires "out of 5"/"stars"/"/5"
        wording but doesn't itself verify the matched number is actually
        <=5 — a stray "9.8 out of 5" (typo or an unrelated nearby number
        the regex snapped onto) would otherwise pass through unguarded,
        same defensive range check as llm_fallback.py's guardrail."""
        html = """
        <html><body class="pf-container"><div class="pagefly-container">
        <h1>Product</h1>
        <div class="offer-grid"><div class="offer-card">1 Bottle $39.95</div></div>
        <p>Rated 9.8 out of 5 by our editorial team</p>
        </div></body></html>
        """
        result = extract_via_builder_fingerprint(html, "https://store.com/p")
        assert result is not None
        assert result.social_proof.rating_value is None

    def test_builder_detected_but_nothing_extractable_returns_none(self) -> None:
        html = '<html><body class="pf-container"><p>Coming soon.</p></body></html>'
        assert extract_via_builder_fingerprint(html, "https://store.com/p") is None

    def test_zero_schema_org_markup_still_extracts(self) -> None:
        assert "application/ld+json" not in _PAGEFLY_HTML
        result = extract_via_builder_fingerprint(_PAGEFLY_HTML, "https://store.com/p")
        assert result is not None


_AMAZON_HTML = """
<html><body>
<span id="productTitle">Immune Support Supplement, Elderberry, 60 Count</span>
<div id="bylineInfo">Visit the Vitamins Wellness Store</div>
<span class="a-icon-alt">4.4 out of 5 stars</span>
<span id="acrCustomerReviewText">(118)</span>
<td>Add to Cart</td>
</body></html>
"""


class TestExtractViaAmazon:
    def test_amazon_url_routes_to_dedicated_extractor_regardless_of_content(self) -> None:
        """Regression: Amazon pages never matched any BUILDER_SIGNATURES
        (they're not a page-builder app), so extract_via_builder_fingerprint
        always returned None for them, leaving Tier 5's zone-pruned LLM as
        the only path — which often landed on Amazon's own checkout/nav
        widget text instead of the real #productTitle. Confirmed live: 8/10
        Amazon URLs in one 61-URL sample hit exactly this."""
        result = extract_via_builder_fingerprint(_AMAZON_HTML, "https://www.amazon.com/dp/B08WKV7LV6")
        assert result is not None
        assert result.page_metadata.builder_detected == "amazon"
        assert result.extraction_tier == ExtractionTier.TIER_4_5_BUILDER
        assert result.product_info.title == "Immune Support Supplement, Elderberry, 60 Count"
        assert result.product_info.brand_name == "Vitamins Wellness"
        assert result.social_proof.rating_value == 4.4
        assert result.social_proof.review_count == 118

    def test_amazon_regional_domain_detected(self) -> None:
        result = extract_via_builder_fingerprint(_AMAZON_HTML, "https://www.amazon.co.uk/dp/B08WKV7LV6")
        assert result is not None
        assert result.page_metadata.builder_detected == "amazon"

    def test_non_amazon_url_does_not_use_amazon_path_even_with_matching_ids(self) -> None:
        """A non-Amazon page happening to reuse Amazon's element ids (unlikely
        but not impossible) should not be routed to the Amazon extractor —
        detection is URL-domain-based, not content-based, precisely to avoid
        this kind of false trigger."""
        result = extract_via_builder_fingerprint(_AMAZON_HTML, "https://store.com/p")
        assert result is None or result.page_metadata.builder_detected != "amazon"

    def test_amazon_page_with_nothing_extractable_returns_none(self) -> None:
        html = "<html><body><td>Add to Cart</td></body></html>"
        assert extract_via_builder_fingerprint(html, "https://www.amazon.com/dp/B000000000") is None

    def test_amazon_price_extracted_from_core_price_container(self) -> None:
        """Regression: the generic `.a-price .a-offscreen` selector alone
        matches every price mention on an Amazon page (strikethrough MSRP,
        per-unit breakdowns, other listings) — confirmed live, one page
        returned 5 matches including a stray "$0.30". Scoping to
        #corePrice_feature_div/#apex_desktop and taking the first match is
        what actually isolates the real buy-box price."""
        html = """
        <html><body>
        <span id="productTitle">Immune Support Supplement</span>
        <div id="corePrice_feature_div">
          <span class="a-price"><span class="a-offscreen">$17.95</span></span>
          <span class="a-price"><span class="a-offscreen">$0.30</span></span>
        </div>
        </body></html>
        """
        result = extract_via_builder_fingerprint(html, "https://www.amazon.com/dp/B08WKV7LV6")
        assert result is not None
        assert len(result.offer_matrix) == 1
        assert result.offer_matrix[0].total_price == 17.95
        assert result.offer_matrix[0].currency == "USD"

    def test_amazon_offer_tier_label_is_empty_not_a_fake_variant(self) -> None:
        """Regression (variants_featured Round 1, issue #35): the buy-box
        OfferTier's tier_label used to be the hardcoded literal "Amazon buy
        box" -- harmless while nothing read tier_label, but once
        _merge_direct_response_into started surfacing every tier_label into
        variants_featured (confirmed live: a corpus reprocessing pass wrote
        "Amazon buy box" into variants_featured on every priced Amazon ad),
        that string is code-internal, never real page content, and must
        never be treated as a product variant. tier_label must stay empty
        so the merge's `if o.tier_label` check naturally excludes it."""
        html = """
        <html><body>
        <span id="productTitle">Immune Support Supplement</span>
        <div id="corePrice_feature_div">
          <span class="a-price"><span class="a-offscreen">$17.95</span></span>
        </div>
        </body></html>
        """
        result = extract_via_builder_fingerprint(html, "https://www.amazon.com/dp/B08WKV7LV6")
        assert result is not None
        assert result.offer_matrix[0].tier_label == ""

    def test_amazon_no_price_container_leaves_offer_matrix_empty(self) -> None:
        result = extract_via_builder_fingerprint(_AMAZON_HTML, "https://www.amazon.com/dp/B08WKV7LV6")
        assert result is not None
        assert result.offer_matrix == []

    def test_amazon_price_uses_marketplace_currency_not_hardcoded_usd(self) -> None:
        """Regression: every ad's link_url observed in this corpus so far
        is amazon.com, but Amazon operates a separate marketplace per
        country with its own currency — a genuine amazon.co.uk URL's price
        should be tagged GBP, not silently mislabeled USD."""
        html = """
        <html><body>
        <span id="productTitle">Immune Support Supplement</span>
        <div id="corePrice_feature_div">
          <span class="a-price"><span class="a-offscreen">£14.99</span></span>
        </div>
        </body></html>
        """
        result = extract_via_builder_fingerprint(html, "https://www.amazon.co.uk/dp/B08WKV7LV6")
        assert result is not None
        assert result.offer_matrix[0].currency == "GBP"


class TestGetAmazonRegion:
    def test_amazon_com_maps_to_us(self) -> None:
        assert get_amazon_region("https://www.amazon.com/dp/B08WKV7LV6") == ("us", "USD")

    def test_amazon_co_uk_maps_to_gb(self) -> None:
        assert get_amazon_region("https://www.amazon.co.uk/dp/B08WKV7LV6") == ("gb", "GBP")

    def test_amazon_de_maps_to_de_eur(self) -> None:
        assert get_amazon_region("https://www.amazon.de/dp/B08WKV7LV6") == ("de", "EUR")

    def test_amazon_in_maps_to_in_inr(self) -> None:
        """The exact marketplace whose price mismatch (INR pricing served
        on an amazon.com fetch, not a genuine amazon.in URL) motivated this
        whole investigation — confirming the map itself is correct even
        though no amazon.in URL has been observed in this corpus."""
        assert get_amazon_region("https://www.amazon.in/dp/B08WKV7LV6") == ("in", "INR")

    def test_non_amazon_url_returns_none(self) -> None:
        assert get_amazon_region("https://store.com/products/x") is None

    def test_unrecognized_amazon_tld_returns_none_not_a_guess(self) -> None:
        """A real but not-yet-mapped Amazon TLD should return None rather
        than silently defaulting to a wrong region — better to skip
        forcing a proxy_country than force the wrong one."""
        assert get_amazon_region("https://www.amazon.co.za/dp/B08WKV7LV6") is None
