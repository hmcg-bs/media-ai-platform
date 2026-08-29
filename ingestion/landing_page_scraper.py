"""Stages 4a-4c: Landing page analysis.

4a: Scrape HTML + structure detection
4b: Extract structured data (JSON-LD, OG tags)
4c: LLM extraction for semantic fields (category, USP, branding, variants)
"""

from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

import httpx
from structlog import get_logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ingestion.product_page import ProductPage
from ingestion.product_page_analyzer import extract_semantic_fields
from ingestion.rate_limiter import DomainRateLimiterRegistry
from pipeline.config import get_settings

logger = get_logger(__name__)

# Retryable: 429/403 confirmed (empirically) to be part of the same
# burst-triggered block as each other on this project's corpus, not a
# permanent auth failure — deliberately different from
# pipeline/clients/replicate_client.py's _is_retryable, which treats 403 as
# non-retryable. Do not "fix" this back to match that convention.
_RETRY_STATUS = {429, 403, 500, 502, 503, 504}

_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None


def get_http_client() -> httpx.Client:
    """Lazily-built, module-level shared httpx.Client — safe to use across
    threads, gives connection pooling/keep-alive for free (fewer fresh
    TCP+TLS handshakes = faster and less bot-signal-y than a fresh
    `requests.get()` per call)."""
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                settings = get_settings()
                _shared_client = httpx.Client(
                    headers={
                        "User-Agent": settings.scrape_user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    follow_redirects=True,
                    timeout=settings.scrape_timeout_s,
                )
    return _shared_client


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


def _fetch(
    url: str,
    client: httpx.Client,
    rate_limiter: DomainRateLimiterRegistry | None,
) -> httpx.Response | None:
    """Shared retry-wrapped GET used by scrape_landing_page and
    resolve_and_scrape. Returns the response (post-redirects) or None."""
    settings = get_settings()
    domain = urlsplit(url).netloc

    @retry(
        stop=stop_after_attempt(settings.scrape_max_attempts),
        wait=wait_exponential(
            min=settings.scrape_backoff_min_seconds, max=settings.scrape_backoff_max_seconds
        ),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    def _do_request() -> httpx.Response:
        if rate_limiter:
            rate_limiter.acquire(domain)
        resp = client.get(url)
        resp.raise_for_status()
        return resp

    try:
        response = _do_request()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("scrape_failed", url=url, error=str(e))
        return None

    if rate_limiter:
        rate_limiter.record_response(domain, dict(response.headers), response.text[:2000])

    return response


def scrape_landing_page(
    url: str,
    timeout_s: int = 10,
    client: httpx.Client | None = None,
    rate_limiter: DomainRateLimiterRegistry | None = None,
) -> str | None:
    """Scrape HTML content from a landing page URL.

    Args:
        url: The product page URL to scrape.
        timeout_s: Unused when `client` is provided (the client carries its
            own timeout from settings); kept for backward compatibility.
        client: Shared httpx.Client to use (see get_http_client). Falls
            back to the module-level shared client if not given.
        rate_limiter: If given, acquired before every attempt (including
            retries) — pacing and backoff compose instead of racing.

    Returns:
        HTML content as string, or None if scrape failed.
    """
    if not url or not url.startswith(("http://", "https://")):
        logger.warning("invalid_url", url=url)
        return None

    response = _fetch(url, client or get_http_client(), rate_limiter)
    return response.text if response is not None else None


def resolve_and_scrape(
    url: str,
    client: httpx.Client | None = None,
    rate_limiter: DomainRateLimiterRegistry | None = None,
) -> tuple[str, str] | None:
    """Like scrape_landing_page, but also returns the final URL after
    redirects — needed by the tiered scraper to discover a /products/{handle}
    path that only appears post-redirect (common for click-tracker domains).

    Returns (final_url, html) or None if the fetch failed.
    """
    if not url or not url.startswith(("http://", "https://")):
        logger.warning("invalid_url", url=url)
        return None

    response = _fetch(url, client or get_http_client(), rate_limiter)
    if response is None:
        return None
    return str(response.url), response.text


def extract_json_ld(html: str) -> dict | None:
    """Extract JSON-LD structured data from HTML.

    Args:
        html: HTML content.

    Returns:
        Parsed JSON-LD object, or None if not found.
    """
    try:
        # Look for <script type="application/ld+json">
        start = html.find('<script type="application/ld+json">')
        if start == -1:
            return None

        start += len('<script type="application/ld+json">')
        end = html.find("</script>", start)
        if end == -1:
            return None

        json_str = html[start:end].strip()
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_og_tags(html: str) -> dict[str, str]:
    """Extract Open Graph meta tags from HTML.

    Args:
        html: HTML content.

    Returns:
        Dictionary of OG tag key-value pairs (e.g., {"og:title": "Product Name"}).
    """
    og_tags = {}
    try:
        # Simple regex to find meta tags with property="og:*"
        import re

        pattern = r'<meta\s+property="(og:[^"]+)"\s+content="([^"]*)"\s*/?>'
        matches = re.findall(pattern, html, re.IGNORECASE)
        for key, value in matches:
            og_tags[key.lower()] = value
    except Exception as e:
        logger.debug("og_extract_error", error=str(e))

    return og_tags


def extract_from_json_ld(json_ld: dict) -> dict[str, str | float | int | None]:
    """Extract product fields from JSON-LD schema.org data.

    Args:
        json_ld: Parsed JSON-LD object.

    Returns:
        Dictionary with extracted fields: {name, category, price, rating, etc.}
    """
    extracted = {}

    # Handle both single object and array of objects
    items = json_ld.get("@graph", [json_ld]) if json_ld else []
    if not isinstance(items, list):
        items = [items]

    # Find Product type
    product = None
    for item in items:
        if isinstance(item, dict):
            item_type = item.get("@type", "")
            if isinstance(item_type, list):
                item_type = item_type[0] if item_type else ""
            if item_type == "Product":
                product = item
                break

    if not product:
        return extracted

    # Extract fields
    extracted["product_name"] = product.get("name", "")
    extracted["product_category"] = (
        product.get("category", "") or product.get("productCategory", "")
    )

    # Price from Offer
    offer = product.get("offers")
    if offer:
        if isinstance(offer, dict):
            extracted["price"] = float(offer.get("price", 0)) or None
            extracted["price_currency"] = offer.get("priceCurrency", "USD")
        elif isinstance(offer, list) and offer:
            extracted["price"] = float(offer[0].get("price", 0)) or None
            extracted["price_currency"] = offer[0].get("priceCurrency", "USD")

    # Brand
    brand = product.get("brand", {})
    if isinstance(brand, dict):
        extracted["brand_name"] = brand.get("name", "")
    elif isinstance(brand, str):
        extracted["brand_name"] = brand

    # Rating
    rating = product.get("aggregateRating", {})
    if isinstance(rating, dict):
        extracted["rating"] = float(rating.get("ratingValue", 0)) or None
        extracted["rating_count"] = int(rating.get("reviewCount", 0)) or None

    # Description
    extracted["marketing_copy"] = product.get("description", "")

    return extracted


def extract_from_og_tags(og_tags: dict[str, str]) -> dict[str, str]:
    """Extract product fields from Open Graph meta tags.

    Args:
        og_tags: Dictionary of OG tags (from extract_og_tags).

    Returns:
        Dictionary with extracted fields.
    """
    extracted = {}

    extracted["product_name"] = og_tags.get("og:title", "")
    extracted["marketing_copy"] = og_tags.get("og:description", "")

    # Some e-commerce sites put price in og:price
    if "og:price" in og_tags:
        try:
            extracted["price"] = float(og_tags["og:price"])
        except ValueError:
            pass

    extracted["price_currency"] = og_tags.get("og:price:currency", "USD")

    return extracted


def extract_structured_data(url: str, html: str) -> ProductPage | None:
    """Extract product data from structured data in HTML (JSON-LD, OG tags).

    Args:
        url: The product page URL.
        html: HTML content.

    Returns:
        ProductPage with extracted fields, or None if extraction failed.
    """
    if not html:
        logger.warning("empty_html", url=url)
        return None

    extracted = {}

    # Try JSON-LD first (most reliable for e-commerce)
    json_ld = extract_json_ld(html)
    if json_ld:
        extracted.update(extract_from_json_ld(json_ld))

    # Fallback to OG tags
    og_tags = extract_og_tags(html)
    if og_tags and not extracted.get("product_name"):
        extracted.update(extract_from_og_tags(og_tags))

    if not extracted.get("product_name"):
        logger.warning("no_structured_data", url=url)
        return None

    # Build ProductPage
    product_page = ProductPage(
        product_name=extracted.get("product_name", ""),
        product_category=extracted.get("product_category", ""),
        brand_name=extracted.get("brand_name", ""),
        price=extracted.get("price"),
        price_currency=extracted.get("price_currency", "USD"),
        rating=extracted.get("rating"),
        rating_count=extracted.get("rating_count"),
        marketing_copy=extracted.get("marketing_copy", ""),
        extraction_method="structured_data",
        confidence=0.9,
        url=url,
    )

    return product_page


def extract_product_page(
    url: str,
    html: str,
    use_llm_enrichment: bool = True,
    ad_context: dict[str, str] | None = None,
) -> ProductPage | None:
    """Full pipeline: scrape + structured extract + optional LLM enrichment.

    Orchestrates Stages 4a-4c:
    - 4a: HTML scraping (already done upstream)
    - 4b: Structured data extraction (JSON-LD, OG tags)
    - 4c: LLM enrichment for semantic fields (category, USP, branding, variants) + ad context

    Args:
        url: The product page URL.
        html: HTML content.
        use_llm_enrichment: If True, call Gemini for semantic fields. Default True.
        ad_context: Optional ad marketing context: {"title": "...", "body": "...", "caption": "..."}

    Returns:
        Fully extracted ProductPage, or None if extraction failed.
    """
    # Stage 4b: Structured data extraction
    product = extract_structured_data(url, html)
    if not product:
        return None

    # Stage 4c: Optional LLM enrichment for semantic fields
    if use_llm_enrichment:
        enriched = extract_semantic_fields(html, product, ad_context=ad_context)
        if enriched:
            return enriched

    return product
