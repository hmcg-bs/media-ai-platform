"""ZenRows-based product page extraction.

Managed scraping API (JS rendering + proxy rotation + anti-bot evasion
server-side) — replaces a self-hosted TLS-impersonation approach that was
designed but never built. The tiered scraper's rate limiter
(ingestion/rate_limiter.py) fixed a volume-triggered block, but a residual
45.5% of unique URLs still failed 429/403, broadly and immediately across
unrelated domains — a fingerprinting signature, not volume. ZenRows handles
that server-side instead of self-hosting it.

Extracts 5 fields via a 4-tier parsing cascade (XHR capture -> JSON-LD ->
platform window objects -> DOM fallback), each tier only filling fields
earlier tiers left empty: product_price, product_description, variants,
rating, rating_count. Tier 2 (JSON-LD) additionally attempts product_name
(Product.name) and brand_name (Product.brand, falling back to an
Organization block's name or <meta property="og:site_name">) — deterministic
and free, so a page never has to wait on Tier 4.5/5 just for these two
fields when JSON-LD already has them. This does NOT replace the existing LLM-based semantic
enrichment (product_category/usp/brand_name/cultural_branding — see
product_page_analyzer.py) — results merge into whatever ProductPage already
exists for a URL via to_product_page_updates().

Also the only source for rating/rating_count at any real scale: Shopify's
core product.json schema (ingestion/shopify_json.py) has no review fields —
those come from third-party widgets (Loox/Yotpo/Judge.me) injected
client-side, which is exactly what js_render + this cascade is for.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from ingestion.builder_fingerprint import (
    extract_via_builder_fingerprint,
    get_amazon_region,
    is_amazon_url,
)
from ingestion.dedupe import get_content_hash
from ingestion.direct_response_schema import (
    DirectResponseProductData,
    ExtractionTier,
    PageMetadata,
    best_offer,
)
from ingestion.llm_fallback import extract_via_llm, validate_against_raw_text
from ingestion.product_page import ProductPage
from ingestion.product_page_analyzer import _extract_visible_text
from ingestion.subscription_detector import (
    determine_subscription_status,
    extract_subscription_price,
)
from ingestion.zone_pruner import prune_to_markdown
from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ZenRowsProductData:
    """Raw extraction result — field names match the user's spec exactly,
    kept distinct from ProductPage (see to_product_page_updates for the bridge)."""

    product_price: float | None = None
    product_description: str = ""
    variants: list[str] = field(default_factory=list)
    rating: float | None = None
    rating_count: int | None = None
    # Provenance from Tier 4.5/5 (Phase 0.5e) — None when Tiers 1-4 resolved
    # the page (the common case) or when the page is still fully unresolved.
    page_metadata: PageMetadata | None = None
    extraction_tier: ExtractionTier | None = None
    # Also Tier 4.5/5-only — outside the user's original 5-field ZenRows
    # spec, but confirmed live (50-URL batch run) that the LLM tier reliably
    # extracts a real product title/brand while every other tier leaves the
    # page with product_page=None entirely; discarding it here just to keep
    # the original 5-field shape strict would throw away real, free data.
    product_name: str | None = None
    brand_name: str | None = None
    # Subscription commerce (subscription-pricing investigation, 2026-08-15)
    # — always attempted when html is available, independent of whether
    # Tiers 1-4.5 already resolved a one-time price, since a page can have
    # both a structured one-time price AND a subscription option a
    # different tier entirely wouldn't know to look for.
    subscription_status: str = "unknown"
    subscription_price: float | None = None
    # Marketplace region (continued extraction-gap investigation, 2026-08-16)
    # — deterministic, keyed off the URL's own domain TLD (get_amazon_region),
    # not an LLM guess. Always attempted for Amazon URLs regardless of which
    # tier resolved the rest of the page, since "which marketplace does this
    # ad's landing page target" is worth knowing on its own, not just an
    # internal proxy-routing detail.
    marketplace_region: str | None = None
    product_currency: str | None = None


@dataclass
class ZenRowsFetchResult:
    url: str
    success: bool
    status_code: int | None = None
    error: str | None = None
    data: ZenRowsProductData | None = None
    # Raw fetched HTML (Phase 0.5e) — lets a corpus-level caller run
    # content-hash/MinHash near-duplicate dedup, or re-run extract_product_data
    # with enable_llm_fallback=True, without a second network fetch.
    html: str | None = None
    # ZenRows' own Zr-Final-Url response header, when it differs from `url`
    # (confirmed live: affiliate-cloaking short-links -- amzn.to, ampd.to --
    # resolve to a real destination page ZenRows already followed). Kept
    # separate from `url` (which stays the original ad's link_url, for
    # identity/dedup against the corpus) purely for provenance.
    final_url: str | None = None


# ─── Cleaning / normalization ───────────────────────────────────────────

_NUMERIC_RE = re.compile(r"[^\d.\-\s]")
_PRICE_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")


def clean_price(raw: Any) -> float | None:
    """Strip currency symbols/commas, parse to float. '$49.99' -> 49.99.

    Some pages emit non-standard schema.org values (e.g. offers.price as a
    nested dict/list instead of a plain string/number) — confirmed live
    during the full corpus run (AttributeError crash on one real page
    before this guard was added). Anything that isn't str/int/float
    degrades to None rather than raising, matching this function's
    graceful-on-bad-input contract for every other case.

    Also handles multi-number text (e.g. a before/after-price DOM element
    like `<s>$59.99</s><span>$39.99</span>` whose separated text reads
    "$59.99 $39.99") by taking the last number found rather than failing to
    parse the concatenation — the discounted/sale price conventionally
    renders after the strikethrough original in DOM order.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    cleaned = _NUMERIC_RE.sub("", raw.replace(",", ""))
    tokens = _PRICE_TOKEN_RE.findall(cleaned)
    if not tokens:
        return None
    try:
        return float(tokens[-1])
    except ValueError:
        return None


