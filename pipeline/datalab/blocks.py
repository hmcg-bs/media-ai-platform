"""Pure helpers for walking Datalab convert-JSON blocks and their geometry.

No external dependencies and no I/O — every function here is deterministic and
unit-testable, so the feature math on top of it stays easy to verify.

Coordinate note: Datalab renders on an upscaled *canvas* (the ``Page`` block
bbox), which is usually larger than the original image. Bboxes are in canvas
space; use :func:`canvas_dims` + :func:`scale_bbox` to map them to image pixels.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any

# Block types that are image/region containers — their bbox is a whole region,
# not a per-line text box. Excluded when attaching bboxes to text elements.
IMAGE_BLOCK_TYPES = frozenset(
    {"Figure", "Picture", "Diagram", "Image", "Table", "TableGroup"}
)

_TAG_RE = re.compile(r"<[^>]+>")
_NORM_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")


def iter_blocks(node: Any):
    """Yield every block dict (has ``block_type`` + ``id``) in the convert tree."""
    if isinstance(node, dict):
        if "block_type" in node and node.get("id"):
            yield node
        for child in node.get("children") or []:
            yield from iter_blocks(child)
    elif isinstance(node, list):
        for item in node:
            yield from iter_blocks(item)


def block_text(block: dict) -> str:
    """Plain text of a block (HTML tags stripped, entities unescaped)."""
    return unescape(_TAG_RE.sub(" ", block.get("html") or ""))


def norm_text(text: str) -> str:
    """Normalize for matching: lowercase, alphanumerics + single spaces."""
    return _WS_RE.sub(" ", _NORM_RE.sub(" ", unescape(text).lower())).strip()


def canvas_dims(convert_json: Any) -> tuple[float, float] | None:
    """Width/height of the convert canvas (the ``Page`` block bbox), or None."""
    for b in iter_blocks(convert_json):
        if b.get("block_type") == "Page" and b.get("bbox"):
            x0, y0, x1, y1 = b["bbox"]
            return (x1 - x0, y1 - y0)
    return None


def scale_bbox(
    bbox: list[float], sx: float, sy: float
) -> tuple[float, float, float, float]:
    """Scale a ``[x0, y0, x1, y1]`` bbox by per-axis factors."""
    return (bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy)


def bbox_metrics(
    bbox: list[float], cw: float, ch: float
) -> dict[str, float]:
    """Normalized centroid / size / coverage of a canvas-space bbox (0–1 scale)."""
    if cw <= 0 or ch <= 0:
        return {"x_center": 0.0, "y_center": 0.0, "width": 0.0,
                "height": 0.0, "coverage": 0.0}
    x0, y0, x1, y1 = bbox
    w = max(0.0, x1 - x0) / cw
    h = max(0.0, y1 - y0) / ch
    return {
        "x_center": round((x0 + x1) / 2 / cw, 4),
        "y_center": round((y0 + y1) / 2 / ch, 4),
        "width": round(w, 4),
        "height": round(h, 4),
        "coverage": round(w * h, 4),
    }


def zone_of(y_center: float) -> str:
    """Vertical band of a normalized y-centre: top / middle / bottom (thirds)."""
    if y_center < 1 / 3:
        return "top"
    if y_center < 2 / 3:
        return "middle"
    return "bottom"
