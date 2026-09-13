"""Efficient, content-addressed GCS storage for durable ad reference images."""

from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass
from typing import Any

from PIL import Image, ImageOps

from pipeline.config import get_settings

DEFAULT_MAX_DIMENSION = 1600
DEFAULT_WEBP_QUALITY = 82


@dataclass(frozen=True)
class CompressedImage:
    content: bytes
    source_sha256: str
    stored_sha256: str
    source_bytes: int
    stored_bytes: int
    width: int
    height: int
    format: str = "webp"
    quality: int = DEFAULT_WEBP_QUALITY

    def metadata(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("content")
        result["compression_ratio"] = round(self.stored_bytes / self.source_bytes, 4)
        return result


def compress_reference_image(
    source: bytes,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    quality: int = DEFAULT_WEBP_QUALITY,
) -> CompressedImage:
    """Auto-orient, bound dimensions, strip metadata, and encode WebP."""
    if not source:
        raise ValueError("image source is empty")
    if max_dimension < 256:
        raise ValueError("max_dimension must be at least 256")
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")
    with Image.open(io.BytesIO(source)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=quality, method=6, exif=b"")
        content = output.getvalue()
        width, height = image.size
    return CompressedImage(
        content=content,
        source_sha256=hashlib.sha256(source).hexdigest(),
        stored_sha256=hashlib.sha256(content).hexdigest(),
        source_bytes=len(source),
        stored_bytes=len(content),
        width=width,
        height=height,
        quality=quality,
    )


class GCSImageStore:
    """Upload content-addressed images without overwriting concurrent writes."""

    def __init__(self, bucket_name: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self.bucket_name = bucket_name or settings.gcs_ad_bucket
        if not self.bucket_name:
            raise ValueError("GCS_AD_BUCKET is required for image persistence")
        if client is None:
            from google.cloud import storage

            client = storage.Client(project=settings.gcp_project_id)
        self.client = client
        self.bucket = client.bucket(self.bucket_name)

    def ensure_bucket(self, location: str | None = None) -> None:
        from google.api_core.exceptions import Conflict, Forbidden, NotFound

        settings = get_settings()
        try:
            self.bucket = self.client.get_bucket(self.bucket_name)
        except Forbidden:
            # Bucket-scoped roles/storage.objectAdmin can write objects but
            # intentionally lacks storage.buckets.get. Let the object upload
            # provide the authoritative existence/permission check.
            return
        except NotFound:
            try:
                self.bucket = self.client.create_bucket(
                    self.bucket_name,
                    location=location or settings.gcp_region,
                )
            except Conflict:
                self.bucket = self.client.get_bucket(self.bucket_name)

    def upload(
        self,
        ad_id: str,
        image: CompressedImage,
        collection: str = "archive",
        metadata: dict[str, str] | None = None,
    ) -> str:
        from google.api_core.exceptions import PreconditionFailed

        object_name = f"{collection}/{ad_id}/{image.stored_sha256}.webp"
        blob = self.bucket.blob(object_name)
        blob.metadata = {
            "ad_id": ad_id,
            "source_sha256": image.source_sha256,
            "stored_sha256": image.stored_sha256,
            **(metadata or {}),
        }
        try:
            blob.upload_from_string(
                image.content,
                content_type="image/webp",
                if_generation_match=0,
            )
        except PreconditionFailed:
            pass
        return f"gs://{self.bucket_name}/{object_name}"

    def upload_manifest(self, path: str, content: bytes) -> str:
        """Create an immutable manifest; callers must use a versioned path."""
        from google.api_core.exceptions import PreconditionFailed

        blob = self.bucket.blob(path)
        try:
            blob.upload_from_string(
                content,
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed:
            # A retry of the exact content-addressed manifest is idempotent.
            pass
        return f"gs://{self.bucket_name}/{path}"


def estimate_monthly_storage_cost(total_bytes: int, usd_per_gib_month: float = 0.020) -> float:
    """Storage-only estimate; retrieval, egress, and operations are separate."""
    return round((total_bytes / 1024**3) * usd_per_gib_month, 4)