_REVIEW_COUNT_RE = re.compile(r"[\d,]+")


def clean_review_count(raw: Any) -> int | None:
    """Parse '1,420 Reviews' -> 1420. Same defensive non-str/int guard as
    clean_price — see its docstring."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    match = _REVIEW_COUNT_RE.search(raw)
    if not match:
        return None
    try:
        return int(match.group().replace(",", ""))
    except ValueError:
        return None


def _extract_portable_text(value: Any, out: list[str]) -> None:
    """Recursively pull text nodes out of a Sanity/Portable-Text-style JSON
    tree (`{"type": "root"/"paragraph"/..., "children": [...]}`, each leaf
    a `{"type": "text", "value": "..."}`). Confirmed live: at least one
    Shopify headless-theme store (im8health.com) embeds this directly as
    the JSON-LD Product `description` string instead of plain text/HTML."""
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("value"), str):
            out.append(value["value"])
        children = value.get("children")
        if isinstance(children, list):
            for child in children:
                _extract_portable_text(child, out)
    elif isinstance(value, list):
        for item in value:
            _extract_portable_text(item, out)


def clean_description(html_or_text: str | None) -> str:
    """Strip HTML tags/excess whitespace, or extract plain text from
    Portable-Text-style JSON if that's what's embedded (see
    _extract_portable_text docstring). Reuses product_page_analyzer.py's
    tag-strip helper for the plain-HTML case rather than a second
    implementation."""
    if not html_or_text:
        return ""

    stripped = html_or_text.strip()
    if stripped.startswith('{"type"') or stripped.startswith('{\\"type\\"'):
        try:
            parsed = json.loads(html_or_text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            parts: list[str] = []
            _extract_portable_text(parsed, parts)
            if parts:
                return " ".join(parts).strip()[:3000]

    return _extract_visible_text(html_or_text, max_chars=3000)


# ─── Tier 1: background XHR / captured JSON payloads ────────────────────


def _extract_from_xhr(zenrows_json_response: Any) -> dict[str, Any]:
    """Search ZenRows' captured background XHR/JSON responses (if the
    json_response param actually returns them — NOT confirmed against
    ZenRows' PyPI package description, only js_render/wait/wait_for/
    autoparse/css_extractor were documented there) for Shopify
    /products.json, WooCommerce endpoints, or review-widget
    (Loox/Yotpo/Judge.me) payloads.

    Verify the real response shape during the live smoke test; designed to
    degrade gracefully (return {}) if the assumed shape isn't present,
    letting tiers 2-4 fill in instead.
    """
    result: dict[str, Any] = {}
    if not zenrows_json_response:
        return result

    entries = (
        zenrows_json_response if isinstance(zenrows_json_response, list) else [zenrows_json_response]
    )
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        body = entry.get("body") if isinstance(entry.get("body"), dict) else entry

        product = body.get("product")
        if not product and isinstance(body.get("products"), list) and body["products"]:
            product = body["products"][0]

        if isinstance(product, dict):
            variants = product.get("variants") or []
            if variants and "variants" not in result:
                titles = [v.get("title") for v in variants if isinstance(v, dict) and v.get("title")]
                if titles:
                    result["variants"] = titles
            prices = [clean_price(v.get("price")) for v in variants if isinstance(v, dict)]
            prices = [p for p in prices if p is not None]
            if prices and "product_price" not in result:
                result["product_price"] = min(prices)
            if product.get("body_html") and "product_description" not in result:
                result["product_description"] = clean_description(product["body_html"])

        for rating_key in ("average_rating", "rating", "score"):
            if rating_key in body and "rating" not in result:
                parsed = clean_price(body[rating_key])
                if parsed is not None:
                    result["rating"] = parsed
                break

        for count_key in ("total_reviews", "review_count", "reviews_count"):
            if count_key in body and "rating_count" not in result:
                parsed = clean_review_count(body[count_key])
                if parsed is not None:
                    result["rating_count"] = parsed
                break

    return result


# ─── Tier 2: JSON-LD (Schema.org Product / AggregateRating) ─────────────


def _brand_name_from(value: Any) -> str | None:
    # schema.org `brand` is either a plain string or a Brand/Organization
    # object with its own `name` — both appear in the wild.
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and isinstance(value.get("name"), str) and value["name"].strip():
        return value["name"].strip()
    return None


# Shopify's default theme renders <title> as "{page_title} – {shop_name}"
# (en-dash), and pages that customize their own SEO title commonly nest a
# second "ProductName | BrandName" split inside that. Splitting on the
# *last* separator isolates the page's own title from the shop-name suffix;
# splitting the result again on its *first* separator strips any inner
# brand mention, leaving just the product name. Deliberately only en-dash
# "–" and pipe "|" — NOT a plain hyphen "-", which advertorial/hook-style
# titles use internally as a generic word-joiner (e.g.
# "Advertorial - Personal Story - Comparison - Apr 1, 16:47:22 – Jevawell"
# — treating plain "-" as a separator would wrongly split at "Advertorial").
# Confirmed live (multi-field extraction-gap investigation): recovers the
# correct product name on alevia.com, bartonsupplements.com,
# rhonutrition.com, tryorgatics.com; the 50-char length bound correctly
# rejects long advertorial-hook titles that also happen to end in
# " – ShopName" (mengotomars.com, and jevawell.com's CMS page-type/timestamp
# label "Advertorial - Personal Story - Comparison - Apr 1, 16:47:22", 59
# chars — real product names in this corpus have consistently come in well
# under 50).
_TITLE_SEGMENT_SEP_RE = re.compile(r"\s+[–|]\s+")
_MAX_TITLE_PRODUCT_NAME_LEN = 50


def _product_name_from_title(title: str) -> str | None:
    title = title.strip()
    if not title:
        return None
    matches = list(_TITLE_SEGMENT_SEP_RE.finditer(title))
    if not matches:
        return None
    candidate = title[: matches[-1].start()].strip()
    inner_matches = list(_TITLE_SEGMENT_SEP_RE.finditer(candidate))
    if inner_matches:
        candidate = candidate[: inner_matches[0].start()].strip()
    if not candidate or len(candidate) > _MAX_TITLE_PRODUCT_NAME_LEN:
        return None
    return candidate


def _extract_from_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    result: dict[str, Any] = {}
    # product_name/brand_name used to be extracted nowhere in Tiers 1-4 at
    # all — they were only ever populated by Tier 4.5/builder-fingerprint or
    # Tier 5/LLM fallback (ingestion/llm_fallback.py's product_info), which
    # only run when Tiers 1-4 already came up short elsewhere. So a page
    # whose JSON-LD cleanly gave price/description/rating never got a chance
    # at name/brand even though schema.org's Product.name/Product.brand are
    # among the most universal fields on a Product block. Confirmed live
    # (multi-field extraction-gap investigation): goldsealsupplements.com and
    # mengotomars.com had real, LLM-visible product/brand text on the page,
    # but the LLM's zone-pruned view of these long-form "advertorial" pages
    # never included it — while a deterministic JSON-LD/meta read recovers
    # brand_name (og:site_name="Gold Seal Supplements",
    # Organization.name="Mars Men") for free, no LLM call needed.
    org_brand_name: str | None = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            if isinstance(item_type, list):
                item_type = item_type[0] if item_type else ""

            if item_type == "Organization":
                if org_brand_name is None:
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        org_brand_name = name.strip()
                continue

            if item_type != "Product":
                continue

            if item.get("name") and "product_name" not in result:
                name = item["name"]
                if isinstance(name, str) and name.strip():
                    result["product_name"] = name.strip()

            if item.get("brand") and "brand_name" not in result:
                brand = _brand_name_from(item["brand"])
                if brand:
                    result["brand_name"] = brand

            if item.get("description") and "product_description" not in result:
                result["product_description"] = clean_description(item["description"])

            offers = item.get("offers")
            if offers:
                offer = offers[0] if isinstance(offers, list) and offers else offers
                if isinstance(offer, dict) and offer.get("price") and "product_price" not in result:
                    parsed = clean_price(offer["price"])
                    if parsed is not None:
                        result["product_price"] = parsed

            agg_rating = item.get("aggregateRating")
            if isinstance(agg_rating, dict):
                if agg_rating.get("ratingValue") and "rating" not in result:
                    parsed = clean_price(agg_rating["ratingValue"])
                    if parsed is not None:
                        result["rating"] = parsed
                if agg_rating.get("reviewCount") and "rating_count" not in result:
                    parsed = clean_review_count(agg_rating["reviewCount"])
                    if parsed is not None:
                        result["rating_count"] = parsed

    # Product.brand outranks the site-wide Organization block (a specific
    # product's stated brand beats "whoever owns this website" — relevant on
    # multi-brand marketplaces), so this is a setdefault, not an overwrite.
    if org_brand_name:
        result.setdefault("brand_name", org_brand_name)

    og_site_name = soup.select_one('meta[property="og:site_name"]')
    if og_site_name is not None:
        content = og_site_name.get("content")
        if isinstance(content, str) and content.strip():
            result.setdefault("brand_name", content.strip())

    if soup.title and soup.title.string:
        title_name = _product_name_from_title(soup.title.string)
        if title_name:
            result.setdefault("product_name", title_name)

    return result


# ─── Tier 3: platform window objects ─────────────────────────────────────

_WINDOW_OBJECT_PATTERNS = [
    re.compile(r"window\.ShopifyAnalytics\.meta\.product\s*=\s*(\{.*?\});", re.DOTALL),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", re.DOTALL),
]

# Shopify's own Pixel-loader `initData` and Apple-Pay/Shopify-Payments
# merchant-capabilities block both carry the storefront's own shop name —
# present in the raw inline-script JS (not necessarily inside a
# application/ld+json tag or a <meta og:site_name>, so JSON-LD parsing
# doesn't reach it) on effectively every Shopify page, independent of
# whether the page has any Product/Organization JSON-LD at all. Confirmed
# live (multi-field extraction-gap investigation): getionix.com has neither
# JSON-LD nor og:site_name, but both these patterns cleanly resolve
# "IONIX LABS".
_SHOP_NAME_RE = re.compile(r'"shop"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"')
_MERCHANT_NAME_RE = re.compile(r'"merchantName"\s*:\s*"([^"]+)"')


def _extract_from_window_objects(html: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    shop_name_match = _SHOP_NAME_RE.search(html) or _MERCHANT_NAME_RE.search(html)
    if shop_name_match:
        name = shop_name_match.group(1).strip()
        if name:
            result["brand_name"] = name

    for pattern in _WINDOW_OBJECT_PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue

        variants = data.get("variants")
        if variants and "variants" not in result:
            titles = [
                v.get("title") or v.get("name") for v in variants if isinstance(v, dict)
            ]
            titles = [t for t in titles if t]
            if titles:
                result["variants"] = titles

        if "product_price" not in result and data.get("price") is not None:
            parsed = clean_price(data["price"])
            if parsed is not None:
                # Shopify's window.ShopifyAnalytics product object commonly
                # stores price in cents (a well-known Shopify quirk) — this
                # heuristic needs validating against a real page during the
                # live smoke test, not fully certain.
                result["product_price"] = parsed / 100 if parsed > 1000 else parsed

    return result


# ─── Tier 4: DOM fallback (generic selectors + review-widget detection) ──

_PRICE_SELECTORS = [
    '[itemprop="price"]',
    "[data-price]",
    ".price-item--sale",
    ".price-item--regular",
    ".price-item",
    ".price__regular",
    ".price__sale",
    ".product__price",
    ".product-price",
    ".woocommerce-Price-amount",
    ".price",
    ".money",
]
_DESCRIPTION_SELECTORS = ['[itemprop="description"]', ".product-description", ".product__description"]
_RATING_SELECTORS = ['[itemprop="ratingValue"]', ".rating-value", "[data-rating]"]
_REVIEW_COUNT_SELECTORS = ['[itemprop="reviewCount"]', ".review-count", "[data-review-count]"]


def _first_attr_or_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = soup.select_one(sel)
        if el is None:
            continue
        # get_text(separator=" ") — a before/after-price element like
        # <s>$59.99</s><span>$39.99</span> concatenates its child text nodes
        # with no separator by default ("$59.99$39.99"), which clean_price
        # then silently fails to parse. Confirmed live against a real
        # Shopify theme markup pattern.
        text = (
            el.get("content")
            or el.get("data-price")
            or el.get("data-rating")
            or el.get_text(separator=" ", strip=True)
        )
        if text:
            return text
    return None


def _extract_review_widgets(soup: BeautifulSoup) -> dict[str, Any]:
    """Loox/Yotpo/Judge.me/Okendo widget-attribute detection — common enough
    on Shopify DTC storefronts to warrant dedicated handling beyond generic
    itemprop/CSS-class selectors. NOTE (confirmed against a real live page,
    im8health.com): a review platform's JS variables being present on a page
    doesn't mean rating data is populated — the widget may load
    asynchronously and not have resolved within the render wait, or the
    product may genuinely have no reviews yet. This tier can only extract
    what's actually in the captured HTML; that's a real coverage ceiling,
    not a selector bug — expect this tier to sometimes come up empty on
    real pages that visually show star ratings if JS wait time is too short."""
    result: dict[str, Any] = {}

    yotpo_el = soup.select_one("[data-yotpo-average-rating], .yotpo-bottomline")
    if yotpo_el:
        rating_attr = yotpo_el.get("data-yotpo-average-rating") or yotpo_el.get("data-score")
        if rating_attr:
            parsed = clean_price(rating_attr)
            if parsed is not None:
                result["rating"] = parsed
        count_attr = yotpo_el.get("data-yotpo-total-reviews") or yotpo_el.get("data-total-reviews")
        if count_attr:
            parsed = clean_review_count(count_attr)
            if parsed is not None:
                result["rating_count"] = parsed

    loox_el = soup.select_one("loox-rating, [data-loox-rating]")
    if loox_el and "rating" not in result:
        rating_attr = loox_el.get("data-rating") or loox_el.get("data-loox-rating")
        if rating_attr:
            parsed = clean_price(rating_attr)
            if parsed is not None:
                result["rating"] = parsed
        count_attr = loox_el.get("data-raters") or loox_el.get("data-reviews-count")
        if count_attr:
            parsed = clean_review_count(count_attr)
            if parsed is not None:
                result["rating_count"] = parsed

    jdgm_el = soup.select_one(".jdgm-prev-badge, .jdgm-widget")
    if jdgm_el and "rating" not in result:
        rating_attr = jdgm_el.get("data-average-rating")
        if rating_attr:
            parsed = clean_price(rating_attr)
            if parsed is not None:
                result["rating"] = parsed
        count_attr = jdgm_el.get("data-number-of-reviews")
        if count_attr:
            parsed = clean_review_count(count_attr)
            if parsed is not None:
                result["rating_count"] = parsed

    okendo_el = soup.select_one("[data-oke-reviews-product-rating], [data-oke-reviews-product-review-count]")
    if okendo_el and "rating" not in result:
        rating_attr = okendo_el.get("data-oke-reviews-product-rating")
        if rating_attr:
            parsed = clean_price(rating_attr)
            if parsed is not None:
                result["rating"] = parsed
        count_attr = okendo_el.get("data-oke-reviews-product-review-count")
        if count_attr:
            parsed = clean_review_count(count_attr)
            if parsed is not None:
                result["rating_count"] = parsed

    return result


def _extract_from_dom(soup: BeautifulSoup) -> dict[str, Any]:
    result: dict[str, Any] = {}

    price_text = _first_attr_or_text(soup, _PRICE_SELECTORS)
    if price_text:
        parsed = clean_price(price_text)
        if parsed is not None:
            result["product_price"] = parsed

    desc_text = _first_attr_or_text(soup, _DESCRIPTION_SELECTORS)
    if desc_text:
        result["product_description"] = clean_description(desc_text)

    rating_text = _first_attr_or_text(soup, _RATING_SELECTORS)
    if rating_text:
        parsed = clean_price(rating_text)
        if parsed is not None:
            result["rating"] = parsed

    review_count_text = _first_attr_or_text(soup, _REVIEW_COUNT_SELECTORS)
    if review_count_text:
        parsed = clean_review_count(review_count_text)
        if parsed is not None:
            result["rating_count"] = parsed

    if "rating" not in result or "rating_count" not in result:
        widget_data = _extract_review_widgets(soup)
        for k, v in widget_data.items():
            result.setdefault(k, v)

    return result


# ─── Cascade composition ─────────────────────────────────────────────────


def _has_min_fields(merged: dict[str, Any]) -> bool:
    # rating_count matters on its own, not just alongside rating — confirmed
    # live on track.tryrosabella.com: Tier 5 recovered a real review count
    # ("134 reviews") with no accompanying star rating on the page at all;
    # omitting rating_count here would mark that a failure despite real data.
    #
    # product_price is deliberately NOT part of this OR-chain — it used to
    # be, which meant a page where Tiers 1-4 found a description/rating but
    # never resolved a price was treated as fully successful and never got a
    # price-specific fallback attempt. Confirmed live as the dominant reason
    # `price` coverage (34.6%) lagged `product_page` coverage (95.4%) so
    # heavily: description/rating survive far more reliably than price
    # (which is often client-side/variant-dependent), so "any field found"
    # was silently gating price out of ever being retried. See
    # extract_product_data, which checks price separately.
    return bool(
        merged.get("product_description")
        or merged.get("variants")
        or merged.get("rating")
        or merged.get("rating_count")
    )


def _merge_direct_response_into(merged: dict[str, Any], data: DirectResponseProductData) -> None:
    """Maps the richer DirectResponseProductData (offer_matrix, social_proof)
    onto the flat ZenRowsProductData field names, same setdefault-only
    priority as every other tier in this cascade."""
    offer = best_offer(data.offer_matrix)
    if offer is not None:
        price = offer.price_per_unit if offer.price_per_unit is not None else offer.total_price
        if price is not None:
            merged.setdefault("product_price", price)
    # variants_featured gap (variants_featured Round 1, issue #35): Tier 4.5
    # (builder_fingerprint.py) and Tier 5 (llm_fallback.py) both already
    # extract quantity/bundle tier labels ("1 bottle", "3 bottles", ...) into
    # offer_matrix -- best_offer() above only ever pulled out ONE tier's
    # *price*, silently discarding every tier_label. Confirmed live: every
    # extraction_method ending in tier_5_llm or zenrows (i.e. never touched
    # by a variants-capable Tier 1-4 structured-data source) showed ~100%
    # missing variants_featured corpus-wide -- a structural gap, not a
    # per-page miss. Same setdefault-only, additive convention as every
    # other field here; unprefixed like this module's own Tier 2-4 variant
    # titles (shopify_json.py prefixes with "Variant: ", this module doesn't
    # -- a pre-existing inconsistency between the two modules, not
    # introduced here).
    tier_labels = [o.tier_label for o in data.offer_matrix if o.tier_label]
    if tier_labels:
        merged.setdefault("variants", tier_labels)
    if data.product_info.key_specs:
        merged.setdefault("product_description", " ".join(data.product_info.key_specs))
    if data.social_proof.rating_value is not None:
        merged.setdefault("rating", data.social_proof.rating_value)
    if data.social_proof.review_count is not None:
        merged.setdefault("rating_count", data.social_proof.review_count)
    if data.product_info.title:
        merged.setdefault("product_name", data.product_info.title)
    if data.product_info.brand_name:
        merged.setdefault("brand_name", data.product_info.brand_name)


def extract_product_data(
    html: str | None,
    xhr_json: Any = None,
    *,
    url: str = "",
    enable_llm_fallback: bool = False,
) -> ZenRowsProductData:
    """Runs the 4-tier structured-data cascade top-to-bottom; later tiers
    only fill fields earlier tiers left empty, never overwrite.

    If Tiers 1-4 found nothing, tries Tier 4.5 (builder fingerprint —
    deterministic, no network/API call, always attempted) and then, only if
    `enable_llm_fallback=True`, Tier 5 (zone-pruned LLM fallback — makes a
    real Replicate API call, so it's opt-in: default False keeps every
    existing caller/test's behavior and cost profile unchanged; the
    corpus-level --advertorial-fallback path opts in explicitly). See
    ingestion/builder_fingerprint.py and ingestion/llm_fallback.py."""
    merged: dict[str, Any] = {}

    for k, v in _extract_from_xhr(xhr_json).items():
        merged.setdefault(k, v)

    soup = BeautifulSoup(html, "html.parser") if html else None

    if soup is not None:
        for k, v in _extract_from_json_ld(soup).items():
            merged.setdefault(k, v)

    if html:
        for k, v in _extract_from_window_objects(html).items():
            merged.setdefault(k, v)

    if soup is not None:
        for k, v in _extract_from_dom(soup).items():
            merged.setdefault(k, v)

    direct_response: DirectResponseProductData | None = None
    # Trigger Tier 4.5/5 whenever price is still missing, independent of
    # whether Tiers 1-4 already found other fields — see _has_min_fields'
    # docstring for why price can't share its OR-chain.
    price_missing = not merged.get("product_price")
    if (not _has_min_fields(merged) or price_missing) and html:
        direct_response = extract_via_builder_fingerprint(html, url)

        if direct_response is None and enable_llm_fallback:
            pruned = prune_to_markdown(html)
            llm_result = extract_via_llm(pruned, url)
            if llm_result is not None:
                raw_text = _extract_visible_text(html, max_chars=20000)
                validated = validate_against_raw_text(llm_result, raw_text)
                direct_response = validated.model_copy(
                    update={
                        "page_metadata": validated.page_metadata.model_copy(
                            update={"content_hash": get_content_hash(html)}
                        )
                    }
                )

        if direct_response is not None:
            _merge_direct_response_into(merged, direct_response)

    # Subscription commerce: always attempted when html is available,
    # independent of whether Tiers 1-4.5 already resolved a one-time price
    # — a page can have both. Deterministic (app signature + regex over
    # visible text), no LLM call; see ingestion/subscription_detector.py for
    # why this is regex-over-text rather than parsing the app's own JS
    # config object.
    subscription_status = "unknown"
    subscription_price = None
    if html:
        visible_text = _extract_visible_text(html, max_chars=20000)
        subscription_status = determine_subscription_status(html, visible_text)
        # Only trust a day/month-rate regex match as a real subscription
        # price when there's independent evidence this page actually has a
        # subscription option. Confirmed live: healthinsider.news (no
        # subscription app or keyword anywhere) still matched a "$70 per
        # month" rate — a rhetorical mention about a compared competitor
        # product elsewhere on the page, same false-positive class as the
        # main price extraction's banner/rhetorical bugs.
        if subscription_status != "unknown":
            subscription_price = extract_subscription_price(visible_text)

    # Marketplace region: deterministic (URL domain TLD), always attempted
    # for Amazon URLs regardless of which tier resolved the rest of the
    # page — see get_amazon_region's docstring for why an unrecognized
    # Amazon TLD deliberately leaves this None rather than guessing.
    amazon_region = get_amazon_region(url) if url else None
    marketplace_region = amazon_region[0] if amazon_region else None
    product_currency = amazon_region[1] if amazon_region else None

    return ZenRowsProductData(
        product_price=merged.get("product_price"),
        product_description=merged.get("product_description", ""),
        variants=merged.get("variants", []),
        rating=merged.get("rating"),
        rating_count=merged.get("rating_count"),
        page_metadata=direct_response.page_metadata if direct_response else None,
        extraction_tier=direct_response.extraction_tier if direct_response else None,
        product_name=merged.get("product_name"),
        brand_name=merged.get("brand_name"),
        subscription_status=subscription_status,
        subscription_price=subscription_price,
        marketplace_region=marketplace_region,
        product_currency=product_currency,
    )


# ─── Fetch + batch orchestration ─────────────────────────────────────────


# Review-widget async-rendering retry (opt-in — see enable_review_widget_retry
# below). ZenRows' `wait_for` pauses JS rendering until a CSS selector
# appears, up to 3 minutes — confirmed via ZenRows' own docs (Context7,
# 2026-08-16) more reliable than a blind fixed `wait` delay for exactly the
# "widget hasn't resolved yet" gap `_extract_review_widgets` already
# documents. Critical constraint from the same docs: if the selector never
# matches, the *entire request fails with a 422* — so this can NEVER be
# applied blindly to every fetch (most pages have no review widget at all,
# which would turn today's successful fetches into failures). Two guards
# keep this safe: (1) only attempted when the first, normal fetch already
# found a review-app script signature but no rendered rating data — strong
# evidence the widget exists and just hasn't mounted yet, not a guess; (2)
# the retry is wrapped so any exception/bad-status (including a 422 on a
# false-positive signature match) falls back to the original result rather
# than failing the whole fetch.
_REVIEW_WIDGET_APP_SIGNATURE_RE = re.compile(r"yotpo|loox|judge\.me|jdgm|okendo", re.IGNORECASE)
_REVIEW_WIDGET_WAIT_FOR_SELECTOR = (
    "[data-yotpo-average-rating], .yotpo-bottomline, "
    "loox-rating, [data-loox-rating], "
    ".jdgm-prev-badge, .jdgm-widget, "
    "[data-oke-reviews-product-rating], [data-oke-reviews-product-review-count]"
)


async def fetch_product_zenrows(
    client: Any,
    url: str,
    *,
    js_render: bool = True,
    wait_ms: int = 2000,
    enable_llm_fallback: bool = False,
    enable_review_widget_retry: bool = False,
) -> ZenRowsFetchResult:
    """Single-URL fetch + parse via ZenRows. Never raises — per-URL
    exceptions caught, graceful (matches the project's established
    never-kill-the-batch convention). `client` needs only an async
    `get_async(url, params=...)` method — a real ZenRowsClient or a test
    fake, never constructed here directly for testability.

    `enable_review_widget_retry` (opt-in, default False — no behavior/cost
    change for any existing caller, same convention as enable_llm_fallback):
    if the first fetch finds a review-app signature but no rating data, pays
    for one more fetch with `wait_for` set to wait for the widget's own
    container element. See the module-level comment above for why this is
    conditional rather than applied to every request."""
    params: dict[str, str] = {}
    if js_render:
        params["js_render"] = "true"
        params["wait"] = str(wait_ms)
    if is_amazon_url(url):
        # Amazon serves region-specific pricing/currency based on the
        # requesting IP's geolocation, and neither premium_proxy nor
        # proxy_country was set anywhere in this pipeline — every fetch was
        # subject to whichever country ZenRows' standard proxy pool
        # happened to assign. Confirmed live (continued extraction-gap
        # investigation): the exact same amazon.com URL returned prices in
        # INR ("INR1,716.07") on one fetch and clean USD on another,
        # explaining the wildly-wrong-magnitude price hallucinations Tier 5's
        # LLM produced on Amazon pages that the guardrail then had to
        # reject. Fixed by pinning the proxy to the URL's OWN marketplace
        # region (get_amazon_region, keyed off the domain TLD) rather than
        # blindly forcing "us" for every Amazon URL — every ad's link_url
        # observed in this corpus so far is amazon.com, but forcing US on a
        # genuine amazon.co.uk/amazon.de URL would itself be wrong, not
        # just imprecise. An unrecognized Amazon TLD deliberately sets no
        # proxy_country at all rather than guessing. Scoped to Amazon
        # specifically, not applied to every fetch — premium/geo-targeted
        # proxies cost more, and this is the only site this corpus has
        # confirmed the currency-mismatch bug on.
        region = get_amazon_region(url)
        if region is not None:
            params["premium_proxy"] = "true"
            params["proxy_country"] = region[0]

    try:
        response = await client.get_async(url, params=params)
    except Exception as e:  # noqa: BLE001 — one bad URL shouldn't kill the batch
        logger.warning("zenrows_fetch_failed", url=url, error=str(e))
        return ZenRowsFetchResult(url=url, success=False, error=str(e))

    status_code = getattr(response, "status_code", None)
    if status_code is not None and status_code >= 400:
        logger.warning("zenrows_fetch_bad_status", url=url, status_code=status_code)
        return ZenRowsFetchResult(
            url=url, success=False, status_code=status_code, error=f"HTTP {status_code}"
        )

    html = getattr(response, "text", None)
    # Confirmed live (continued extraction-gap investigation, fresh corpus):
    # affiliate-cloaking short-links (amzn.to, ampd.to) return `url` as the
    # short-link itself, but ZenRows already followed the redirect chain --
    # the real destination is in this header. Using `url` for is_amazon_url
    # routing inside extract_product_data (Tier 4.5's dedicated Amazon
    # extractor) meant a cloaked link that genuinely resolved to a real
    # Amazon page never got routed there: is_amazon_url("amzn.to") is False
    # even when the fetched HTML is 100% Amazon's own markup. Falls back to
    # `url` when the header is absent (a test fake, or ZenRows not setting
    # it for a direct, non-redirected fetch).
    response_headers = getattr(response, "headers", None) or {}
    final_url = response_headers.get("Zr-Final-Url") or url
    try:
        data = extract_product_data(html, url=final_url, enable_llm_fallback=enable_llm_fallback)
    except Exception as e:  # noqa: BLE001 — parsing bugs shouldn't kill the batch
        logger.warning("zenrows_parse_failed", url=url, error=str(e))
        return ZenRowsFetchResult(
            url=url,
            success=False,
            status_code=status_code,
            error=f"parse_error: {e!r}",
            html=html,
            final_url=final_url,
        )

    if (
        enable_review_widget_retry
        and js_render
        and not data.rating
        and not data.rating_count
        and html
        and _REVIEW_WIDGET_APP_SIGNATURE_RE.search(html)
    ):
        retry_params = {**params, "wait_for": _REVIEW_WIDGET_WAIT_FOR_SELECTOR}
        try:
            retry_response = await client.get_async(url, params=retry_params)
            retry_status = getattr(retry_response, "status_code", None)
            retry_html = getattr(retry_response, "text", None)
            if retry_status is not None and retry_status < 400 and retry_html:
                retry_data = extract_product_data(
                    retry_html, url=final_url, enable_llm_fallback=False
                )
                if retry_data.rating or retry_data.rating_count:
                    logger.info("review_widget_wait_for_retry_succeeded", url=url)
                    data = replace(
                        data,
                        rating=retry_data.rating or data.rating,
                        rating_count=retry_data.rating_count or data.rating_count,
                    )
                    html = retry_html
        except Exception as e:  # noqa: BLE001 — a failed retry (incl. 422) just falls back
            logger.debug("review_widget_wait_for_retry_failed", url=url, error=str(e))

    # rating_count included alongside rating — see _has_min_fields's docstring.
    success = bool(
        data.product_price
        or data.product_description
        or data.variants
        or data.rating
        or data.rating_count
    )
    return ZenRowsFetchResult(
        url=url,
        success=success,
        status_code=status_code,
        data=data,
        html=html,
        final_url=final_url,
    )


async def batch_scrape_zenrows(
    urls: list[str],
    *,
    client: Any | None = None,
    enable_llm_fallback: bool = False,
    enable_review_widget_retry: bool = False,
) -> dict[str, ZenRowsFetchResult]:
    """Fires all URLs through client.get_async concurrently. Concurrency is
    bounded by ZenRowsClient's own internal thread pool (constructor
    `concurrency` kwarg) — no separate semaphore needed, unlike the
    superseded curl_cffi design (get_async there wraps a real async
    transport we'd have had to throttle ourselves)."""
    settings = get_settings()
    if client is None:
        from zenrows import ZenRowsClient

        client = ZenRowsClient(
            settings.zenrows_api_key,
            retries=settings.zenrows_retries,
            concurrency=settings.zenrows_concurrency,
        )

    results = await asyncio.gather(
        *(
            fetch_product_zenrows(
                client,
                url,
                js_render=settings.zenrows_js_render,
                wait_ms=settings.zenrows_wait_ms,
                enable_llm_fallback=enable_llm_fallback,
                enable_review_widget_retry=enable_review_widget_retry,
            )
            for url in urls
        )
    )
    return {r.url: r for r in results}


def run_zenrows_batch_sync(
    urls: list[str],
    *,
    client: Any | None = None,
    enable_llm_fallback: bool = False,
    enable_review_widget_retry: bool = False,
) -> dict[str, ZenRowsFetchResult]:
    """Thin asyncio.run() wrapper — the only function the sync orchestrator
    (ingestion/enrich_with_product_pages.py) touches."""
    return asyncio.run(
        batch_scrape_zenrows(
            urls,
            client=client,
            enable_llm_fallback=enable_llm_fallback,
            enable_review_widget_retry=enable_review_widget_retry,
        )
    )


# ─── ProductPage bridge ───────────────────────────────────────────────────


def to_product_page_updates(
    data: ZenRowsProductData,
    existing: ProductPage | None,
    url: str,
) -> ProductPage:
    """Merges ZenRows' 5 fields into an existing ProductPage (preserving
    product_category/usp/brand_name/cultural_branding from prior Tier 1/2/LLM
    enrichment), or creates a minimal new one if none exists yet for this URL."""
    base = existing.model_copy() if existing is not None else ProductPage(url=url)

    updates: dict[str, Any] = {}
    # price/rating/rating_count deliberately use truthy checks, not `is not
    # None` — these are freshness-oriented fields (a later tier's value is
    # allowed to replace an earlier one, unlike product_name/brand_name
    # below), but an explicit 0 from an LLM call means "found nothing", not
    # "confirmed zero" (a real product with a genuine $0 price or a 0.0
    # star rating essentially never happens). Confirmed live as a real bug,
    # not theoretical: a reprocessing run wiped a real rating_count=171 to
    # 0 on eternapure.com/apps/pagefly?id=86f4b49f-... when a fresh Tier 5
    # LLM call found no review count in its own (differently-pruned) view
    # of the page, silently discarding real prior data under the old
    # `is not None` check.
    # Sanity bounds, independent of which tier produced the value —
    # confirmed live as real, not theoretical: a Phase 1 data-exploration
    # pass over the corpus found `price` outliers up to $235,235,112.00
    # (mean pulled to $123,590 against a true median of $44.99) and
    # `rating` values up to 89.0 (mean 5.04 on a 0-5 field). The window-
    # objects tier's own "price is commonly in cents" heuristic
    # (`parsed / 100 if parsed > 1000`, see _extract_from_window_objects)
    # was already flagged in its own comment as "not fully certain" — it
    # has no upper bound on the *result*, so a large non-price integer
    # (a variant/product id landing in a generically-named "price" key
    # inside a framework-agnostic window.__INITIAL_STATE__ blob) sails
    # through undetected. Rather than chase the exact originating tier for
    # every possible bad value, this is the true universal bottleneck for
    # every ZenRows-cascade price/rating (Tiers 1-5 all funnel through
    # here) — one guard here covers all of them. $1,500 is a generous
    # ceiling for a single supplement product/bundle listing; every
    # confirmed-legitimate price seen live this session topped out around
    # $60-100 for a multi-bottle bundle. The $2 floor catches a related,
    # confirmed-real pattern on the low end: an indirect "$0.67/day"
    # subscription rate or a stray Amazon subscribe-and-save fragment
    # ($0.20-$0.30) being stored as if it were the product's full price —
    # nothing in this corpus's confirmed-legitimate price distribution
    # (p10 = $11.99) goes anywhere near this low.
    if data.product_price and 2 <= data.product_price <= 1500:
        updates["price"] = data.product_price
    elif data.product_price:
        logger.warning(
            "product_price_rejected_out_of_range", url=url, value=data.product_price
        )
    if data.product_description:
        updates["marketing_copy"] = data.product_description
    if data.variants:
        updates["variants_featured"] = data.variants
        updates["shows_all_variants"] = len(data.variants) > 1
    if data.rating and 0 < data.rating <= 5:
        updates["rating"] = data.rating
    elif data.rating:
        logger.warning("rating_rejected_out_of_range", url=url, value=data.rating)
    if data.rating_count:
        updates["rating_count"] = data.rating_count
    # Tier 4.5/5-only (outside the original 5-field ZenRows spec) — never
    # overwrite a name/brand a prior tier (Tier 1/2/Stage 4c LLM) already found.
    if data.product_name and not base.product_name:
        updates["product_name"] = data.product_name
    if data.brand_name and not base.brand_name:
        updates["brand_name"] = data.brand_name
    # Subscription-pricing investigation (2026-08-15) — setdefault-style,
    # same never-overwrite pattern as every other field here.
    if data.subscription_status != "unknown" and base.subscription_status == "unknown":
        updates["subscription_status"] = data.subscription_status
    if data.subscription_price is not None and base.subscription_price is None:
        updates["subscription_price"] = data.subscription_price
    # Marketplace region/currency (continued extraction-gap investigation,
    # 2026-08-16) — deterministic (URL domain TLD), not an LLM guess, so
    # safe to overwrite ProductPage.price_currency's "USD" Pydantic
    # default outright rather than needing a setdefault check against it
    # (that default is a placeholder, never a real detected value — no
    # prior tier in this pipeline ever set price_currency for a
    # ZenRows/Tier-4.5/5-resolved page).
    if data.marketplace_region:
        updates["marketplace_region"] = data.marketplace_region
    if data.product_currency:
        updates["price_currency"] = data.product_currency

    if not updates:
        return base

    prior_method = base.extraction_method
    # extraction_tier is set only when Tier 4.5/5 (Phase 0.5e) resolved the
    # page — carries that provenance through instead of the generic
    # "zenrows" suffix, so downstream resume/dedup logic can distinguish them.
    tier_suffix = data.extraction_tier.value if data.extraction_tier is not None else "zenrows"
    updates["extraction_method"] = f"{prior_method}+{tier_suffix}" if prior_method else tier_suffix
    return base.model_copy(update=updates)


# ─── Output / diagnostics ─────────────────────────────────────────────────


def results_to_dataframe(results: dict[str, ZenRowsFetchResult]) -> Any:
    import pandas as pd

    rows = []
    for r in results.values():
        rows.append(
            {
                "url": r.url,
                "success": r.success,
                "status_code": r.status_code,
                "error": r.error,
                "product_price": r.data.product_price if r.data else None,
                "product_description": (r.data.product_description[:100] if r.data else ""),
                "num_variants": len(r.data.variants) if r.data else 0,
                "rating": r.data.rating if r.data else None,
                "rating_count": r.data.rating_count if r.data else None,
            }
        )
    return pd.DataFrame(rows)


def summarize(results: dict[str, ZenRowsFetchResult]) -> dict[str, Any]:
    total = len(results)
    success = sum(1 for r in results.values() if r.success)
    status_counts = Counter(r.status_code for r in results.values() if r.status_code is not None)
    failed_urls = sorted(r.url for r in results.values() if not r.success)
    return {
        "total": total,
        "success": success,
        "failed": total - success,
        "status_breakdown": dict(status_counts),
        "failed_urls": failed_urls,
    }


def write_diagnostics(
    results: dict[str, ZenRowsFetchResult],
    csv_path: Path,
    json_path: Path | None = None,
) -> None:
    df = results_to_dataframe(results)
    df.to_csv(csv_path, index=False)
    if json_path:
        with open(json_path, "w") as f:
            json.dump(summarize(results), f, indent=2)
