"""Tier 4.5: page-builder fingerprint detection + targeted extraction.

Custom "advertorial" funnel pages built on page-builder apps (PageFly,
Zipify, GemPages, ReConvert) emit zero Schema.org/OpenGraph markup, so the
existing structured-data cascade (ingestion/zenrows_scraper.py Tiers 1-4)
finds nothing on them — confirmed live on track.tryrosabella.com and
rituallabs.shop. Detection is genuinely builder-specific (each app stamps
distinctive container classes/CDN hosts into the DOM); the offer-grid and
review-widget markup *within* those containers follows similar-enough
generic patterns (a quantity phrase + a price, a review-count phrase) across
builders that one shared extractor serves all of them — no LLM call needed
for this tier.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ingestion.dedupe import canonicalize_url, get_content_hash
from ingestion.direct_response_schema import (
    DirectResponseProductData,
    ExtractionTier,
    OfferTier,
    PageMetadata,
    PageType,
    ProductInfo,
    SocialProof,
)
from pipeline.logger import get_logger

logger = get_logger(__name__)

BUILDER_SIGNATURES: dict[str, list[str]] = {
    "pagefly": [r"pf-container", r"pagefly-container", r"cdn\.pagefly\.io"],
    "zipify": [r"zp-container", r"zipify-pages", r"cdn\.zipify\.com"],
    "gempages": [r"gf_style", r"gempages-container"],
    "reconvert": [r"reconvert-content", r"reconvert_funnel"],
}

_BUILDER_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns]
    for name, patterns in BUILDER_SIGNATURES.items()
}


def detect_builder(html: str) -> str | None:
    """Returns the builder name on the first signature match, else None.
    Checked in BUILDER_SIGNATURES' dict order (stable in Python 3.7+)."""
    for name, patterns in _BUILDER_PATTERNS.items():
        if any(p.search(html) for p in patterns):
            return name
    return None


_QTY_RE = re.compile(
    # Plural forms listed first in each pair -- regex alternation matches the
    # first alternative that succeeds, not the longest, so "bottle|bottles"
    # (singular first) truncates "3 Bottles" to "3 Bottle" at the match site.
    # Caught by a variants_featured regression test (issue #35): the offer
    # card's tier_label is used verbatim as a variants_featured entry, so
    # this wasn't just cosmetic -- it silently mismatched plurals in real
    # extracted data.
    r"(\d+)\s*(?:bottles|bottle|packs|pack|units|unit|items|item|boxes|box|months|month|supply)",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"([$€£])\s?(\d[\d,]*\.?\d{0,2})")
_BEST_VALUE_RE = re.compile(r"best\s*value|most\s*popular|best\s*seller", re.IGNORECASE)
# Deliberately "reviews?" only, not "ratings?" — a page's numeric star
# rating (e.g. "4.9 stars") frequently sits a few words before a genuine
# review count in generic whole-page text scans; matching "ratings?" too
# risked snapping onto an unrelated preceding number (confirmed by a real
# test failure: "$34.99 rating 4.9 stars, 88 reviews" matched "99" via
# "...99 rating" before this fix).
_REVIEW_COUNT_RE = re.compile(r"([\d,]+)\s*reviews?", re.IGNORECASE)
_RATING_VALUE_RE = re.compile(r"(\d(?:\.\d)?)\s*(?:stars?|out of 5|/\s*5)", re.IGNORECASE)

_CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP"}

# Candidate offer-card containers — deliberately broad since builder wrapper
# classes vary; scoped further by requiring both a quantity phrase and a
# price inside the same element (see _looks_like_offer_card).
_OFFER_CARD_SELECTORS = [
    "[class*='offer']",
    "[class*='tier']",
    "[class*='bundle']",
    "[class*='pricing']",
    "[class*='plan']",
    "[class*='package']",
]


def _looks_like_offer_card(text: str) -> bool:
    return bool(_QTY_RE.search(text) and _PRICE_RE.search(text))


