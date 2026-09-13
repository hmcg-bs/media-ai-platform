"""Build the final ML feature matrix from the ingestion corpus + Step 2
creative output — the last item on the Phase 2 Bridge checklist (see the
wayfinder map at GitHub issue #1).

Reuses ingestion/merge_step2_features.py::load_step2_results (the same
Step-2-output-loading logic the corpus merge already uses) rather than
reimplementing it, and pipeline/feature_engineering/extractor.py::
extract_all_features for the actual per-ad feature computation.

Embeddings make a real Replicate API call per non-empty title/body/usp (see
pipeline/feature_engineering/embeddings.py) -- `--sample-size` exists
specifically so a pilot can validate cost/behavior on a small, cheap sample
before committing to a full-corpus run.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ingestion.merge_step2_features import load_step2_results
from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.clients.replicate_client import EmbeddingClient
from pipeline.feature_engineering.extractor import extract_all_features
from pipeline.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STEP2_OUT_DIR = Path(__file__).parent.parent.parent / "out" / "step2"
DEFAULT_ADS_FILE = DATA_DIR / "supplements_enriched.json"
DEFAULT_OUTPUT_FILE = DATA_DIR / "feature_matrix.json"

_SUPPLEMENT_SUBCATEGORY_TERMS = {
    "protein": ("protein powder", "whey", "casein", "plant protein"),
    "creatine": ("creatine",),
    "collagen": ("collagen",),
    "probiotics": ("probiotic", "gut health"),
    "multivitamin": ("multivitamin", "multi-vitamin"),
    "omega-3": ("fish oil", "omega 3", "omega-3"),
    "magnesium": ("magnesium",),
    "weight management": ("weight loss", "fat burner", "weight management"),
    "pre-workout": ("pre workout", "pre-workout"),
    "sleep": ("melatonin", "sleep supplement", "sleep support"),
    "vitamins": ("vitamin",),
}


def infer_supplement_subcategory(ad: dict[str, Any]) -> str:
    """Deterministic fallback when landing-page/query provenance is absent."""
    text = " ".join(
        str(ad.get(key) or "") for key in ("title", "body", "caption", "page_name")
    ).lower()
    for category, terms in _SUPPLEMENT_SUBCATEGORY_TERMS.items():
        if any(term in text for term in terms):
            return category
    return "other supplements"


def _taxonomy_context(ad: dict[str, Any]) -> tuple[str | None, str]:
    """Return only subcategory evidence supported by human adjudication.

    A validated binary classifier can admit a Supplements row, but its coarse
    model category is not evidence for the project's detailed subcategories.
    """
    label = ad.get("taxonomy_label") or {}
    source = str(label.get("source") or "")
    if source == "human_adjudication":
        return label.get("supplement_subcategory"), source
    if source == "validated_classifier":
        return None, source
    return None, source


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomic checkpoint publish; caller owns the output lock."""
    atomic_write_json(path, rows)


def _brand_scaling_counts(ads: list[dict[str, Any]]) -> Counter[str]:
    """Count captured active-search ads per known Meta page.

    The Apify actor is configured for active ads, so the complete per-page
    corpus count is the repository's available Scaling proxy. Missing page IDs
    are not pooled into one fictitious brand.
    """
    ad_ids_by_page: defaultdict[str, set[str]] = defaultdict(set)
    for ad in ads:
        if ad.get("page_id") and ad.get("ad_archive_id"):
            ad_ids_by_page[str(ad["page_id"])].add(str(ad["ad_archive_id"]))
    return Counter({page_id: len(ad_ids) for page_id, ad_ids in ad_ids_by_page.items()})


