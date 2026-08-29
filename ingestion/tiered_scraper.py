"""Tier 1 (Shopify JSON API) / Tier 2 (hardened HTML scrape) composition.

Single entry point per unique landing-page URL, `scrape_and_extract()`,
designed to minimize redundant requests — at most 2 requests per URL:

1. If `/products/{handle}` is already literal in the URL: GET the .json
   endpoint directly (1 request). Success -> Tier 1 done. 404/non-JSON ->
   fall through to Tier 2 using a fresh GET of the original URL.
2. Else: GET the URL with redirects followed (1 request) — this both
   resolves the final path AND gives HTML we can reuse if Tier 1 doesn't
   apply. If the resolved path has `/products/{handle}`: GET the .json
   endpoint (1 more request, 2 total). Success -> Tier 1 done. Failure ->
   fall through to Tier 2 reusing the HTML already fetched (0 extra
   requests). If the resolved path has no `/products/`: go straight to
   Tier 2 reusing that same HTML (1 request total, same as the old scraper).
"""

from __future__ import annotations

import httpx

from ingestion.landing_page_scraper import (
    extract_product_page,
    resolve_and_scrape,
    scrape_landing_page,
)
from ingestion.product_page import ProductPage
from ingestion.product_page_analyzer import extract_semantic_fields_from_shopify_json
from ingestion.rate_limiter import DomainRateLimiterRegistry
from ingestion.shopify_json import (
    build_json_url,
    fetch_shopify_json,
    has_product_path,
    parse_shopify_product,
)
from pipeline.logger import get_logger

logger = get_logger(__name__)


def _tier1(
    product_url: str,
    client: httpx.Client,
    rate_limiter: DomainRateLimiterRegistry,
    use_llm: bool,
    ad_context: dict[str, str] | None,
) -> ProductPage | None:
    json_url = build_json_url(product_url)
    shopify_data = fetch_shopify_json(json_url, client, rate_limiter)
    if not shopify_data:
        return None

    base = parse_shopify_product(shopify_data, product_url)
    if not base:
        return None

    if use_llm:
        enriched = extract_semantic_fields_from_shopify_json(shopify_data, base, ad_context)
        if enriched:
            return enriched

    return base


def _tier2(
    url: str,
    html: str | None,
    use_llm: bool,
    ad_context: dict[str, str] | None,
) -> ProductPage | None:
    if not html:
        return None
    return extract_product_page(url, html, use_llm_enrichment=use_llm, ad_context=ad_context)


def scrape_and_extract(
    url: str,
    *,
    client: httpx.Client,
    rate_limiter: DomainRateLimiterRegistry,
    use_llm: bool = True,
    ad_context: dict[str, str] | None = None,
) -> ProductPage | None:
    """Single entry point per unique URL. See module docstring for the tier
    composition and request-count guarantees. Returns None only if both
    tiers fail (mirrors extract_product_page's existing None-on-failure
    contract)."""
    if has_product_path(url):
        result = _tier1(url, client, rate_limiter, use_llm, ad_context)
        if result:
            return result
        # Tier 1 failed (404 / non-Shopify / malformed) — no HTML fetched
        # yet in this branch, so do a fresh GET for Tier 2.
        html = scrape_landing_page(url, client=client, rate_limiter=rate_limiter)
        return _tier2(url, html, use_llm, ad_context)

    resolved = resolve_and_scrape(url, client=client, rate_limiter=rate_limiter)
    if resolved is None:
        return None
    final_url, html = resolved

    if has_product_path(final_url):
        result = _tier1(final_url, client, rate_limiter, use_llm, ad_context)
        if result:
            return result
        # Tier 1 failed — reuse the HTML already fetched, no extra request.
        return _tier2(final_url, html, use_llm, ad_context)

    # No product path even after following redirects — straight to Tier 2,
    # reusing the HTML from the resolve step (1 request total).
    return _tier2(final_url, html, use_llm, ad_context)
