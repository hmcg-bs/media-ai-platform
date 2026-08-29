"""Stage 4d: Optional enrichment utility.

Enriches a corpus of ads (ads.json) with ProductPage data by analyzing landing
pages linked in each ad.

Usage:
    python -m ingestion.enrich_with_product_pages \\
      --ads /path/to/ads.json \\
      --out /path/to/enriched_ads.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from ingestion.landing_page_scraper import extract_product_page, scrape_landing_page
from ingestion.models import CompetitorAd
from pipeline.logger import get_logger

logger = get_logger(__name__)


def enrich_corpus(ads_file: Path, output_file: Path, use_llm: bool = True) -> int:
    """Enrich ads corpus with landing page product analysis.

    Args:
        ads_file: Path to ads.json (array of CompetitorAd dicts).
        output_file: Path to write enriched ads.json.
        use_llm: If True, use LLM for semantic enrichment (Stage 4c).

    Returns:
        0 on success, 1 on error.
    """
    if not ads_file.exists():
        logger.error("enrich_file_not_found", path=str(ads_file))
        return 1

    try:
        with open(ads_file) as f:
            ads_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("enrich_invalid_json", path=str(ads_file), error=str(e))
        return 1

    if not isinstance(ads_data, list):
        logger.error("enrich_invalid_format", msg="ads.json must be an array")
        return 1

    enriched_ads = []
    for i, ad_dict in enumerate(ads_data, 1):
        try:
            ad = CompetitorAd.model_validate(ad_dict)
        except Exception as e:
            logger.warning("enrich_ad_invalid", index=i, error=str(e))
            enriched_ads.append(ad_dict)  # Keep original on validation error
            continue

        # Skip if no link_url
        if not ad.link_url:
            logger.debug("enrich_skipped_no_link", index=i, page=ad.page_name)
            enriched_ads.append(ad_dict)
            continue

        # Scrape landing page
        html = scrape_landing_page(ad.link_url, timeout_s=10)
        if not html:
            logger.warning(
                "enrich_scrape_failed",
                index=i,
                link_url=ad.link_url,
                page=ad.page_name,
            )
            enriched_ads.append(ad_dict)
            continue

        # Build ad context for Stage 4c (LLM enrichment)
        ad_context = None
        if use_llm and (ad.title or ad.body or ad.caption):
            ad_context = {
                "title": ad.title or "",
                "body": ad.body or "",
                "caption": ad.caption or "",
            }

        # Extract product page (with optional ad context for LLM enrichment)
        product_page = extract_product_page(
            ad.link_url, html, use_llm_enrichment=use_llm, ad_context=ad_context
        )
        if not product_page:
            logger.warning(
                "enrich_extraction_failed",
                index=i,
                link_url=ad.link_url,
                page=ad.page_name,
            )
            enriched_ads.append(ad_dict)
            continue

        # Enrich ad with product_page
        ad.product_page = product_page
        enriched_ads.append(ad.model_dump(mode="json"))
        logger.info(
            "enrich_success",
            index=i,
            product_name=product_page.product_name,
            confidence=product_page.confidence,
        )

    # Write enriched corpus
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(enriched_ads, f, indent=2, default=str)

    enriched_count = sum(
        1
        for ad in enriched_ads
        if isinstance(ad, dict) and ad.get("product_page") is not None
    )
    logger.info(
        "enrich_complete",
        input_count=len(ads_data),
        enriched_count=enriched_count,
        output_file=str(output_file),
    )

    return 0


def _enrich_one(i: int, ad_dict: dict, use_llm: bool) -> tuple[int, dict]:
    """Enrich a single ad; returns (original_index, result_dict). Never raises —
    on any failure the original ad_dict is returned unchanged (matches
    enrich_corpus's per-ad fallback behavior)."""
    try:
        ad = CompetitorAd.model_validate(ad_dict)
    except Exception as e:
        logger.warning("enrich_ad_invalid", index=i, error=str(e))
        return i, ad_dict

    if not ad.link_url:
        return i, ad_dict

    try:
        html = scrape_landing_page(ad.link_url, timeout_s=10)
        if not html:
            logger.warning("enrich_scrape_failed", index=i, link_url=ad.link_url, page=ad.page_name)
            return i, ad_dict

        ad_context = None
        if use_llm and (ad.title or ad.body or ad.caption):
            ad_context = {
                "title": ad.title or "",
                "body": ad.body or "",
                "caption": ad.caption or "",
            }

        product_page = extract_product_page(
            ad.link_url, html, use_llm_enrichment=use_llm, ad_context=ad_context
        )
        if not product_page:
            logger.warning("enrich_extraction_failed", index=i, link_url=ad.link_url, page=ad.page_name)
            return i, ad_dict

        ad.product_page = product_page
        return i, ad.model_dump(mode="json")
    except Exception as e:  # noqa: BLE001 — one bad ad shouldn't kill the batch
        logger.warning("enrich_ad_failed", index=i, link_url=ad.link_url, error=str(e))
        return i, ad_dict


def enrich_corpus_parallel(
    ads_file: Path,
    output_file: Path,
    use_llm: bool = True,
    max_workers: int = 10,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 20,
    resume: bool = False,
) -> int:
    """Parallel version of enrich_corpus for large corpora (thousands of ads).

    Each ad involves a network scrape (up to 10s timeout) plus an optional
    LLM call — at any real corpus size this is worth parallelizing the same
    way Phase 0's classifier was. Order-preserving (indexed, not
    append-order) so output stays aligned with input regardless of which
    worker finishes first. checkpoint_path/resume let an interrupted run
    pick back up (matched by ad_archive_id) instead of restarting.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not ads_file.exists():
        logger.error("enrich_file_not_found", path=str(ads_file))
        return 1

    try:
        with open(ads_file) as f:
            ads_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("enrich_invalid_json", path=str(ads_file), error=str(e))
        return 1

    if not isinstance(ads_data, list):
        logger.error("enrich_invalid_format", msg="ads.json must be an array")
        return 1

    already_enriched: dict[str, dict] = {}
    if resume and checkpoint_path and checkpoint_path.exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        already_enriched = {
            a["ad_archive_id"]: a
            for a in prior
            if isinstance(a, dict) and a.get("ad_archive_id") and a.get("product_page") is not None
        }
        print(f"Resuming: {len(already_enriched)} ads already enriched")

    results: list[dict | None] = [None] * len(ads_data)
    lock = threading.Lock()
    completed = 0

    def checkpoint_locked() -> None:
        if checkpoint_path and (completed % checkpoint_every == 0):
            with open(checkpoint_path, "w") as f:
                json.dump([r for r in results if r is not None], f, indent=2, default=str)

    def task(i: int, ad_dict: dict) -> tuple[int, dict]:
        archive_id = ad_dict.get("ad_archive_id")
        if archive_id and archive_id in already_enriched:
            return i, already_enriched[archive_id]
        return _enrich_one(i, ad_dict, use_llm)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(task, i, ad): i for i, ad in enumerate(ads_data)}
        for fut in as_completed(futures):
            idx, result = fut.result()
            with lock:
                results[idx] = result
                completed += 1
                print(f"  Enriched {completed}/{len(ads_data)}...", end="\r")
                checkpoint_locked()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    enriched_count = sum(
        1 for ad in results if isinstance(ad, dict) and ad.get("product_page") is not None
    )
    print(f"\n✅ Enrich complete: {enriched_count}/{len(ads_data)} ads got product_page")
    logger.info(
        "enrich_complete",
        input_count=len(ads_data),
        enriched_count=enriched_count,
        output_file=str(output_file),
    )

    return 0


def enrich_corpus_parallel_tiered(
    ads_file: Path,
    output_file: Path,
    use_llm: bool = True,
    max_workers: int = 10,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 20,
    resume: bool = False,
) -> int:
    """Tiered (Shopify JSON API + hardened HTML fallback) + rate-limited +
    URL-deduped version of enrich_corpus_parallel.

    Built after enrich_corpus_parallel's naive per-request scraping tripped
    Shopify's shared platform-level rate limit at scale (2,736 requests, 15
    workers -> 66% failure). Two things fix that here: (1) a shared
    DomainRateLimiterRegistry (see ingestion/rate_limiter.py) that every
    worker acquires through before each request, and (2) URL-level dedup —
    this corpus's 2,736 ads collapse to under 1,000 unique link_urls, so
    scheduling one task per unique URL (not per ad) and fanning the result
    out to every ad sharing it eliminates most of the redundant fetching
    before rate limiting even matters.

    checkpoint_path/resume work the same as enrich_corpus_parallel (matched
    by ad_archive_id), and additionally seed the URL-dedup cache — so ads
    that share a link_url with an already-enriched ad are free too, even if
    the ad itself wasn't in a prior successful run.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ingestion.landing_page_scraper import get_http_client
    from ingestion.product_page import ProductPage
    from ingestion.rate_limiter import get_rate_limiter_registry
    from ingestion.tiered_scraper import scrape_and_extract

    if not ads_file.exists():
        logger.error("enrich_file_not_found", path=str(ads_file))
        return 1

    try:
        with open(ads_file) as f:
            ads_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("enrich_invalid_json", path=str(ads_file), error=str(e))
        return 1

    if not isinstance(ads_data, list):
        logger.error("enrich_invalid_format", msg="ads.json must be an array")
        return 1

    already_enriched: dict[str, dict] = {}
    if resume and checkpoint_path and checkpoint_path.exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        already_enriched = {
            a["ad_archive_id"]: a
            for a in prior
            if isinstance(a, dict) and a.get("ad_archive_id") and a.get("product_page") is not None
        }
        print(f"Resuming: {len(already_enriched)} ads already enriched")

    # Seed the URL cache from already-enriched ads so any other ad sharing
    # that URL is free too, even if that specific ad wasn't in the prior run.
    url_cache: dict[str, dict | None] = {}
    for a in already_enriched.values():
        link_url = a.get("link_url")
        if link_url and a.get("product_page"):
            url_cache[link_url] = a["product_page"]

    results: list[dict | None] = [None] * len(ads_data)
    lock = threading.Lock()
    completed = 0

    def checkpoint_locked() -> None:
        if checkpoint_path and (completed % checkpoint_every == 0):
            with open(checkpoint_path, "w") as f:
                json.dump([r for r in results if r is not None], f, indent=2, default=str)

    # Validate all ads up front and bucket indices by link_url.
    validated: dict[int, CompetitorAd] = {}
    url_to_indices: dict[str, list[int]] = {}
    url_to_ad_context: dict[str, dict[str, str]] = {}

    for i, ad_dict in enumerate(ads_data):
        archive_id = ad_dict.get("ad_archive_id")
        if archive_id and archive_id in already_enriched:
            results[i] = already_enriched[archive_id]
            completed += 1
            continue

        try:
            ad = CompetitorAd.model_validate(ad_dict)
        except Exception as e:
            logger.warning("enrich_ad_invalid", index=i, error=str(e))
            results[i] = ad_dict
            completed += 1
            continue

        if not ad.link_url:
            results[i] = ad_dict
            completed += 1
            continue

        validated[i] = ad
        url_to_indices.setdefault(ad.link_url, []).append(i)
        if ad.link_url not in url_to_ad_context and use_llm and (ad.title or ad.body or ad.caption):
            url_to_ad_context[ad.link_url] = {
                "title": ad.title or "",
                "body": ad.body or "",
                "caption": ad.caption or "",
            }

    client = get_http_client()
    rate_limiter = get_rate_limiter_registry()

    def fan_out(link_url: str, product_page_dict: dict | None) -> None:
        nonlocal completed
        for idx in url_to_indices[link_url]:
            ad = validated[idx]
            if product_page_dict is not None:
                ad.product_page = ProductPage.model_validate(product_page_dict)
            results[idx] = ad.model_dump(mode="json")
            with lock:
                completed += 1
                print(f"  Enriched {completed}/{len(ads_data)}...", end="\r")
                checkpoint_locked()

    def task(link_url: str) -> tuple[str, dict | None]:
        try:
            product_page = scrape_and_extract(
                link_url,
                client=client,
                rate_limiter=rate_limiter,
                use_llm=use_llm,
                ad_context=url_to_ad_context.get(link_url),
            )
        except Exception as e:  # noqa: BLE001 — one bad URL shouldn't kill the batch
            logger.warning("enrich_tiered_url_failed", link_url=link_url, error=str(e))
            return link_url, None
        return link_url, product_page.model_dump(mode="json") if product_page else None

    # URLs already resolved via the resume cache: fan out immediately, no request.
    for u in list(url_to_indices):
        if u in url_cache:
            fan_out(u, url_cache[u])

    fresh_urls = [u for u in url_to_indices if u not in url_cache]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(task, u): u for u in fresh_urls}
        for fut in as_completed(futures):
            link_url, product_page_dict = fut.result()
            fan_out(link_url, product_page_dict)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    enriched_count = sum(
        1 for ad in results if isinstance(ad, dict) and ad.get("product_page") is not None
    )
    print(
        f"\n✅ Enrich complete (tiered): {enriched_count}/{len(ads_data)} ads got product_page "
        f"({len(url_to_indices)} unique URLs, {len(fresh_urls)} freshly fetched)"
    )
    logger.info(
        "enrich_complete_tiered",
        input_count=len(ads_data),
        enriched_count=enriched_count,
        unique_urls=len(url_to_indices),
        fresh_urls=len(fresh_urls),
        output_file=str(output_file),
    )

    return 0