def _parse_offer_card(el, text: str) -> OfferTier | None:
    qty_match = _QTY_RE.search(text)
    price_match = _PRICE_RE.search(text)
    if not qty_match or not price_match:
        return None

    quantity = int(qty_match.group(1))
    symbol = price_match.group(1)
    try:
        total_price = float(price_match.group(2).replace(",", ""))
    except ValueError:
        return None

    return OfferTier(
        tier_label=qty_match.group(0).strip(),
        quantity=quantity,
        total_price=total_price,
        price_per_unit=round(total_price / quantity, 2) if quantity else None,
        currency=_CURRENCY_CODES.get(symbol, "USD"),
        is_best_value=bool(_BEST_VALUE_RE.search(text)),
    )


def _extract_offer_matrix(soup: BeautifulSoup) -> list[OfferTier]:
    seen_texts: set[str] = set()
    offers: list[OfferTier] = []

    candidates = soup.select(", ".join(_OFFER_CARD_SELECTORS))
    for el in candidates:
        text = el.get_text(separator=" ", strip=True)
        if not text or not _looks_like_offer_card(text) or text in seen_texts:
            continue
        # Skip ancestors that just wrap multiple offer cards (their text is a
        # superset containing >1 quantity phrase) — keep the innermost match.
        if len(_QTY_RE.findall(text)) > 1:
            continue
        offer = _parse_offer_card(el, text)
        if offer is not None:
            seen_texts.add(text)
            offers.append(offer)

    return offers


def _extract_social_proof(soup: BeautifulSoup) -> SocialProof:
    text = soup.get_text(separator=" ", strip=True)

    review_count = None
    count_match = _REVIEW_COUNT_RE.search(text)
    if count_match:
        try:
            review_count = int(count_match.group(1).replace(",", ""))
        except ValueError:
            review_count = None

    rating_value = None
    rating_match = _RATING_VALUE_RE.search(text)
    if rating_match:
        try:
            parsed = float(rating_match.group(1))
        except ValueError:
            parsed = None
        # A malformed/unusual phrasing (e.g. a stray "9.8 out of 5" typo,
        # or a nearby unrelated number the regex snapped onto) could still
        # produce a number outside a real 5-star scale despite matching the
        # "out of 5"/"stars"/"/5" wording — same defensive range check as
        # llm_fallback.py's validate_against_raw_text guardrail.
        if parsed is not None and 0 <= parsed <= 5:
            rating_value = parsed

    return SocialProof(review_count=review_count, rating_value=rating_value)


def _extract_product_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        return soup.title.string.strip() or None
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        return text or None
    return None


# ─── Amazon: dedicated marketplace extraction ────────────────────────────
#
# Not a page-builder app, but the same tier-4.5 idea applies: Amazon's own
# checkout/subscription-delivery widget matches zone_pruner's generic
# hero-zone heuristics (see zone_pruner.py's `_CHECKOUT_FLOW_TEXT_RE`
# comment), and even once that's excluded, the real product info lives in
# a handful of Amazon-specific, extremely stable element ids
# (`#productTitle`, `#bylineInfo`) that generic hero-zone/currency-scan
# heuristics were never going to find reliably. Confirmed live (multi-field
# extraction-gap investigation): 8-10 Amazon URLs surfaced in a single
# 61-URL sample of ads missing name/brand/price/rating entirely — common
# enough in this corpus to warrant a dedicated path rather than relying on
# Tier 5's zone-pruned LLM view, which on Amazon pages often lands on
# checkout-widget or nav-breadcrumb text instead of the product itself.
_AMAZON_DOMAIN_RE = re.compile(r"(?:^|\.)amazon\.[a-z.]+$", re.IGNORECASE)
_AMAZON_BYLINE_STRIP_RE = re.compile(
    r"^\s*(?:visit the|brand:)\s*|\s*store\s*$", re.IGNORECASE
)
_AMAZON_RATING_RE = re.compile(r"(\d(?:\.\d)?)\s*out of 5", re.IGNORECASE)
_AMAZON_REVIEW_COUNT_RE = re.compile(r"([\d,]+)")


def is_amazon_url(url: str) -> bool:
    return bool(_AMAZON_DOMAIN_RE.search(urlsplit(url).netloc))


