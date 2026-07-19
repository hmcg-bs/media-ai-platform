"""Background colour variables from the clean Qwen background layer (ADR-008 #2).

Qwen-Image-Layered's background layer has the copy and product removed, giving a cleaner
K-Means input than stage_03's perimeter-band approximation. We run the same deterministic
colour maths on it and emit the existing ``ColorProfile`` (so it drops straight into the
Master Schema). Text colour is handled separately (Datalab ``color_measured``).

Reuses the proven colour classifiers from ``stage_03_color`` to avoid divergence; only the
K-Means-over-a-whole-layer step is local (stage_03's is coupled to its masking flow).
"""

from __future__ import annotations

import cv2
import numpy as np

from pipeline.config import get_settings
from pipeline.models.output_schema import ColorProfile
from pipeline.stages.stage_03_color import (
    _bgr_to_hex,
    _classify_background_style,
    _classify_contrast,
)


def _kmeans_palette(pixels: np.ndarray, k: int) -> tuple[list[np.ndarray], list[str]]:
    """Dominant palette (ordered by cluster size) as BGR centres + hex strings."""
    data = np.float32(pixels.reshape(-1, 3))
    k = min(k, len(data))
    if k < 1:
        return [], []
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(data, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(counts)[::-1]
    palette_bgr = [centers[i] for i in order]
    return palette_bgr, [_bgr_to_hex(c) for c in palette_bgr]


def background_color_profile(layer_png: bytes, k: int | None = None) -> ColorProfile:
    """Compute background colour variables from a Qwen background-layer PNG.

    The whole layer is sampled (incl. inpainted regions — accepted for gradients per
    ADR-008). Returns a ``ColorProfile`` with the background hex, dominant palette,
    background style, and contrast classification.
    """
    k = k or get_settings().kmeans_clusters
    arr = np.frombuffer(layer_png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # drops alpha; bg layer is fully opaque
    if img is None:
        raise ValueError("could not decode background layer PNG")

    pixels = img.reshape(-1, 3)
    palette_bgr, palette_hex = _kmeans_palette(pixels, k)
    return ColorProfile(
        background_hex=palette_hex[0] if palette_hex else "",
        background_style=_classify_background_style(pixels),
        dominant_hex_palette=palette_hex,
        contrast_ratio_type=_classify_contrast(palette_bgr),
    )
