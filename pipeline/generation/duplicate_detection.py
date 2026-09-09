"""Duplicate-product hallucination detection (Generation v2, 2026-08-30).

Confirmed live (Round 6-8, docs/generation-failure-modes.md bug #6): Flux
Fill sometimes paints a second, garbled product-like object into the open
background despite explicit prompt rules -- raising `guidance` reduced but
never eliminated it. Rather than hoping the general-purpose review agents
(reviewer.py, blend.py) happen to catch it, this module checks directly.

Deterministic-first, escalate only when ambiguous (per the user's own
confirmed decision): re-run background-removal on the *finished*
background+product image -- a general foreground segmentation model, so it
tends to segment both the real product and any hallucinated duplicate as
separate blobs -- then use connected-component analysis (scipy.ndimage) to
find them. One blob should land almost exactly on the already-known
`original_product_bbox` (masking.py structurally guarantees those pixels are
never touched by Flux Fill, so this is a near-exact position match, not a
fuzzy one); any other significant blob is a duplicate candidate. A single
clean candidate that closely matches the product's own size/aspect ratio is
flagged directly, no API call needed. Anything messier (an odd shape, or
multiple candidates) is ambiguous enough to warrant one vision call rather
than guessing.
"""

from __future__ import annotations

import io
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.clients.replicate_client import BackgroundRemoverClient
from pipeline.generation.layout import BoundingBox

_ALPHA_PRESENCE_THRESHOLD = 10
# Ignore small props/noise blobs -- only a candidate large enough to
# plausibly be a whole duplicated product counts.
_MIN_BLOB_AREA_FRACTION = 0.03
# masking.py's own dilation margin is only a few pixels -- a blob within
# this fraction of the original bbox's position/size on every edge is the
# real product itself, not a separate object.
_ORIGINAL_BBOX_MATCH_TOLERANCE = 0.05
_SIZE_RATIO_MIN = 0.5
_SIZE_RATIO_MAX = 2.0
_ASPECT_RATIO_TOLERANCE = 0.4


class DuplicateDetectionResult(BaseModel):
    duplicate_detected: bool
    duplicate_bbox: BoundingBox | None = None
    detection_method: Literal["deterministic", "vision_agent", "none"]
    notes: str


class _DuplicateVisionResult(BaseModel):
    """The vision-escalation schema -- deliberately narrower than
    DuplicateDetectionResult (no detection_method, that's a code-level fact
    about which path ran, not something the model judges)."""

    duplicate_detected: bool
    duplicate_bbox: BoundingBox | None = None
    notes: str


def _blob_bboxes(alpha: np.ndarray) -> list[BoundingBox]:
    from scipy import ndimage

    binary = alpha > _ALPHA_PRESENCE_THRESHOLD
    labeled, n_blobs = ndimage.label(binary)
    height, width = alpha.shape

    boxes = []
    for label_id in range(1, n_blobs + 1):
        ys, xs = np.where(labeled == label_id)
        if len(xs) == 0:
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        boxes.append(
            BoundingBox(
                x=x0 / width, y=y0 / height,
                width=(x1 - x0 + 1) / width, height=(y1 - y0 + 1) / height,
            )
        )
    return boxes


def _matches_original(bbox: BoundingBox, original: BoundingBox) -> bool:
    return (
        abs(bbox.x - original.x) < _ORIGINAL_BBOX_MATCH_TOLERANCE
        and abs(bbox.y - original.y) < _ORIGINAL_BBOX_MATCH_TOLERANCE
        and abs(bbox.width - original.width) < _ORIGINAL_BBOX_MATCH_TOLERANCE
        and abs(bbox.height - original.height) < _ORIGINAL_BBOX_MATCH_TOLERANCE
    )


def _closely_matches_product_shape(bbox: BoundingBox, original: BoundingBox) -> bool:
    original_area = original.width * original.height
    if original_area <= 0 or original.height <= 0 or bbox.height <= 0:
        return False
    size_ratio = (bbox.width * bbox.height) / original_area
    if not (_SIZE_RATIO_MIN <= size_ratio <= _SIZE_RATIO_MAX):
        return False
    original_aspect = original.width / original.height
    if original_aspect <= 0:
        return False
    aspect = bbox.width / bbox.height
    return abs(aspect - original_aspect) / original_aspect <= _ASPECT_RATIO_TOLERANCE


def detect_duplicate_product(
    bg_remover_client: BackgroundRemoverClient,
    genai_client: GenAIClient,
    *,
    model: str,
    generated_image_bytes: bytes,
    original_product_bbox: BoundingBox,
    original_product_photo_bytes: bytes,
) -> DuplicateDetectionResult:
    cutout = bg_remover_client.remove_background(generated_image_bytes)
    alpha = np.array(Image.open(io.BytesIO(cutout)).convert("RGBA"))[..., 3]

    candidates = [
        b for b in _blob_bboxes(alpha)
        if b.width * b.height >= _MIN_BLOB_AREA_FRACTION
        and not _matches_original(b, original_product_bbox)
    ]

    if not candidates:
        return DuplicateDetectionResult(
            duplicate_detected=False, duplicate_bbox=None, detection_method="none",
            notes="No blob found beyond the known product region.",
        )

    if len(candidates) == 1 and _closely_matches_product_shape(
        candidates[0], original_product_bbox
    ):
        return DuplicateDetectionResult(
            duplicate_detected=True, duplicate_bbox=candidates[0], detection_method="deterministic",
            notes="Exactly one extra blob, closely matching the product's own size/aspect ratio.",
        )

    prompt = """The FIRST image is the original product photo. The SECOND
image is a generated ad background with that same product composited onto
it. Does the SECOND image contain more than one instance of the product
shown in the first image? If so, report the bounding box (fractions of the
second image, 0.0-1.0) of the EXTRA, hallucinated instance only -- not the
real one. If there is no duplicate, set duplicate_detected to false and
leave duplicate_bbox null."""
    vision_result = genai_client.extract_structured_multi_image(
        model=model,
        prompt=prompt,
        images=[
            (original_product_photo_bytes, "image/jpeg"),
            (generated_image_bytes, "image/png"),
        ],
        schema=_DuplicateVisionResult,
    )
    return DuplicateDetectionResult(
        duplicate_detected=vision_result.duplicate_detected,
        duplicate_bbox=vision_result.duplicate_bbox,
        detection_method="vision_agent",
        notes=vision_result.notes,
    )