def build_feature_matrix(
    ads_file: Path = DEFAULT_ADS_FILE,
    step2_out_dir: Path = STEP2_OUT_DIR,
    sample_size: int | None = None,
    seed: int = 42,
    embedding_client: EmbeddingClient | None = None,
    existing_rows: list[dict[str, Any]] | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_every: int = 25,
    include_embeddings: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (rows, summary). Each row is
    {ad_id, price_tier, **features}. `summary` reports row count,
    price_tier distribution, how many rows got real creative_features
    (Step 2 data), and how many ads failed feature extraction -- the same
    sanity checks the original Phase 2 Bridge plan called for (no NaN
    explosion, price_tier distribution matches Phase 1, real distributional
    variety, not everything defaulting to UNKNOWN/null).

    One bad ad's feature extraction never kills the run (mirrors
    ingestion/run_step2_pipeline.py's per-ad catch-all at the batch-job
    boundary) -- a real ~935-ad-in, zero-rows-out crash on an unretried
    httpx.RemoteProtocolError motivated this. `existing_rows` (already-built
    rows, e.g. loaded from a prior partial `--out` file) are kept as-is and
    their ad_ids skipped, so a resumed run doesn't re-pay for embeddings
    already fetched. `checkpoint_path`, if given, flushes accumulated rows to
    disk every `checkpoint_every` newly-processed ads, so a later crash loses
    at most one checkpoint interval's worth of paid API calls, not the whole
    run."""
    all_ads = json.loads(ads_file.read_text())
    scaling_counts = _brand_scaling_counts(all_ads)
    ads = list(all_ads)
    if sample_size is not None:
        rng = random.Random(seed)
        ads = rng.sample(ads, min(sample_size, len(ads)))
    selected_ids = {str(ad.get("ad_archive_id")) for ad in ads}

    creative_by_id = load_step2_results(step2_out_dir)
    embedding_client = embedding_client or EmbeddingClient()

    ads_by_id = {str(ad.get("ad_archive_id")): ad for ad in all_ads}
    existing_rows = existing_rows or []
    if sample_size is not None:
        # A different sample size with the same seed is not guaranteed to be
        # a strict superset under random.sample(). Retain paid embeddings only
        # for rows in this run's exact sample; otherwise resume silently grows
        # beyond --sample-size and makes the report irreproducible.
        existing_rows = [row for row in existing_rows if str(row.get("ad_id")) in selected_ids]
    rows: list[dict[str, Any]] = []
    for existing in existing_rows:
        row = dict(existing)
        if not include_embeddings:
            for name in ("title_embedding", "body_embedding", "usp_embedding"):
                row[name] = []
        ad = ads_by_id.get(str(row.get("ad_id")), {})
        creative = creative_by_id.get(str(row.get("ad_id")))
        if creative is not None:
            color_keys = {
                "background_hex",
                "background_style",
                "dominant_hex_palette",
                "contrast_ratio_type",
                "ad_id",
                "aspect_ratio",
            }
            for key, value in creative.items():
                if key not in color_keys:
                    row[f"creative_{key}"] = value
        page_id = str(ad.get("page_id") or row.get("page_id") or "")
        row["page_id"] = page_id
        search_queries = ad.get("search_queries", [])
        taxonomy_subcategory, taxonomy_source = _taxonomy_context(ad)
        row["taxonomy_label_source"] = taxonomy_source
        row["product_subcategory"] = (
            None
            if taxonomy_source == "validated_classifier"
            else (
                taxonomy_subcategory
                or (ad.get("product_page") or {}).get("product_subcategory")
                or ((search_queries or [""])[0])
                or row.get("product_subcategory")
                or infer_supplement_subcategory(ad)
            )
        )
        row["brand_scaling_count"] = scaling_counts.get(page_id, 1)
        rows.append(row)
    already_have_ids: set[str] = {r["ad_id"] for r in existing_rows}
    price_tier_counts: Counter[str] = Counter(r["price_tier"] for r in existing_rows)
    hook_framework_counts: Counter[str] = Counter(
        r.get("creative_hook_framework") or "null" for r in existing_rows
    )
    with_creative_features = sum(1 for ad_id in already_have_ids if ad_id in creative_by_id)
    failed = 0

    for ad in ads:
        ad_id = ad.get("ad_archive_id")
        if not ad_id or ad_id in already_have_ids:
            continue
        creative_features = creative_by_id.get(ad_id)

        try:
            features, price_tier = extract_all_features(
                ad,
                creative_features=creative_features,
                embedding_client=embedding_client,
                include_embeddings=include_embeddings,
            )
        except Exception:
            logger.warning("feature_extraction_failed", ad_id=ad_id, exc_info=True)
            failed += 1
            continue

        if creative_features is not None:
            with_creative_features += 1
        page_id = str(ad.get("page_id") or "")
        taxonomy_subcategory, taxonomy_source = _taxonomy_context(ad)
        rows.append(
            {
                "ad_id": ad_id,
                "page_id": page_id,
                "taxonomy_label_source": taxonomy_source,
                "product_subcategory": (
                    None
                    if taxonomy_source == "validated_classifier"
                    else (
                        taxonomy_subcategory
                        or (ad.get("product_page") or {}).get("product_subcategory")
                        or ((ad.get("search_queries") or [""])[0])
                        or infer_supplement_subcategory(ad)
                    )
                ),
                "brand_scaling_count": scaling_counts.get(page_id, 1),
                "price_tier": price_tier,
                **features,
            }
        )
        already_have_ids.add(ad_id)
        price_tier_counts[price_tier] += 1
        hook_framework_counts[features.get("creative_hook_framework") or "null"] += 1

        if checkpoint_path is not None and len(rows) % checkpoint_every == 0:
            _write_rows(checkpoint_path, rows)

    summary = {
        "row_count": len(rows),
        "with_creative_features": with_creative_features,
        "failed": failed,
        "price_tier_distribution": dict(price_tier_counts),
        "creative_hook_framework_distribution": dict(hook_framework_counts),
        "embeddings_included": include_embeddings,
    }
    return rows, summary


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Build the final ML feature matrix.")
    parser.add_argument("--ads", type=Path, default=DEFAULT_ADS_FILE)
    parser.add_argument("--step2-out", type=Path, default=STEP2_OUT_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Process a random sample instead of the full corpus (for pilots -- "
        "embeddings make a real paid API call per ad).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=25,
        help="Flush progress to --out every N newly-processed ads, so a crash "
        "(e.g. a transient network error after retries are exhausted) doesn't "
        "lose the whole run.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any existing --out file and reprocess every ad from scratch "
        "(default: resume, skipping ad_ids already present in --out).",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Build deterministic/creative features without paid Replicate embeddings.",
    )
    args = parser.parse_args()
    if args.checkpoint_every < 1:
        parser.error("--checkpoint-every must be at least 1")

    existing_rows: list[dict[str, Any]] = []
    if not args.no_resume and args.out.exists():
        existing_rows = json.loads(args.out.read_text())
        print(f"Resuming: {len(existing_rows)} ads already in {args.out}")

    start = time.monotonic()
    with exclusive_output(args.out):
        rows, summary = build_feature_matrix(
            ads_file=args.ads,
            step2_out_dir=args.step2_out,
            sample_size=args.sample_size,
            seed=args.seed,
            existing_rows=existing_rows,
            checkpoint_path=args.out,
            checkpoint_every=args.checkpoint_every,
            include_embeddings=not args.skip_embeddings,
        )
        _write_rows(args.out, rows)
    elapsed = time.monotonic() - start

    print(f"✅ Built feature matrix: {summary['row_count']} rows -> {args.out}")
    print(f"   Elapsed: {elapsed:.1f}s ({elapsed / max(summary['row_count'], 1):.2f}s/ad)")
    print(f"   With creative_features (Step 2 data): {summary['with_creative_features']}")
    if summary["failed"]:
        print(f"   ⚠️  {summary['failed']} ads failed feature extraction (skipped, logged)")
    print(f"   price_tier distribution: {summary['price_tier_distribution']}")
    print(f"   hook_framework distribution: {summary['creative_hook_framework_distribution']}")


if __name__ == "__main__":
    main()