def enrich_corpus_zenrows(
    ads_file: Path,
    output_file: Path,
    checkpoint_path: Path | None = None,
    checkpoint_every_urls: int = 50,
    resume: bool = False,
    diagnostics_csv: Path | None = None,
) -> int:
    """ZenRows-based enrichment: backfills price/description/variants/rating/
    rating_count for every unique link_url in the corpus (see
    ingestion/zenrows_scraper.py — 4-tier parsing cascade, JS-rendered,
    server-side anti-bot handling). Reuses the same URL-dedup + fan-out
    scaffolding as enrich_corpus_parallel_tiered, but merges into whatever
    product_page each ad already has (from a prior tiered run) rather than
    discarding it — ZenRows only fills the 5 fields it's responsible for.

    Processes unique URLs in chunks (checkpoint_every_urls), checkpointing
    after each chunk — a single ZenRowsClient/thread-pool is reused across
    chunks (each chunk is its own asyncio.run(), but the client itself is
    plain, not event-loop-bound, so sharing it across event loops is safe).
    """
    from ingestion.product_page import ProductPage
    from ingestion.zenrows_scraper import (
        run_zenrows_batch_sync,
        to_product_page_updates,
        write_diagnostics,
    )

    if not ads_file.exists():
        logger.error("enrich_file_not_found", path=str(ads_file))
        return 1

    try:
        with open(ads_file) as f:
            ads_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("enrich_invalid_json", path=str(ads_file), error=str(e))
        return 1

    if not isinstance(ads_data, list):
        logger.error("enrich_invalid_format", msg="ads.json must be an array")
        return 1

    already_done_urls: set[str] = set()
    if resume and checkpoint_path and checkpoint_path.exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        for a in prior:
            if not isinstance(a, dict):
                continue
            pp = a.get("product_page")
            method = (pp or {}).get("extraction_method", "") if pp else ""
            if a.get("link_url") and "zenrows" in method:
                already_done_urls.add(a["link_url"])
        print(f"Resuming: {len(already_done_urls)} URLs already zenrows-enriched")
        ads_data = prior  # carry forward prior run's product_page state as the base

    results: list[dict] = list(ads_data)
    url_to_indices: dict[str, list[int]] = {}
    url_existing_page: dict[str, dict | None] = {}

    for i, ad_dict in enumerate(ads_data):
        link_url = ad_dict.get("link_url")
        if not link_url:
            continue
        url_to_indices.setdefault(link_url, []).append(i)
        if link_url not in url_existing_page:
            url_existing_page[link_url] = ad_dict.get("product_page")

    target_urls = [u for u in url_to_indices if u not in already_done_urls]
    print(f"Total unique URLs: {len(url_to_indices)}, targeting {len(target_urls)} via ZenRows")

    all_fetch_results: dict = {}
    zenrows_client = None
    processed = 0

    for chunk_start in range(0, len(target_urls), checkpoint_every_urls):
        chunk = target_urls[chunk_start : chunk_start + checkpoint_every_urls]
        if zenrows_client is None:
            from zenrows import ZenRowsClient

            from pipeline.config import get_settings

            settings = get_settings()
            zenrows_client = ZenRowsClient(
                settings.zenrows_api_key,
                retries=settings.zenrows_retries,
                concurrency=settings.zenrows_concurrency,
            )

        chunk_results = run_zenrows_batch_sync(chunk, client=zenrows_client)
        all_fetch_results.update(chunk_results)

        for url, fetch_result in chunk_results.items():
            existing_dict = url_existing_page.get(url)
            existing_page = ProductPage.model_validate(existing_dict) if existing_dict else None

            if fetch_result.success and fetch_result.data:
                merged = to_product_page_updates(fetch_result.data, existing_page, url)
            else:
                merged = existing_page

            for idx in url_to_indices[url]:
                if merged is not None:
                    results[idx]["product_page"] = merged.model_dump(mode="json")

        processed += len(chunk)
        print(f"  ZenRows processed {processed}/{len(target_urls)} URLs...", end="\r")

        if checkpoint_path:
            with open(checkpoint_path, "w") as f:
                json.dump(results, f, indent=2, default=str)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    if diagnostics_csv and all_fetch_results:
        write_diagnostics(all_fetch_results, diagnostics_csv)

    enriched_count = sum(1 for r in results if isinstance(r, dict) and r.get("product_page"))
    print(f"\n✅ ZenRows enrich complete: {enriched_count}/{len(results)} ads have product_page")
    logger.info(
        "enrich_complete_zenrows",
        input_count=len(ads_data),
        enriched_count=enriched_count,
        unique_urls=len(url_to_indices),
        zenrows_targeted=len(target_urls),
        output_file=str(output_file),
    )

    return 0


