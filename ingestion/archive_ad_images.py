"""Download, compress, and durably archive ad images in GCS."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ingestion.gcs_images import (
    GCSImageStore,
    compress_reference_image,
    estimate_monthly_storage_cost,
)
from pipeline.artifacts import atomic_write_json, exclusive_output


def _archive_one(ad: dict[str, Any], store: GCSImageStore, collection: str) -> dict[str, Any]:
    ad_id = str(ad["ad_archive_id"])
    url = ad["image_urls"][0]
    response = httpx.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=30
    )
    response.raise_for_status()
    image = compress_reference_image(response.content)
    gcs_uri = store.upload(
        ad_id,
        image,
        collection=collection,
        metadata={"page_id": str(ad.get("page_id") or "")},
    )
    return {
        "ad_id": ad_id,
        "page_id": str(ad.get("page_id") or ""),
        "page_name": ad.get("page_name") or "",
        "start_date": ad.get("start_date"),
        "snapshot_url": ad.get("snapshot_url") or "",
        "gcs_uri": gcs_uri,
        **image.metadata(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive compressed Meta ad images to GCS.")
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/ad_image_archive_manifest.json"))
    parser.add_argument("--bucket")
    parser.add_argument("--collection", default="archive")
    parser.add_argument(
        "--benchmark-review",
        type=Path,
        help="Archive only explicitly approved benchmark candidates (maximum eight).",
    )
    parser.add_argument("--max-images", type=int)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    ads = [
        ad
        for ad in json.loads(args.ads.read_text())
        if ad.get("ad_archive_id") and ad.get("image_urls")
    ]
    usage_constraints: list[str] = []
    if args.benchmark_review:
        review = json.loads(args.benchmark_review.read_text())
        approved = {
            str(row["ad_id"])
            for row in review.get("candidates", [])
            if row.get("approved_for_craft_reference") is True
        }
        if len(approved) > 8:
            parser.error("benchmark review approves more than the maximum eight references")
        ads = [ad for ad in ads if str(ad["ad_archive_id"]) in approved]
        if not ads:
            parser.error("benchmark review has no approved candidates present in the ad corpus")
        usage_constraints = review.get("usage_constraints", [])
    if args.max_images is not None:
        ads = ads[: args.max_images]
    store = GCSImageStore(bucket_name=args.bucket)
    store.ensure_bucket()
    manifest: dict[str, Any] = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "source": str(args.ads),
        "compression": {"format": "webp", "quality": 82, "max_dimension": 1600},
        "directive_aligned": False,
        "usage_constraints": usage_constraints,
        "images": [],
        "failures": [],
    }
    with exclusive_output(args.out):
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_archive_one, ad, store, args.collection): ad for ad in ads}
            for future in as_completed(futures):
                ad = futures[future]
                try:
                    manifest["images"].append(future.result())
                except Exception as exc:  # noqa: BLE001 - retain every per-ad failure
                    manifest["failures"].append(
                        {"ad_id": str(ad["ad_archive_id"]), "error": str(exc)[:500]}
                    )
                atomic_write_json(args.out, manifest)

        manifest["images"].sort(key=lambda row: row["ad_id"])
        source_bytes = sum(row["source_bytes"] for row in manifest["images"])
        stored_bytes = sum(row["stored_bytes"] for row in manifest["images"])
        manifest["summary"] = {
            "requested": len(ads),
            "stored": len(manifest["images"]),
            "failed": len(manifest["failures"]),
            "source_bytes": source_bytes,
            "stored_bytes": stored_bytes,
            "compression_ratio": round(stored_bytes / source_bytes, 4) if source_bytes else None,
            "estimated_standard_storage_usd_per_month": estimate_monthly_storage_cost(stored_bytes),
        }
        atomic_write_json(args.out, manifest)
        manifest_bytes = args.out.read_bytes()
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_uri = store.upload_manifest(
            f"{args.collection}/manifests/{manifest_sha256}.json", manifest_bytes
        )
    print(f"Archived images: {manifest['summary']}; manifest={manifest_uri}")


if __name__ == "__main__":
    main()
