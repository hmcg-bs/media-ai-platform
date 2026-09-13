"""Coverage and provenance gate for landing-page product enrichment."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from typing import Any

REPORT_SCHEMA_VERSION = "product-enrichment-coverage-v1"
MIN_LINKED_COVERAGE = {
    "product_page": 0.50,
    "price": 0.40,
    "description": 0.25,
}
MIN_FEATURE_ROWS = 100
MIN_FEATURE_ADVERTISERS = 10


def _content_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def build_product_enrichment_report(ads: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure usable product evidence; never turn missing extraction into zero."""
    linked = [ad for ad in ads if ad.get("link_url")]
    selectors: dict[str, Callable[[dict[str, Any]], bool]] = {
        "product_page": lambda page: bool(page),
        "price": lambda page: _present(page.get("price")),
        "description": lambda page: _present(page.get("marketing_copy"))
        or _present(page.get("usp")),
        "rating": lambda page: _present(page.get("rating")),
        "rating_count": lambda page: _present(page.get("rating_count")),
        "variants": lambda page: bool(page.get("variants_featured")),
    }
    fields: dict[str, Any] = {}
    for name, selector in selectors.items():
        matching = [ad for ad in linked if selector(ad.get("product_page") or {})]
        advertisers = {str(ad.get("page_id") or "") for ad in matching if ad.get("page_id")}
        coverage = len(matching) / len(linked) if linked else 0.0
        fields[name] = {
            "n_rows": len(matching),
            "n_advertisers": len(advertisers),
            "linked_ad_coverage": round(coverage, 5),
            "model_support": (
                len(matching) >= MIN_FEATURE_ROWS
                and len(advertisers) >= MIN_FEATURE_ADVERTISERS
            ),
        }

    failures = []
    if not linked:
        failures.append("no taxonomy-approved ads have landing-page URLs")
    for name, minimum in MIN_LINKED_COVERAGE.items():
        actual = fields[name]["linked_ad_coverage"]
        required_rows = math.ceil(len(linked) * minimum)
        if actual < minimum:
            failures.append(
                f"{name} coverage {actual:.3f} is below fixed {minimum:.3f} "
                f"({fields[name]['n_rows']}/{required_rows} required rows)"
            )
    methods: dict[str, int] = {}
    for ad in linked:
        method = str((ad.get("product_page") or {}).get("extraction_method") or "missing")
        methods[method] = methods.get(method, 0) + 1
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "passed" if not failures else "failed",
        "policy": {
            "minimum_linked_coverage": MIN_LINKED_COVERAGE,
            "minimum_feature_rows": MIN_FEATURE_ROWS,
            "minimum_feature_advertisers": MIN_FEATURE_ADVERTISERS,
        },
        "ads_content_sha256": _content_sha256(ads),
        "n_ads": len(ads),
        "n_linked_ads": len(linked),
        "n_unique_link_urls": len({str(ad["link_url"]) for ad in linked}),
        "fields": fields,
        "extraction_methods": dict(sorted(methods.items())),
        "failures": failures,
    }