_MERGE_FILLABLE_FIELDS = (
    "product_name",
    "product_category",
    "product_subcategory",
    "brand_name",
    "price",
    "price_currency",
    "price_range",
    "rating",
    "rating_count",
    "marketing_copy",
    "usp",
    "cultural_branding",
    "variants_featured",
)


def _merge_product_pages(base: Any, other: Any, label: str = "dedup_merged") -> Any:
    """setdefault-style merge: base's own fields win whenever populated;
    other only fills gaps base left empty.

    Used for near-duplicate dedup hits in enrich_corpus_advertorial_fallback,
    which previously replaced base outright with the matched page —
    discarding real data (product_category/brand_name/variants_featured,
    etc.) an earlier tier had already found for this exact ad's own URL.
    Confirmed live as real data loss in production: 22 fields across 4 ads
    regressed in the Phase 0.5f Pass B run before this fix, all near-dup
    hits whose matched page happened to be thinner than the ad's own
    pre-existing product_page. Also reused for the free Shopify-JSON Tier-1
    pre-pass (label="shopify_json_backfill") — same merge semantics, just a
    different provenance suffix so extraction_method stays accurate.
    """
    if base is None:
        return other
    if other is None:
        return base

    updates: dict[str, Any] = {}
    for key in _MERGE_FILLABLE_FIELDS:
        base_value = getattr(base, key)
        other_value = getattr(other, key)
        if not base_value and other_value:
            updates[key] = other_value
    if other.shows_all_variants and not base.shows_all_variants:
        updates["shows_all_variants"] = True

    if not updates:
        return base

    updates["extraction_method"] = (
        f"{base.extraction_method}+{label}" if base.extraction_method else other.extraction_method
    )
    return base.model_copy(update=updates)


