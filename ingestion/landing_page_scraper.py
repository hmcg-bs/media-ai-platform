"""Stage 4a: Landing page HTML scraping and structured data extraction.

Scrapes product landing pages and extracts structured data (JSON-LD, OG tags, microdata)
for product categorization.
"""

from __future__ import annotations

import json

import requests
from structlog import get_logger

from ingestion.product_page import ProductPage

logger = get_logger(__name__)


def scrape_landing_page(url: str, timeout_s: int = 10) -> str | None:
    """Scrape HTML content from a landing page URL.

    Args:
        url: The product page URL to scrape.
        timeout_s: HTTP request timeout in seconds.

    Returns:
        HTML content as string, or None if scrape failed.
    """
    if not url or not url.startswith(("http://", "https://")):
        logger.warning("invalid_url", url=url)
        return None

    try:
        response = requests.get(url, timeout=timeout_s, allow_redirects=True)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.warning("scrape_failed", url=url, error=str(e))
        return None


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
