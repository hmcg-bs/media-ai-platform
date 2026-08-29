"""Tier 1 scraping: Shopify's `.json` product-page convention.

Appending `.json` to a Shopify product URL (`https://store.com/products/
{handle}` -> `https://store.com/products/{handle}.json`) returns the full
structured product payload directly — no HTML scraping or LLM call needed.
Confirmed empirically against 15 real ad-linked URLs from this project's
corpus: 13/15 (87%) succeeded. No WooCommerce found anywhere in the top 60
domains by ad volume in this corpus — Shopify dominates the supplements
ad-landing-page landscape, making this a high-leverage shortcut.

`product_type` is flagged lower-trust: some stores stuff A/B-test bucket
names in it (e.g. "Intelligems Testing v2"); `tags` is generally a better
category signal.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ingestion.product_page import ProductPage
from ingestion.rate_limiter import DomainRateLimiterRegistry
from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)

_PRODUCT_PATH_RE = re.compile(r"/products/([^/?#]+)")

# Retry transient failures; 404 means "this store doesn't expose the classic
# .json endpoint" — a permanent-for-this-run signal, not a transient one.
_RETRY_STATUS = {429, 403, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


def has_product_path(url: str) -> bool:
    """True if `url` already contains a literal /products/{handle} path."""
    return bool(_PRODUCT_PATH_RE.search(urlsplit(url).path))


def build_json_url(product_url: str) -> str:
    """https://store.com/products/handle?variant=1 -> https://store.com/products/handle.json

    Strips query string and fragment; ensures no trailing slash before
    appending .json.
    """
    parts = urlsplit(product_url)
    path = parts.path.rstrip("/")
    if not path.endswith(".json"):
        path = f"{path}.json"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def fetch_shopify_json(
    json_url: str,
    client: httpx.Client,
    rate_limiter: DomainRateLimiterRegistry,
) -> dict | None:
    """GET json_url with retry/backoff; returns the parsed dict (with a
    top-level "product" key) or None if this store doesn't expose the
    endpoint (404) or returns something we can't parse as a Shopify product.
    """
    settings = get_settings()
    domain = urlsplit(json_url).netloc

    @retry(
        stop=stop_after_attempt(settings.scrape_max_attempts),
        wait=wait_exponential(
            min=settings.scrape_backoff_min_seconds, max=settings.scrape_backoff_max_seconds
        ),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    def _do_request() -> httpx.Response:
        rate_limiter.acquire(domain)
        resp = client.get(json_url)
        if resp.status_code == 404:
            return resp  # not retryable; handled below
        resp.raise_for_status()
        return resp

    try:
        resp = _do_request()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("shopify_json_failed", url=json_url, error=str(e))
        return None

    if resp.status_code == 404:
        logger.debug("shopify_json_not_found", url=json_url)
        return None

    rate_limiter.record_response(domain, dict(resp.headers), resp.text[:2000])

    try:
        data = resp.json()
    except ValueError:
        logger.debug("shopify_json_not_json", url=json_url)
        return None

    if not isinstance(data, dict) or "product" not in data:
        logger.debug("shopify_json_unexpected_shape", url=json_url)
        return None

    return data


def parse_shopify_product(data: dict, url: str) -> ProductPage | None:
    """Map a Shopify product JSON payload into ProductPage."""
    product = data.get("product")
    if not isinstance(product, dict) or not product.get("title"):
        return None

    tags_raw = product.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    variants = product.get("variants") or []
    prices: list[float] = []
    for v in variants:
        if not isinstance(v, dict):
            continue
        try:
            prices.append(float(v["price"]))
        except (KeyError, TypeError, ValueError):
            continue

    price = min(prices) if prices else None
    price_range = f"${min(prices):.2f}-${max(prices):.2f}" if len(prices) > 1 else ""

    body_html = product.get("body_html") or ""
    marketing_copy = re.sub(r"<[^>]+>", " ", body_html)
    marketing_copy = re.sub(r"\s+", " ", marketing_copy).strip()[:1000]

    return ProductPage(
        product_name=product.get("title", ""),
        product_subcategory=product.get("product_type", ""),  # lower-trust, see module docstring
        brand_name=product.get("vendor", ""),
        price=price,
        price_currency="USD",
        price_range=price_range,
        marketing_copy=marketing_copy,
        cultural_branding=tags,  # raw tags; category signal, refined by LLM enrichment if used
        variants_featured=[f"Variant: {v.get('title')}" for v in variants if isinstance(v, dict) and v.get("title") and v.get("title") != "Default Title"],
        shows_all_variants=len(variants) > 1,
        extraction_method="shopify_json",
        confidence=0.9,
        url=url,
    )