def enrich_corpus_advertorial_fallback(
    ads_file: Path,
    output_file: Path,
    diagnostics_csv: Path,
    checkpoint_path: Path | None = None,
    checkpoint_every_urls: int = 50,
    resume: bool = False,
    near_duplicate_threshold: float = 0.95,
) -> int:
    """Phase 0.5e: targets exactly the URLs Phase 0.5d's --zenrows diagnostics
    CSV flagged as success=False (the advertorial-funnel pages Tiers 1-4
    couldn't parse — confirmed live to be custom page-builder/theme pages
    with real content but zero Schema.org/OpenGraph markup) and runs Tier
    4.5 (builder fingerprint, free) + Tier 5 (zone-pruned LLM fallback)
    against them via ingestion/zenrows_scraper.py's extract_product_data(...,
    enable_llm_fallback=True).

    Content-hash + MinHash near-duplicate dedup (ingestion/dedupe.py) runs
    *after* the fetch but *before* any LLM call: if a URL's fetched HTML is
    a near-duplicate of an already-processed page's HTML *in this run*, its
    result is copied instead of paying for another LLM call — this is where
    the real savings against the 4.0-ads-per-failed-URL duplication skew
    comes from, since it catches duplicate content even when canonical URLs
    differ (e.g. distinct tracker-path UUIDs on the same underlying page).
    """
    import httpx
    import pandas as pd

    from ingestion.dedupe import compute_minhash
    from ingestion.product_page import ProductPage
    from ingestion.rate_limiter import get_rate_limiter_registry
    from ingestion.shopify_json import (
        build_json_url,
        fetch_shopify_json,
        has_product_path,
        parse_shopify_product,
    )
    from ingestion.zenrows_scraper import (
        batch_scrape_zenrows,
        extract_product_data,
        to_product_page_updates,
    )

    if not ads_file.exists():
        logger.error("enrich_file_not_found", path=str(ads_file))
        return 1
    if not diagnostics_csv.exists():
        logger.error("enrich_diagnostics_not_found", path=str(diagnostics_csv))
        return 1

    try:
        with open(ads_file) as f:
            ads_data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("enrich_invalid_json", path=str(ads_file), error=str(e))
        return 1
    if not isinstance(ads_data, list):
        logger.error("enrich_invalid_format", msg="ads.json must be an array")
        return 1

    diag = pd.read_csv(diagnostics_csv)
    # success==False alone under-targets: a row can be marked successful off
    # a description/rating match alone while price was never resolved (see
    # zenrows_scraper.py::_has_min_fields docstring) — those rows never got
    # a price-specific fallback attempt in the original run, and skipping
    # them here would leave that gap permanently unaddressed.
    price_missing = (
        diag["product_price"].isna() if "product_price" in diag.columns else False
    )
    failed_urls = set(
        diag.loc[(diag["success"] == False) | price_missing, "url"].dropna().tolist()  # noqa: E712
    )
    print(f"Diagnostics: {len(failed_urls)} previously-unresolved-or-price-missing URLs to target")

    already_done_urls: set[str] = set()
    if resume and checkpoint_path and checkpoint_path.exists():
        with open(checkpoint_path) as f:
            prior = json.load(f)
        for a in prior:
            if not isinstance(a, dict):
                continue
            method = ((a.get("product_page") or {}).get("extraction_method")) or ""
            if a.get("link_url") and ("tier_4_5_builder" in method or "tier_5_llm" in method):
                already_done_urls.add(a["link_url"])
        print(f"Resuming: {len(already_done_urls)} URLs already advertorial-enriched")
        ads_data = prior

    results: list[dict] = list(ads_data)
    url_to_indices: dict[str, list[int]] = {}
    url_existing_page: dict[str, dict | None] = {}
    for i, ad_dict in enumerate(ads_data):
        link_url = ad_dict.get("link_url")
        if not link_url:
            continue
        url_to_indices.setdefault(link_url, []).append(i)
        if link_url not in url_existing_page:
            url_existing_page[link_url] = ad_dict.get("product_page")

    target_urls = [u for u in url_to_indices if u in failed_urls and u not in already_done_urls]
    print(f"Targeting {len(target_urls)} URLs via Tier 4.5/5")

    # Free pre-pass: this function only ever ran the ZenRows cascade
    # (Tiers 1-4 = XHR/JSON-LD/window-objects/DOM, an entirely separate
    # numbering from tiered_scraper.py's Tier 1 = Shopify JSON API) — it
    # never called shopify_json.py at all. Any URL with a literal
    # /products/{handle} path gets Shopify's own product JSON for free (no
    # ZenRows credit spent), which is more reliable for price/variants than
    # anything scraped-HTML-based. Confirmed live (continued extraction-gap
    # sampling): rituallabs.shop/products/happy-liver13 (220 ads) had
    # extraction_method "tier_5_llm+tier_5_llm+tier_5_llm+tier_5_llm" —
    # reprocessed 4 separate times via this exact function, never once via
    # Tier 1, despite Shopify's own .json endpoint trivially returning a
    # real price (Tier 1 was never even attempted, not attempted-and-failed).
    # setdefault-merged into existing_page *before* the ZenRows cascade
    # decides whether to escalate to the paid LLM step, so a URL Tier 1 can
    # already resolve price for won't pay for an LLM call it no longer needs.
    shopify_json_recovered = 0
    http_client = httpx.Client(
        timeout=15.0, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
    )
    rate_limiter = get_rate_limiter_registry()
    try:
        for url in target_urls:
            if not has_product_path(url):
                continue
            shopify_data = fetch_shopify_json(build_json_url(url), http_client, rate_limiter)
            if not shopify_data:
                continue
            tier1_page = parse_shopify_product(shopify_data, url)
            if tier1_page is None:
                continue
            existing_dict = url_existing_page.get(url)
            existing_page = ProductPage.model_validate(existing_dict) if existing_dict else None
            merged = _merge_product_pages(existing_page, tier1_page, label="shopify_json_backfill")
            url_existing_page[url] = merged.model_dump(mode="json") if merged else None
            shopify_json_recovered += 1
    finally:
        http_client.close()
    print(
        f"Free Shopify-JSON pre-pass: {shopify_json_recovered}/{len(target_urls)} "
        "URLs recovered data"
    )

    # (MinHash signature, resulting ProductPage) for pages already processed
    # *in this run* — a near-duplicate hit copies the prior result rather
    # than paying for another LLM call.
    seen_signatures: list[tuple[Any, ProductPage | None]] = []
    processed = 0
    near_dup_hits = 0
    zenrows_client = None

    for chunk_start in range(0, len(target_urls), checkpoint_every_urls):
        chunk = target_urls[chunk_start : chunk_start + checkpoint_every_urls]
        if zenrows_client is None:
            from zenrows import ZenRowsClient

            from pipeline.config import get_settings

            settings = get_settings()
            zenrows_client = ZenRowsClient(
                settings.zenrows_api_key,
                retries=settings.zenrows_retries,
                concurrency=settings.zenrows_concurrency,
            )

        fetch_results = asyncio.run(
            batch_scrape_zenrows(chunk, client=zenrows_client, enable_llm_fallback=False)
        )

        for url in chunk:
            fetch_result = fetch_results.get(url)
            existing_dict = url_existing_page.get(url)
            existing_page = ProductPage.model_validate(existing_dict) if existing_dict else None
            merged_page = existing_page

            if fetch_result and fetch_result.html:
                minhash = compute_minhash(fetch_result.html)
                dup_match = next(
                    (
                        page
                        for sig, page in seen_signatures
                        if minhash.jaccard(sig) >= near_duplicate_threshold
                    ),
                    None,
                )

                if dup_match is not None:
                    near_dup_hits += 1
                    logger.info("dedupe_near_duplicate_hit", url=url)
                    merged_page = _merge_product_pages(existing_page, dup_match)
                elif fetch_result.success and fetch_result.data and fetch_result.data.product_price:
                    merged_page = to_product_page_updates(fetch_result.data, existing_page, url)
                else:
                    # Escalate to the LLM-enabled re-run whenever price is
                    # still missing, not just when the cheap-tiers pass found
                    # nothing at all — matching the same price-vs-other-fields
                    # split as zenrows_scraper.py::_has_min_fields. A page
                    # that resolved a description/rating but no price would
                    # otherwise never get a price-specific LLM attempt here,
                    # reusing the HTML already fetched (no extra request).
                    data = extract_product_data(
                        fetch_result.html, url=url, enable_llm_fallback=True
                    )
                    merged_page = to_product_page_updates(data, existing_page, url)

                seen_signatures.append((minhash, merged_page))

            for idx in url_to_indices[url]:
                if merged_page is not None:
                    results[idx]["product_page"] = merged_page.model_dump(mode="json")

        processed += len(chunk)
        print(
            f"  Advertorial-fallback processed {processed}/{len(target_urls)} URLs "
            f"({near_dup_hits} near-dup hits)...",
            end="\r",
        )

        if checkpoint_path:
            with open(checkpoint_path, "w") as f:
                json.dump(results, f, indent=2, default=str)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    enriched_count = sum(1 for r in results if isinstance(r, dict) and r.get("product_page"))
    print(
        f"\n✅ Advertorial fallback complete: {enriched_count}/{len(results)} ads have "
        f"product_page ({near_dup_hits} near-dup hits saved an LLM call)"
    )
    logger.info(
        "enrich_complete_advertorial_fallback",
        input_count=len(ads_data),
        enriched_count=enriched_count,
        targeted_urls=len(target_urls),
        near_dup_hits=near_dup_hits,
        output_file=str(output_file),
    )

    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Enrich ads corpus with landing page product analysis (Stage 4)."
    )
    parser.add_argument(
        "--ads",
        type=str,
        required=True,
        help="Path to ads.json corpus to enrich.",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output path for enriched ads.json.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM enrichment (Stage 4c); use structured data only.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent workers. 1 (default) uses the original serial path; "
        ">1 uses enrich_corpus_parallel — recommended for corpora of "
        "hundreds+ ads.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from --out if it exists (parallel mode only, matched by ad_archive_id).",
    )
    parser.add_argument(
        "--tiered",
        action="store_true",
        help="Use the tiered scraper (Shopify .json fast path + rate-limited hardened "
        "HTML fallback + URL-level dedup). Only meaningful with --workers > 1. "
        "Opt-in until validated at scale; recommended for any corpus of hundreds+ ads "
        "linking to real e-commerce sites — the plain --workers path has no rate "
        "limiting and will get blocked (see ingestion/rate_limiter.py docstring).",
    )
    parser.add_argument(
        "--zenrows",
        action="store_true",
        help="Use ZenRows (managed scraping API — JS rendering + proxy + anti-bot "
        "server-side) to backfill price/description/variants/rating/rating_count for "
        "every unique link_url. Merges into whatever product_page each ad already has "
        "(e.g. from a prior --tiered run) rather than replacing it. Requires "
        "ZENROWS_API_KEY in .env. Independent of --tiered/--workers.",
    )
    parser.add_argument(
        "--diagnostics-csv",
        type=str,
        default=None,
        help="With --zenrows: path to WRITE a per-URL diagnostics CSV. "
        "With --advertorial-fallback: path to READ a prior --zenrows diagnostics CSV from "
        "(its success=False rows become the target URL set).",
    )
    parser.add_argument(
        "--advertorial-fallback",
        action="store_true",
        help="Phase 0.5e: run Tier 4.5 (builder fingerprint) + Tier 5 (zone-pruned LLM "
        "fallback, real API cost) against exactly the URLs a prior --zenrows run's "
        "--diagnostics-csv flagged as success=False — custom advertorial-funnel pages "
        "with no Schema.org markup. Requires --diagnostics-csv (input). Independent of "
        "--tiered/--workers; mutually exclusive with --zenrows.",
    )

    args = parser.parse_args()
    if args.advertorial_fallback:
        if not args.diagnostics_csv:
            parser.error(
                "--advertorial-fallback requires --diagnostics-csv "
                "(the prior --zenrows run's diagnostics CSV to read failed URLs from)"
            )
        return enrich_corpus_advertorial_fallback(
            Path(args.ads),
            Path(args.out),
            diagnostics_csv=Path(args.diagnostics_csv),
            checkpoint_path=Path(args.out),
            resume=args.resume,
        )
    if args.zenrows:
        return enrich_corpus_zenrows(
            Path(args.ads),
            Path(args.out),
            checkpoint_path=Path(args.out),
            resume=args.resume,
            diagnostics_csv=Path(args.diagnostics_csv) if args.diagnostics_csv else None,
        )
    if args.workers > 1 and args.tiered:
        return enrich_corpus_parallel_tiered(
            Path(args.ads),
            Path(args.out),
            use_llm=not args.no_llm,
            max_workers=args.workers,
            checkpoint_path=Path(args.out),
            resume=args.resume,
        )
    if args.workers > 1:
        return enrich_corpus_parallel(
            Path(args.ads),
            Path(args.out),
            use_llm=not args.no_llm,
            max_workers=args.workers,
            checkpoint_path=Path(args.out),
            resume=args.resume,
        )
    return enrich_corpus(Path(args.ads), Path(args.out), use_llm=not args.no_llm)


if __name__ == "__main__":
    exit(main())