# Amazon operates a separate marketplace per country/TLD, each with its own
# pricing and currency (confirmed live: the *same* amazon.com URL returned
# INR pricing on one fetch and USD on another, purely because nothing in
# this pipeline pinned the proxy's region — Amazon geolocates by IP, not
# just by domain). Mapping the URL's own TLD to its real marketplace
# region — rather than blindly forcing "us" for every Amazon URL — matters
# for two reasons: (1) forcing a US proxy on a genuine amazon.co.uk/
# amazon.de URL would be *wrong*, not just imprecise, since that could
# itself cause Amazon to serve mismatched/blocked content; (2) knowing
# which marketplace an ad's landing page actually targets, and that
# marketplace's real currency, is data worth keeping, not just an internal
# routing detail. Every ad's link_url observed in this corpus so far is
# amazon.com (US) — this map exists so a future amazon.co.uk/amazon.in ad
# is handled correctly rather than silently mis-routed to US pricing.
_AMAZON_TLD_TO_REGION: dict[str, tuple[str, str]] = {
    "amazon.com": ("us", "USD"),
    "amazon.co.uk": ("gb", "GBP"),
    "amazon.de": ("de", "EUR"),
    "amazon.fr": ("fr", "EUR"),
    "amazon.it": ("it", "EUR"),
    "amazon.es": ("es", "EUR"),
    "amazon.nl": ("nl", "EUR"),
    "amazon.se": ("se", "SEK"),
    "amazon.pl": ("pl", "PLN"),
    "amazon.ca": ("ca", "CAD"),
    "amazon.com.mx": ("mx", "MXN"),
    "amazon.com.br": ("br", "BRL"),
    "amazon.co.jp": ("jp", "JPY"),
    "amazon.in": ("in", "INR"),
    "amazon.com.au": ("au", "AUD"),
    "amazon.sg": ("sg", "SGD"),
    "amazon.ae": ("ae", "AED"),
    "amazon.sa": ("sa", "SAR"),
    "amazon.eg": ("eg", "EGP"),
    "amazon.com.tr": ("tr", "TRY"),
}


def get_amazon_region(url: str) -> tuple[str, str] | None:
    """Returns (zenrows_proxy_country_code, currency_code) for a
    recognized Amazon marketplace TLD, or None if the domain isn't Amazon
    at all or is an Amazon TLD not yet in the map above — callers should
    treat None as "don't guess a region" (e.g. skip forcing proxy_country
    entirely) rather than defaulting to US, since defaulting silently
    reintroduces the exact mismatch this function exists to prevent."""
    netloc = urlsplit(url).netloc.lower()
    for tld, region in _AMAZON_TLD_TO_REGION.items():
        if netloc == tld or netloc.endswith("." + tld):
            return region
    return None


def _extract_amazon_brand(soup: BeautifulSoup) -> str | None:
    el = soup.select_one("#bylineInfo")
    if el is None:
        return None
    text = _AMAZON_BYLINE_STRIP_RE.sub("", el.get_text(strip=True)).strip()
    return text or None


def _extract_amazon_social_proof(soup: BeautifulSoup) -> SocialProof:
    rating_value = None
    rating_el = soup.select_one(".a-icon-alt, #acrPopover")
    if rating_el is not None:
        m = _AMAZON_RATING_RE.search(rating_el.get_text(strip=True))
        if m:
            try:
                rating_value = float(m.group(1))
            except ValueError:
                rating_value = None

    review_count = None
    count_el = soup.select_one("#acrCustomerReviewText")
    if count_el is not None:
        m = _AMAZON_REVIEW_COUNT_RE.search(count_el.get_text(strip=True))
        if m:
            try:
                review_count = int(m.group(1).replace(",", ""))
            except ValueError:
                review_count = None

    return SocialProof(review_count=review_count, rating_value=rating_value)


# Scoped to Amazon's own buy-box price containers, not the generic
# `.a-price .a-offscreen` selector alone — that alone matches every price
# mention on the page (strikethrough MSRP, per-unit breakdowns, other
# listings), and blindly taking the first match risks a wrong value.
# `#corePrice_feature_div`/`#apex_desktop` are Amazon's canonical buy-box
# containers; their first `.a-price .a-offscreen` match is the real price.
# Confirmed live: this selector, combined with forcing a US proxy (see
# fetch_product_zenrows — Amazon serves region-based currency, INR/other
# otherwise), returns clean USD prices where Tier 5's LLM previously
# produced wrong-magnitude hallucinations the guardrail had to reject.
_AMAZON_PRICE_SELECTOR = (
    "#corePrice_feature_div .a-price .a-offscreen, #apex_desktop .a-price .a-offscreen"
)


