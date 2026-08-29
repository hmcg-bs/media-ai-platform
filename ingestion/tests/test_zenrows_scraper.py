"""Tests for ingestion/zenrows_scraper.py.

No real ZenRowsClient/network calls anywhere here — a fake async client with
a `.get_async(url, params=...)` method is injected everywhere a client is
needed, matching the dependency-injection pattern already established for
httpx.Client elsewhere in this project. See TestFetchProductZenrows /
TestBatchScrapeZenrows.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ingestion.product_page import ProductPage
from ingestion.zenrows_scraper import (
    ZenRowsProductData,
    batch_scrape_zenrows,
    clean_description,
    clean_price,
    clean_review_count,
    extract_product_data,
    fetch_product_zenrows,
    results_to_dataframe,
    run_zenrows_batch_sync,
    summarize,
    to_product_page_updates,
)

_LLM_PATCH_TARGET = "ingestion.llm_fallback.ReplicateVisionClient.extract_structured_text"

_JSON_LD_HTML = """
<head>
<script type="application/ld+json">
{"@type": "Product", "description": "Great <b>supplement</b>", "offers": {"price": "39.99"},
 "aggregateRating": {"ratingValue": "4.7", "reviewCount": "1,420"}}
</script>
</head>
"""


class TestCleanPrice:
    def test_strips_dollar_sign(self) -> None:
        assert clean_price("$49.99") == 49.99

    def test_strips_commas_and_currency_word(self) -> None:
        assert clean_price("1,234.56 USD") == 1234.56

    def test_strips_euro_sign(self) -> None:
        assert clean_price("€29.99") == 29.99

    def test_numeric_passthrough(self) -> None:
        assert clean_price(19.99) == 19.99
        assert clean_price(20) == 20.0

    def test_none_returns_none(self) -> None:
        assert clean_price(None) is None

    def test_garbage_returns_none(self) -> None:
        assert clean_price("call for price") is None

    def test_empty_string_returns_none(self) -> None:
        assert clean_price("") is None

    def test_unexpected_dict_returns_none_without_crashing(self) -> None:
        """Regression: a real page during the full corpus run had a
        non-standard schema.org offers.price as a nested dict instead of a
        plain string/number, crashing with AttributeError before this guard."""
        assert clean_price({"@type": "foo"}) is None

    def test_unexpected_list_returns_none_without_crashing(self) -> None:
        assert clean_price(["a", "list"]) is None

    def test_before_after_price_takes_last_number(self) -> None:
        """Regression: a before/after-price DOM element like
        <s>$59.99</s><span>$39.99</span>, once separated with a space by
        _first_attr_or_text's get_text(separator=" "), reads "$59.99 $39.99"
        — previously unparseable (whitespace stripped, digits ran together
        into "59.9939.99"), silently dropping a real price. Now resolves to
        the last number, matching the common strikethrough-then-sale-price
        DOM order."""
        assert clean_price("$59.99 $39.99") == 39.99

    def test_single_price_with_space_separated_symbol_still_works(self) -> None:
        assert clean_price("$ 49.99") == 49.99


class TestCleanReviewCount:
    def test_parses_comma_separated_with_label(self) -> None:
        assert clean_review_count("1,420 Reviews") == 1420

    def test_unexpected_dict_returns_none_without_crashing(self) -> None:
        assert clean_review_count({"nested": "dict"}) is None

    def test_int_passthrough(self) -> None:
        assert clean_review_count(42) == 42

    def test_none_returns_none(self) -> None:
        assert clean_review_count(None) is None

    def test_no_digits_returns_none(self) -> None:
        assert clean_review_count("no reviews yet") is None


class TestCleanDescription:
    def test_strips_tags(self) -> None:
        assert clean_description("<p>Hello <b>world</b></p>") == "Hello world"

    def test_none_returns_empty_string(self) -> None:
        assert clean_description(None) == ""

    def test_empty_returns_empty_string(self) -> None:
        assert clean_description("") == ""


class TestExtractFromJsonLd:
    def test_extracts_price_description_rating(self) -> None:
        data = extract_product_data(_JSON_LD_HTML)
        assert data.product_price == 39.99
        assert "Great supplement" in data.product_description
        assert data.rating == 4.7
        assert data.rating_count == 1420

    def test_no_json_ld_returns_empty_data(self) -> None:
        data = extract_product_data("<html><body>nothing here</body></html>")
        assert data.product_price is None
        assert data.product_description == ""
        assert data.rating is None

    def test_malformed_json_ld_skipped_gracefully(self) -> None:
        html = '<script type="application/ld+json">{not valid json}</script>'
        data = extract_product_data(html)
        assert data.product_price is None

    def test_non_product_type_ignored(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type": "Organization", "name": "Some Corp"}
        </script>
        """
        data = extract_product_data(html)
        assert data.product_price is None

    def test_extracts_product_name_and_string_brand(self) -> None:
        """Regression: product_name/brand_name used to be extracted nowhere
        in Tiers 1-4 — only ever set by Tier 4.5/5, which don't run once
        Tiers 1-4 already found price/description/rating. A page's own
        JSON-LD Product.name/Product.brand are among the most universal
        fields on a Product block and were being silently ignored."""
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "name": "Magnesium Complete", "brand": "Gold Seal Supplements",
         "offers": {"price": "39.99"}}
        </script>
        """
        data = extract_product_data(html)
        assert data.product_name == "Magnesium Complete"
        assert data.brand_name == "Gold Seal Supplements"

    def test_extracts_brand_as_nested_object(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "brand": {"@type": "Brand", "name": "Nested Brand Co"}}
        </script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "Nested Brand Co"

    def test_organization_name_used_as_brand_fallback(self) -> None:
        """Regression: mengotomars.com had no Product.brand at all, only a
        site-wide Organization block with name="Mars Men" — previously
        ignored entirely (see test_non_product_type_ignored)."""
        html = """
        <script type="application/ld+json">
        {"@type": "Organization", "name": "Mars Men"}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "description": "Testosterone support"}
        </script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "Mars Men"

    def test_product_brand_wins_over_organization_name(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type": "Organization", "name": "Site Owner Inc"}
        </script>
        <script type="application/ld+json">
        {"@type": "Product", "brand": "Actual Product Brand"}
        </script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "Actual Product Brand"

    def test_product_name_recovered_from_title_tag(self) -> None:
        """Regression: alevia.com/products/amla-192011 had a correct h1/title
        ("Amla Superfruit Capsules – Alevia") but no JSON-LD Product.name at
        all, and Tier 5's LLM wrongly guessed the brand name ("Alevia") as
        the product name from its zone-pruned view. The <title> tag itself,
        read deterministically, has the real answer."""
        html = "<head><title>Amla Superfruit Capsules – Alevia</title></head>"
        data = extract_product_data(html)
        assert data.product_name == "Amla Superfruit Capsules"

    def test_product_name_from_title_strips_inner_brand_segment(self) -> None:
        """bartonsupplements.com's own SEO title nests a second brand
        mention ("ProductName | InnerBrand") inside the outer Shopify
        "{page_title} – {shop_name}" suffix."""
        html = (
            "<head><title>Berberine 800mg with Milk Thistle | Barton Nutrition"
            " – Barton Supplements</title></head>"
        )
        data = extract_product_data(html)
        assert data.product_name == "Berberine 800mg with Milk Thistle"

    def test_product_name_from_title_pipe_only(self) -> None:
        html = "<head><title>Liposomal Collagen Peptides | Rho Nutrition</title></head>"
        data = extract_product_data(html)
        assert data.product_name == "Liposomal Collagen Peptides"

    def test_long_advertorial_title_not_used_as_product_name(self) -> None:
        """Regression: jevawell.com's title is a CMS page-type/timestamp
        label, not a product name — "Advertorial - Personal Story -
        Comparison - Apr 1, 16:47:22 – Jevawell". Plain hyphens are
        deliberately not treated as separators (else this would wrongly
        split at "Advertorial"), and the resulting 61-char candidate is
        rejected by the length bound regardless."""
        html = (
            "<head><title>Advertorial - Personal Story - Comparison - Apr 1, 16:47:22"
            " – Jevawell</title></head>"
        )
        data = extract_product_data(html)
        assert data.product_name is None

    def test_title_with_no_separator_not_used_as_product_name(self) -> None:
        """goldsealsupplements.com's title is a roundup-listicle headline
        with no brand suffix at all ("Top 5 Magnesium Supplements") — not a
        real product name, and correctly has nothing to split on."""
        html = "<head><title>Top 5 Magnesium Supplements</title></head>"
        data = extract_product_data(html)
        assert data.product_name is None

    def test_json_ld_product_name_wins_over_title_tag(self) -> None:
        html = """
        <head><title>Some Different Title – Some Brand</title></head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Authoritative JSON-LD Name"}
        </script>
        """
        data = extract_product_data(html)
        assert data.product_name == "Authoritative JSON-LD Name"

    def test_og_site_name_used_as_brand_fallback_when_no_json_ld_brand(self) -> None:
        """Regression: goldsealsupplements.com's page had no Product.brand
        and no Organization block, but a reliable
        <meta property="og:site_name"> — near-universal on Shopify themes
        and previously never read anywhere in this cascade."""
        html = """
        <head>
        <meta property="og:site_name" content="Gold Seal Supplements">
        </head>
        <script type="application/ld+json">
        {"@type": "Product", "description": "Top 5 magnesium supplements"}
        </script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "Gold Seal Supplements"


class TestExtractFromWindowObjects:
    def test_shopify_analytics_product_variants(self) -> None:
        html = """
        <script>
        window.ShopifyAnalytics.meta.product = {"variants": [{"title": "Small"}, {"title": "Large"}], "price": 500};
        </script>
        """
        data = extract_product_data(html)
        assert data.variants == ["Small", "Large"]
        # 500 > 1000 heuristic doesn't trigger (500 stays as-is, not treated as cents)
        assert data.product_price == 500

    def test_price_in_cents_heuristic(self) -> None:
        html = """
        <script>
        window.ShopifyAnalytics.meta.product = {"price": 3999};
        </script>
        """
        data = extract_product_data(html)
        assert data.product_price == 39.99

    def test_initial_state_pattern(self) -> None:
        html = """
        <script>
        window.__INITIAL_STATE__ = {"variants": [{"name": "Red"}, {"name": "Blue"}]};
        </script>
        """
        data = extract_product_data(html)
        assert data.variants == ["Red", "Blue"]

    def test_malformed_window_object_skipped(self) -> None:
        html = "<script>window.ShopifyAnalytics.meta.product = {not json};</script>"
        data = extract_product_data(html)
        assert data.product_price is None

    def test_shopify_pixel_shop_name_used_as_brand(self) -> None:
        """Regression: getionix.com has no JSON-LD, no og:site_name, but
        Shopify's Pixel-loader initData carries the shop's own name in raw
        inline-script JS, present site-wide independent of page content."""
        html = """
        <script>
        var config = {"apiClientId":"shopify-pixel","isMerchantRequest": false,
        "initData": {"shop":{"name":"IONIX LABS","paymentSettings":{"currencyCode":"USD"}}}};
        </script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "IONIX LABS"

    def test_shopify_merchant_name_used_as_brand_fallback(self) -> None:
        html = """
        <script>
        var applePay = {"merchantCapabilities":["supports3DS"],"merchantName":"Gold Seal Supplements"};
        </script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "Gold Seal Supplements"

    def test_json_ld_brand_wins_over_shop_name(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "brand": "Specific Product Brand"}
        </script>
        <script>var c = {"shop":{"name":"Site-Wide Shop Name"}};</script>
        """
        data = extract_product_data(html)
        assert data.brand_name == "Specific Product Brand"


class TestExtractFromDomReviewWidgets:
    def test_yotpo_widget(self) -> None:
        html = """
        <div class="yotpo-bottomline" data-yotpo-average-rating="4.8" data-yotpo-total-reviews="1500"></div>
        """
        data = extract_product_data(html)
        assert data.rating == 4.8
        assert data.rating_count == 1500

    def test_loox_widget(self) -> None:
        html = """
        <loox-rating data-rating="4.6" data-raters="320"></loox-rating>
        """
        data = extract_product_data(html)
        assert data.rating == 4.6
        assert data.rating_count == 320

    def test_judgeme_widget(self) -> None:
        html = """
        <div class="jdgm-prev-badge" data-average-rating="4.9" data-number-of-reviews="88"></div>
        """
        data = extract_product_data(html)
        assert data.rating == 4.9
        assert data.rating_count == 88

    def test_generic_itemprop_selectors(self) -> None:
        html = """
        <span itemprop="price" content="24.99"></span>
        <span itemprop="ratingValue">4.2</span>
        <span itemprop="reviewCount">55</span>
        """
        data = extract_product_data(html)
        assert data.product_price == 24.99
        assert data.rating == 4.2
        assert data.rating_count == 55


class TestCascadePriority:
    def test_json_ld_wins_over_dom_widget(self) -> None:
        """Earlier tiers (JSON-LD) should not be overwritten by later tiers (DOM)."""
        html = _JSON_LD_HTML + """
        <div class="yotpo-bottomline" data-yotpo-average-rating="1.0" data-yotpo-total-reviews="1"></div>
        """
        data = extract_product_data(html)
        assert data.rating == 4.7  # from JSON-LD, not the Yotpo 1.0
        assert data.rating_count == 1420

    def test_dom_fills_gap_json_ld_left_empty(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "description": "desc only"}
        </script>
        <div class="yotpo-bottomline" data-yotpo-average-rating="4.5" data-yotpo-total-reviews="200"></div>
        """
        data = extract_product_data(html)
        assert data.product_description == "desc only"
        assert data.rating == 4.5  # filled by DOM tier since JSON-LD had none
        assert data.rating_count == 200

    def test_no_html_no_xhr_returns_empty_data(self) -> None:
        data = extract_product_data(None)
        assert data.product_price is None
        assert data.variants == []


class TestExtractFromXhr:
    def test_shopify_products_json_shape(self) -> None:
        xhr = [
            {
                "body": {
                    "product": {
                        "variants": [{"title": "S", "price": "19.99"}, {"title": "L", "price": "24.99"}],
                        "body_html": "<p>XHR description</p>",
                    }
                }
            }
        ]
        data = extract_product_data(None, xhr_json=xhr)
        assert data.variants == ["S", "L"]
        assert data.product_price == 19.99  # min of variant prices
        assert data.product_description == "XHR description"

    def test_review_widget_shape(self) -> None:
        xhr = [{"body": {"average_rating": "4.6", "total_reviews": "88"}}]
        data = extract_product_data(None, xhr_json=xhr)
        assert data.rating == 4.6
        assert data.rating_count == 88

    def test_unknown_shape_degrades_gracefully(self) -> None:
        xhr = [{"unexpected": "shape"}, "not even a dict", 42]
        data = extract_product_data(None, xhr_json=xhr)
        assert data.product_price is None
        assert data.variants == []

    def test_none_xhr_returns_empty(self) -> None:
        data = extract_product_data(None, xhr_json=None)
        assert data.product_price is None


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _FakeZenRowsClient:
    """Injected in place of a real ZenRowsClient — never constructed for real in tests."""

    def __init__(self, responses: dict[str, Any] | None = None, exceptions: dict[str, Exception] | None = None):
        self.responses = responses or {}
        self.exceptions = exceptions or {}
        self.calls: list[tuple[str, dict]] = []

    async def get_async(self, url: str, params: dict | None = None) -> _FakeResponse:
        self.calls.append((url, params or {}))
        if url in self.exceptions:
            raise self.exceptions[url]
        return self.responses.get(url, _FakeResponse(200, "<html></html>"))


class TestFetchProductZenrows:
    def test_success_parses_html(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(responses={"https://store.com/p": _FakeResponse(200, _JSON_LD_HTML)})
        result = asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        assert result.success is True
        assert result.status_code == 200
        assert result.data.product_price == 39.99

    def test_bad_status_code_marked_failed(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(responses={"https://store.com/p": _FakeResponse(403, "blocked")})
        result = asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        assert result.success is False
        assert result.status_code == 403

    def test_exception_caught_gracefully(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(exceptions={"https://store.com/p": ConnectionError("boom")})
        result = asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        assert result.success is False
        assert "boom" in result.error

    def test_no_extractable_data_marked_unsuccessful(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(responses={"https://store.com/p": _FakeResponse(200, "<html>empty</html>")})
        result = asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        assert result.success is False
        assert result.status_code == 200

    def test_js_render_params_passed_through(self) -> None:
        import asyncio

        client = _FakeZenRowsClient()
        asyncio.run(fetch_product_zenrows(client, "https://store.com/p", js_render=True, wait_ms=3000))
        url, params = client.calls[0]
        assert params["js_render"] == "true"
        assert params["wait"] == "3000"

    def test_amazon_com_url_forces_us_proxy_country(self) -> None:
        """Regression: Amazon serves region-specific pricing/currency based
        on the requesting IP's geolocation, and no fetch anywhere in this
        pipeline set premium_proxy/proxy_country — confirmed live, the same
        amazon.com URL returned INR pricing on one fetch and USD on
        another. Pinning the proxy to the URL's own marketplace region
        (get_amazon_region) fixes this — amazon.com maps to "us"."""
        import asyncio

        client = _FakeZenRowsClient()
        asyncio.run(fetch_product_zenrows(client, "https://www.amazon.com/dp/B0G3CFKWNQ"))
        url, params = client.calls[0]
        assert params["premium_proxy"] == "true"
        assert params["proxy_country"] == "us"

    def test_amazon_co_uk_url_forces_gb_proxy_country_not_us(self) -> None:
        """A genuine amazon.co.uk URL should route to a GB proxy, not be
        blindly forced to US — forcing the wrong region would itself be a
        bug, not just imprecise, and could cause Amazon to serve
        mismatched or blocked content."""
        import asyncio

        client = _FakeZenRowsClient()
        asyncio.run(fetch_product_zenrows(client, "https://www.amazon.co.uk/dp/B0G3CFKWNQ"))
        url, params = client.calls[0]
        assert params["proxy_country"] == "gb"

    def test_unrecognized_amazon_tld_does_not_force_any_proxy_country(self) -> None:
        import asyncio

        client = _FakeZenRowsClient()
        asyncio.run(fetch_product_zenrows(client, "https://www.amazon.co.za/dp/B0G3CFKWNQ"))
        url, params = client.calls[0]
        assert "proxy_country" not in params

    def test_non_amazon_url_does_not_set_proxy_country(self) -> None:
        import asyncio

        client = _FakeZenRowsClient()
        asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        url, params = client.calls[0]
        assert "proxy_country" not in params
        assert "premium_proxy" not in params

    def test_cloaked_short_link_routes_to_amazon_extractor_via_final_url_header(self) -> None:
        """Regression: affiliate-cloaking short-links (amzn.to, ampd.to)
        pass `url="https://amzn.to/..."` to fetch_product_zenrows, but
        ZenRows already followed the redirect chain -- confirmed live, the
        real destination is only visible via the Zr-Final-Url response
        header. Before this fix, extract_product_data was called with the
        original short-link `url`, so is_amazon_url("amzn.to/...") was
        False and the dedicated Amazon extractor (Tier 4.5) never fired
        even though the fetched HTML was 100% Amazon's own markup."""
        import asyncio

        amazon_html = """
        <html><body>
        <span id="productTitle">Immune Support Supplement</span>
        <div id="corePrice_feature_div">
          <span class="a-price"><span class="a-offscreen">$17.95</span></span>
        </div>
        </body></html>
        """
        client = _FakeZenRowsClient(
            responses={
                "https://amzn.to/4bn6jrZ": _FakeResponse(
                    200,
                    amazon_html,
                    headers={"Zr-Final-Url": "https://www.amazon.com/dp/B08WKV7LV6"},
                )
            }
        )
        result = asyncio.run(fetch_product_zenrows(client, "https://amzn.to/4bn6jrZ"))

        assert result.final_url == "https://www.amazon.com/dp/B08WKV7LV6"
        assert result.data.product_price == 17.95
        assert result.url == "https://amzn.to/4bn6jrZ"  # identity/dedup key stays the original

    def test_missing_final_url_header_falls_back_to_original_url(self) -> None:
        """A direct (non-redirected) fetch, or a test fake with no headers
        attribute at all, must not crash -- falls back to the original url
        for extraction routing, same as before this fix."""
        import asyncio

        client = _FakeZenRowsClient(
            responses={"https://store.com/p": _FakeResponse(200, _JSON_LD_HTML)}
        )
        result = asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        assert result.final_url == "https://store.com/p"
        assert result.data.product_price == 39.99


class _FakeSequencedZenRowsClient:
    """Returns a different canned response on each successive call to the
    same URL — needed to test the wait_for retry, which calls get_async
    twice for the same URL with different params."""

    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def get_async(self, url: str, params: dict | None = None) -> _FakeResponse:
        self.calls.append((url, params or {}))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestReviewWidgetWaitForRetry:
    """enable_review_widget_retry is opt-in (default False, no behavior
    change for existing callers) — see the module-level comment above
    fetch_product_zenrows for why this can't be applied unconditionally:
    ZenRows' wait_for fails the whole request with a 422 if the selector
    never matches, confirmed via ZenRows' own docs."""

    _NO_RATING_BUT_YOTPO_PRESENT = (
        "<html><body><script src='https://staticw2.yotpo.com/widget.js'></script>"
        "<div class='yotpo-bottomline'></div></body></html>"
    )
    _RATING_NOW_RENDERED = (
        "<html><body><div class='yotpo-bottomline' "
        "data-yotpo-average-rating='4.8' data-yotpo-total-reviews='250'></div></body></html>"
    )

    def test_retry_not_attempted_when_disabled(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(
            responses={"https://store.com/p": _FakeResponse(200, self._NO_RATING_BUT_YOTPO_PRESENT)}
        )
        result = asyncio.run(fetch_product_zenrows(client, "https://store.com/p"))
        assert result.data.rating is None
        assert len(client.calls) == 1  # no second (retry) call made

    def test_retry_not_attempted_when_no_widget_signature(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(
            responses={"https://store.com/p": _FakeResponse(200, "<html><body>Nothing here</body></html>")}
        )
        asyncio.run(
            fetch_product_zenrows(client, "https://store.com/p", enable_review_widget_retry=True)
        )
        assert len(client.calls) == 1  # no signature -> no point retrying, avoids the 422 risk

    def test_retry_recovers_rating_when_widget_resolves_second_time(self) -> None:
        import asyncio

        client = _FakeSequencedZenRowsClient(
            [
                _FakeResponse(200, self._NO_RATING_BUT_YOTPO_PRESENT),
                _FakeResponse(200, self._RATING_NOW_RENDERED),
            ]
        )
        result = asyncio.run(
            fetch_product_zenrows(client, "https://store.com/p", enable_review_widget_retry=True)
        )
        assert result.data.rating == 4.8
        assert result.data.rating_count == 250
        assert len(client.calls) == 2
        assert "wait_for" in client.calls[1][1]

    def test_retry_422_falls_back_to_original_result_without_failing(self) -> None:
        import asyncio

        client = _FakeSequencedZenRowsClient(
            [
                _FakeResponse(200, self._NO_RATING_BUT_YOTPO_PRESENT),
                _FakeResponse(422, "selector not found"),
            ]
        )
        result = asyncio.run(
            fetch_product_zenrows(client, "https://store.com/p", enable_review_widget_retry=True)
        )
        # The original (first-fetch) result is preserved, not marked failed,
        # even though the retry attempt itself hit a 422.
        assert result.status_code == 200
        assert result.data.rating is None

    def test_retry_exception_falls_back_to_original_result(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(
            responses={"https://store.com/p": _FakeResponse(200, self._NO_RATING_BUT_YOTPO_PRESENT)}
        )
        client.get_async_call_count = 0
        real_get_async = client.get_async

        async def flaky_get_async(url: str, params: dict | None = None) -> _FakeResponse:
            client.get_async_call_count += 1
            if client.get_async_call_count == 2:
                raise ConnectionError("retry boom")
            return await real_get_async(url, params)

        client.get_async = flaky_get_async  # type: ignore[method-assign]

        result = asyncio.run(
            fetch_product_zenrows(client, "https://store.com/p", enable_review_widget_retry=True)
        )
        assert result.status_code == 200
        assert result.data.rating is None


class TestBatchScrapeZenrows:
    def test_processes_all_urls_concurrently(self) -> None:
        import asyncio

        client = _FakeZenRowsClient(
            responses={
                "https://a.com": _FakeResponse(200, _JSON_LD_HTML),
                "https://b.com": _FakeResponse(404, ""),
            }
        )
        results = asyncio.run(batch_scrape_zenrows(["https://a.com", "https://b.com"], client=client))
        assert results["https://a.com"].success is True
        assert results["https://b.com"].success is False
        assert len(client.calls) == 2

    def test_run_zenrows_batch_sync_wrapper(self) -> None:
        client = _FakeZenRowsClient(responses={"https://a.com": _FakeResponse(200, _JSON_LD_HTML)})
        results = run_zenrows_batch_sync(["https://a.com"], client=client)
        assert results["https://a.com"].success is True


class TestToProductPageUpdates:
    def test_creates_new_page_when_none_exists(self) -> None:
        data = ZenRowsProductData(product_price=19.99, product_description="desc", rating=4.5, rating_count=100)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.price == 19.99
        assert page.marketing_copy == "desc"
        assert page.rating == 4.5
        assert page.extraction_method == "zenrows"

    def test_merges_preserving_existing_category(self) -> None:
        existing = ProductPage(
            product_name="Test Product",
            product_category="Supplements",
            usp="Vegan, lab-tested",
            price=None,
            extraction_method="shopify_json",
        )
        data = ZenRowsProductData(product_price=29.99, rating=4.8, rating_count=200)
        page = to_product_page_updates(data, existing, "https://store.com/p")
        assert page.product_category == "Supplements"  # preserved
        assert page.usp == "Vegan, lab-tested"  # preserved
        assert page.price == 29.99  # added
        assert page.rating == 4.8  # added
        assert page.extraction_method == "shopify_json+zenrows"

    def test_explicit_zero_rating_count_does_not_overwrite_real_value(self) -> None:
        """Regression: a corpus reprocessing run wiped a real rating_count=171
        to 0 on eternapure.com when a fresh Tier 5 LLM call found no review
        count in its own (differently-pruned) view of the page — the old
        `is not None` check treated the LLM's explicit 0 as a confirmed
        value rather than "found nothing", silently discarding real prior
        data. price/rating share the same fix."""
        existing = ProductPage(rating_count=171, rating=5.0, price=48.75, extraction_method="tier_5_llm")
        data = ZenRowsProductData(rating_count=0, rating=0.0, product_price=0.0)
        page = to_product_page_updates(data, existing, "https://store.com/p")
        assert page.rating_count == 171
        assert page.rating == 5.0
        assert page.price == 48.75
        # no fields actually changed, so no new tier suffix should be appended
        assert page.extraction_method == "tier_5_llm"

    def test_real_nonzero_rating_count_still_updates(self) -> None:
        """Freshness is preserved for genuine values — only an explicit 0 is
        treated as "no signal", not every later-tier value in general."""
        existing = ProductPage(rating_count=171, extraction_method="tier_5_llm")
        data = ZenRowsProductData(rating_count=200)
        page = to_product_page_updates(data, existing, "https://store.com/p")
        assert page.rating_count == 200

    def test_implausibly_large_price_rejected(self) -> None:
        """Regression: a Phase 1 data-exploration pass found real corpus
        outliers up to $235,235,112.00 (mean pulled to $123,590 against a
        true median of $44.99) — traced to _extract_from_window_objects'
        own "price is commonly in cents" heuristic having no upper bound
        on its result. This is the universal bottleneck every ZenRows-
        cascade tier funnels price through, so the guard lives here rather
        than in each individual tier."""
        data = ZenRowsProductData(product_price=499949.99)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.price is None

    def test_plausible_bundle_price_still_accepted(self) -> None:
        data = ZenRowsProductData(product_price=89.85)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.price == 89.85

    def test_implausibly_small_price_rejected(self) -> None:
        """Regression: the same exploration pass found real corpus prices
        as low as $0.20-$0.67 — an indirect "$0.67/day" subscription rate,
        or a stray Amazon subscribe-and-save fragment, stored as if it
        were the product's full price. Nothing in this corpus's confirmed
        prices goes below $11.99 (p10)."""
        data = ZenRowsProductData(product_price=0.67)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.price is None

    def test_implausible_price_does_not_overwrite_existing_valid_price(self) -> None:
        existing = ProductPage(price=44.99, extraction_method="shopify_json")
        data = ZenRowsProductData(product_price=235235112.00)
        page = to_product_page_updates(data, existing, "https://store.com/p")
        assert page.price == 44.99

    def test_out_of_range_rating_rejected(self) -> None:
        """Regression: the same exploration pass found rating values up to
        89.0 (mean 5.04 on a 0-5 field) — a final backstop here in addition
        to llm_fallback.py's guardrail, since JSON-LD/window-objects tiers
        also route ratingValue through clean_price with no range check."""
        data = ZenRowsProductData(rating=9.8)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.rating is None

    def test_valid_rating_still_accepted(self) -> None:
        data = ZenRowsProductData(rating=4.8)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.rating == 4.8

    def test_marketplace_region_and_currency_propagate_to_product_page(self) -> None:
        """price_currency defaults to "USD" on ProductPage (a Pydantic
        placeholder, never a real detected value from any tier in this
        pipeline) — a genuine detected currency should overwrite it
        outright, not be blocked by a setdefault check against that
        default."""
        data = ZenRowsProductData(
            product_price=14.99, marketplace_region="gb", product_currency="GBP"
        )
        page = to_product_page_updates(data, None, "https://www.amazon.co.uk/dp/B08WKV7LV6")
        assert page.marketplace_region == "gb"
        assert page.price_currency == "GBP"

    def test_no_region_leaves_product_page_currency_at_default(self) -> None:
        data = ZenRowsProductData(product_price=19.99)
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.marketplace_region == ""
        assert page.price_currency == "USD"

    def test_no_new_data_returns_existing_unchanged(self) -> None:
        existing = ProductPage(product_name="Test", extraction_method="shopify_json")
        data = ZenRowsProductData()  # all empty
        page = to_product_page_updates(data, existing, "https://store.com/p")
        assert page.extraction_method == "shopify_json"  # unchanged, no +zenrows suffix
        assert page.price is None

    def test_variants_set_shows_all_variants(self) -> None:
        data = ZenRowsProductData(variants=["S", "M", "L"])
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.variants_featured == ["S", "M", "L"]
        assert page.shows_all_variants is True

    def test_single_variant_does_not_set_shows_all(self) -> None:
        data = ZenRowsProductData(variants=["Default"])
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.shows_all_variants is False

    def test_product_name_and_brand_name_fill_when_missing(self) -> None:
        # Regression: a 50-URL live batch run found product_name was silently
        # discarded for every Tier 4.5/5 result (0/780 newly-covered ads had
        # one) — _merge_direct_response_into never mapped it onto the flat
        # ZenRowsProductData shape. Fixed by adding product_name/brand_name
        # as Tier-4.5/5-only fields threaded through both functions.
        data = ZenRowsProductData(product_name="VisioVance 15-in-1", brand_name="VisioVance")
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.product_name == "VisioVance 15-in-1"
        assert page.brand_name == "VisioVance"

    def test_product_name_does_not_overwrite_existing(self) -> None:
        existing = ProductPage(product_name="Established Name", brand_name="Established Brand")
        data = ZenRowsProductData(product_name="New Name", brand_name="New Brand")
        page = to_product_page_updates(data, existing, "https://store.com/p")
        assert page.product_name == "Established Name"
        assert page.brand_name == "Established Brand"

    def test_subscription_status_and_price_fill_when_missing(self) -> None:
        data = ZenRowsProductData(
            subscription_status="subscription_optional", subscription_price=49.99
        )
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.subscription_status == "subscription_optional"
        assert page.subscription_price == 49.99

    def test_subscription_status_does_not_overwrite_known_value(self) -> None:
        existing = ProductPage(
            subscription_status="one_time_only", subscription_price=None
        )
        data = ZenRowsProductData(
            subscription_status="subscription_required", subscription_price=29.99
        )
        page = to_product_page_updates(data, existing, "https://store.com/p")
        # base already had a real (non-"unknown") status -- never overwritten
        assert page.subscription_status == "one_time_only"
        # but subscription_price was still None on base, so that fills in
        assert page.subscription_price == 29.99


class TestDiagnostics:
    def test_summarize_counts_and_breakdown(self) -> None:
        from ingestion.zenrows_scraper import ZenRowsFetchResult

        results = {
            "https://a.com": ZenRowsFetchResult(url="https://a.com", success=True, status_code=200),
            "https://b.com": ZenRowsFetchResult(url="https://b.com", success=False, status_code=403),
            "https://c.com": ZenRowsFetchResult(url="https://c.com", success=False, status_code=403),
        }
        summary = summarize(results)
        assert summary["total"] == 3
        assert summary["success"] == 1
        assert summary["failed"] == 2
        assert summary["status_breakdown"] == {200: 1, 403: 2}
        assert summary["failed_urls"] == ["https://b.com", "https://c.com"]

    def test_results_to_dataframe_has_expected_columns(self) -> None:
        from ingestion.zenrows_scraper import ZenRowsFetchResult

        results = {
            "https://a.com": ZenRowsFetchResult(
                url="https://a.com",
                success=True,
                status_code=200,
                data=ZenRowsProductData(product_price=9.99, rating=4.5),
            ),
        }
        df = results_to_dataframe(results)
        assert list(df.columns) == [
            "url", "success", "status_code", "error", "product_price",
            "product_description", "num_variants", "rating", "rating_count",
        ]
        assert df.iloc[0]["product_price"] == 9.99


class TestExtendedCascadeTier45And5:
    """Phase 0.5e: Tier 4.5 (builder fingerprint) and Tier 5 (LLM fallback)
    extend extract_product_data() when Tiers 1-4 find nothing. Tier 4.5 is
    deterministic/free and always attempted; Tier 5 makes a real Replicate
    API call and is opt-in via enable_llm_fallback (default False) so every
    existing caller/test above this class keeps its original, network-free
    behavior unchanged."""

    _PAGEFLY_HTML = """
    <html><body class="pf-container">
      <h1>Rosabella Beetroot</h1>
      <div class="offer-grid">
        <div class="offer-card">1 Bottle $39.95</div>
      </div>
      <div>'Excellent' | 134 reviews</div>
    </body></html>
    """

    def test_tier_1_4_success_skips_tier_4_5_and_5(self) -> None:
        # _JSON_LD_HTML resolves via Tier 2 (JSON-LD) — Tier 4.5/5 must not run.
        data = extract_product_data(_JSON_LD_HTML, url="https://store.com/p")
        assert data.product_price == 39.99
        assert data.page_metadata is None
        assert data.extraction_tier is None

    def test_builder_fingerprint_fills_when_tiers_1_4_find_nothing(self) -> None:
        data = extract_product_data(self._PAGEFLY_HTML, url="https://store.com/p")
        assert data.product_price == 39.95
        assert data.rating_count == 134
        assert data.extraction_tier is not None
        assert data.extraction_tier.value == "tier_4_5_builder"
        assert data.product_name == "Rosabella Beetroot"  # from the fixture's <h1>
        # Regression (variants_featured Round 1, issue #35): _merge_direct_
        # response_into used to pull ONLY the price out of offer_matrix,
        # discarding every tier_label -- confirmed live as the dominant
        # reason variants_featured stayed near-0% on every ad whose
        # extraction_method ended in tier_4_5_builder/tier_5_llm.
        assert data.variants == ["1 Bottle"]

    def test_llm_fallback_not_attempted_by_default(self) -> None:
        from unittest.mock import patch

        no_signature_html = "<html><body><p>Nothing structured here at all.</p></body></html>"
        with patch(_LLM_PATCH_TARGET) as mock_llm:
            data = extract_product_data(no_signature_html, url="https://store.com/p")
            mock_llm.assert_not_called()
        assert data.page_metadata is None

    def test_llm_fallback_runs_when_opted_in_and_nothing_else_resolved(self) -> None:
        from unittest.mock import patch

        from ingestion.llm_fallback import _DirectResponseLLMExtraction, _LLMOfferTier

        no_signature_html = (
            "<html><body><h1>Sleep Aid Drops</h1>"
            "<p>1 Item just $19.99 today. Rated by 88 happy customers.</p></body></html>"
        )
        mock_extraction = _DirectResponseLLMExtraction(
            product_title="Sleep Aid Drops",
            offer_matrix=[
                _LLMOfferTier(
                    tier_label="1 Item", quantity=1, total_price=19.99,
                    price_per_unit=19.99, currency="USD",
                )
            ],
            review_count=88,
        )
        with patch(_LLM_PATCH_TARGET) as mock_llm:
            mock_llm.return_value = mock_extraction
            data = extract_product_data(
                no_signature_html, url="https://store.com/p", enable_llm_fallback=True
            )

        assert data.product_price == 19.99
        assert data.rating_count == 88
        assert data.extraction_tier is not None
        assert data.extraction_tier.value == "tier_5_llm"
        assert data.variants == ["1 Item"]
        # content_hash is filled in post-hoc from the raw HTML (llm_fallback.py
        # itself never sees the full page, only the pruned Markdown).
        assert data.page_metadata.content_hash != ""

    def test_llm_fallback_hallucinated_value_is_guardrailed_out(self) -> None:
        from unittest.mock import patch

        from ingestion.llm_fallback import _DirectResponseLLMExtraction

        no_signature_html = (
            "<html><body><h1>Mystery Product</h1><p>No numbers on this page.</p></body></html>"
        )
        mock_extraction = _DirectResponseLLMExtraction(
            product_title="Mystery Product", review_count=99999
        )
        with patch(_LLM_PATCH_TARGET) as mock_llm:
            mock_llm.return_value = mock_extraction
            data = extract_product_data(
                no_signature_html, url="https://store.com/p", enable_llm_fallback=True
            )

        # 99999 never appears in the raw text -> guardrail nulls it -> no
        # min-fields signal -> overall extraction stays empty, not fabricated.
        assert data.rating_count is None

    def test_price_missing_triggers_tier_4_5_even_when_other_fields_already_resolved(self) -> None:
        """Regression: previously, _has_min_fields treated price as fungible
        with description/rating/variants via a single OR-chain, so a page
        where Tiers 1-4 resolved a description but never a price was marked
        "successful" and never got a price-specific fallback attempt at all
        — confirmed live as the dominant reason price coverage (34.6%)
        lagged product_page coverage (95.4%) so heavily. Price alone must
        now trigger Tier 4.5, independent of what else already succeeded."""
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "description": "A description, but no offers/price key at all."}
        </script>
        <div class="pf-container">
          <div class="offer-card">1 Bottle $39.95</div>
        </div>
        """
        data = extract_product_data(html, url="https://store.com/p")
        assert data.product_description == "A description, but no offers/price key at all."
        assert data.product_price == 39.95
        assert data.extraction_tier is not None
        assert data.extraction_tier.value == "tier_4_5_builder"

    def test_multi_tier_offer_matrix_maps_every_tier_label_to_variants(self) -> None:
        """A quantity-bundle ladder ("1 bottle"/"3 bottles"/"6 bottles") is a
        real form of variants_featured for this pipeline's purposes (the ML
        target variable variants_featured_count cares about purchase-quantity
        complexity, not just flavor/size SKUs) -- every tier's label must
        survive the merge, not just the one used for price."""
        html = """
        <div class="pf-container">
          <div class="offer-card">1 Bottle $39.95</div>
          <div class="offer-card">3 Bottles $89.95</div>
          <div class="offer-card">6 Bottles $149.95</div>
        </div>
        """
        data = extract_product_data(html, url="https://store.com/p")
        assert data.variants == ["1 Bottle", "3 Bottles", "6 Bottles"]

    def test_amazon_buy_box_price_does_not_leak_into_variants(self) -> None:
        """Regression (issue #35): the Amazon extractor's OfferTier exists
        purely to carry a price through best_offer() -- its tier_label is
        deliberately empty (not the old hardcoded "Amazon buy box"), so it
        must never surface as a fake product variant end-to-end through the
        real cascade, not just at the builder_fingerprint unit level."""
        html = """
        <html><body>
        <span id="productTitle">Immune Support Supplement</span>
        <div id="corePrice_feature_div">
          <span class="a-price"><span class="a-offscreen">$17.95</span></span>
        </div>
        </body></html>
        """
        data = extract_product_data(html, url="https://www.amazon.com/dp/B08WKV7LV6")
        assert data.product_price == 17.95
        assert data.variants == []

    def test_to_product_page_updates_uses_tier_suffix_for_direct_response_data(self) -> None:
        data = extract_product_data(self._PAGEFLY_HTML, url="https://store.com/p")
        page = to_product_page_updates(data, None, "https://store.com/p")
        assert page.extraction_method == "tier_4_5_builder"
        assert page.price == 39.95


class TestExtractProductDataSubscriptionDetection:
    """Subscription-pricing investigation (2026-08-15): extract_product_data
    always attempts subscription detection when html is available,
    independent of whether Tiers 1-4.5 resolved a one-time price — a page
    can have both. Deterministic (app signature + regex over visible text),
    no LLM call involved."""

    def test_recharge_app_and_daily_rate_detected(self) -> None:
        html = """
        <html><body>
        <script>window.RechargeStorefrontConfig = {customer: null};</script>
        <p>About $1.63/day on subscription. Cancel anytime, no commitments.
        Start with a single bottle if you'd rather try first.</p>
        </body></html>
        """
        data = extract_product_data(html, url="https://store.com/p")
        assert data.subscription_status == "subscription_optional"
        assert data.subscription_price == round(1.63 * 30, 2)

    def test_no_subscription_signal_stays_unknown(self) -> None:
        html = "<html><body><p>Regular one-time product, $29.95.</p></body></html>"
        data = extract_product_data(html, url="https://store.com/p")
        assert data.subscription_status == "unknown"
        assert data.subscription_price is None

    def test_rate_pattern_without_subscription_signal_is_not_extracted(self) -> None:
        """Regression: healthinsider.news (no subscription app or keyword
        anywhere on the page) still matched a "$70 per month" rate via
        regex -- a rhetorical mention about a compared competitor product
        elsewhere on the page, not a real subscription price for the
        featured product. A day/month-rate match is only trusted once
        subscription_status is no longer "unknown"."""
        html = (
            "<html><body><p>Ozempic costs $70 per month for most patients, "
            "but our featured supplement is a one-time purchase of $29.95.</p></body></html>"
        )
        data = extract_product_data(html, url="https://store.com/p")
        assert data.subscription_status == "unknown"
        assert data.subscription_price is None

    def test_subscription_detected_alongside_one_time_price(self) -> None:
        """A page can have a real structured one-time price (JSON-LD) AND a
        separate subscription option -- both should populate independently."""
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "offers": {"price": "39.95"}}
        </script>
        <div class="recharge-widget">Subscribe & Save 20% -- $31.96/month</div>
        """
        data = extract_product_data(html, url="https://store.com/p")
        assert data.product_price == 39.95
        assert data.subscription_status == "subscription_optional"
        assert data.subscription_price == 31.96

    def test_no_html_stays_unknown(self) -> None:
        data = extract_product_data(None)
        assert data.subscription_status == "unknown"
        assert data.subscription_price is None