_AMAZON_PRICE_TEXT_RE = re.compile(r"[\d,]+\.\d{2}")


def _extract_amazon_price(soup: BeautifulSoup) -> float | None:
    # `.a-offscreen`'s text is already a clean "$XX.XX" — no HTML noise, no
    # European decimal-comma cases to handle here (Amazon's US buy-box
    # always renders period-decimal), so a small local parse is enough;
    # importing zenrows_scraper.py's fuller clean_price would create a
    # circular import (that module already imports from this one).
    el = soup.select_one(_AMAZON_PRICE_SELECTOR)
    if el is None:
        return None
    m = _AMAZON_PRICE_TEXT_RE.search(el.get_text(strip=True))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _extract_via_amazon(html: str, url: str) -> DirectResponseProductData | None:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("#productTitle")
    title = title_el.get_text(strip=True) if title_el is not None else None
    brand = _extract_amazon_brand(soup)
    social_proof = _extract_amazon_social_proof(soup)
    price = _extract_amazon_price(soup)

    has_social_proof = (
        social_proof.review_count is not None or social_proof.rating_value is not None
    )
    if not title and not brand and not has_social_proof and price is None:
        logger.warning("builder_fingerprint_extract_failed", url=url, builder="amazon")
        return None

    logger.info("builder_fingerprint_detected", url=url, builder="amazon")

    region = get_amazon_region(url)
    currency = region[1] if region is not None else "USD"
    # tier_label deliberately empty, not e.g. "Amazon buy box" -- this
    # OfferTier exists purely as a vehicle to carry the single buy-box price
    # through best_offer(); Amazon's buy box isn't a real product variant.
    # Confirmed live (variants_featured Round 1, issue #35): once
    # _merge_direct_response_into started surfacing every tier_label into
    # variants_featured, a literal "Amazon buy box" string was previously
    # harmless -- now it would pollute variants_featured on every Amazon ad
    # with fake variant data. An empty tier_label is naturally excluded by
    # that merge's `if o.tier_label` check.
    offer_matrix = (
        [OfferTier(tier_label="", quantity=1, total_price=price, currency=currency)]
        if price is not None
        else []
    )

    return DirectResponseProductData(
        page_metadata=PageMetadata(
            canonical_url=canonicalize_url(url),
            original_url=url,
            page_type=PageType.ADVERTORIAL_FUNNEL,
            content_hash=get_content_hash(html),
            builder_detected="amazon",
        ),
        product_info=ProductInfo(title=title, brand_name=brand),
        social_proof=social_proof,
        offer_matrix=offer_matrix,
        extraction_tier=ExtractionTier.TIER_4_5_BUILDER,
    )


def extract_via_builder_fingerprint(html: str, url: str) -> DirectResponseProductData | None:
    """Detects a known page-builder and extracts offer matrix + social proof
    via the shared generic parser. Returns None (fall through to Tier 5) if
    no builder is detected, or if a builder is detected but nothing usable
    was found within it."""
    if is_amazon_url(url):
        return _extract_via_amazon(html, url)

    builder = detect_builder(html)
    if builder is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    offer_matrix = _extract_offer_matrix(soup)
    social_proof = _extract_social_proof(soup)

    if not offer_matrix and social_proof.review_count is None and social_proof.rating_value is None:
        logger.warning("builder_fingerprint_extract_failed", url=url, builder=builder)
        return None

    logger.info("builder_fingerprint_detected", url=url, builder=builder)

    return DirectResponseProductData(
        page_metadata=PageMetadata(
            canonical_url=canonicalize_url(url),
            original_url=url,
            page_type=PageType.ADVERTORIAL_FUNNEL,
            content_hash=get_content_hash(html),
            builder_detected=builder,
        ),
        product_info=ProductInfo(title=_extract_product_title(soup)),
        social_proof=social_proof,
        offer_matrix=offer_matrix,
        extraction_tier=ExtractionTier.TIER_4_5_BUILDER,
    )
