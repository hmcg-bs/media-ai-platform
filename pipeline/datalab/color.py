"""Measure the true text colour of Datalab blocks by sampling the original image.

Style Preserver's HTML ``color`` is a non-deterministic VLM guess, not the real colour.
This module fills ``TextStyle.color_measured`` from actual pixels: crop the image at each
block's bbox and take the colour of the glyph pixels (the Otsu *minority* class — within a
tight text box the background dominates, so the smaller class is the text).

Pure numpy + PIL (no cv2). Ported from the prototype ``scripts/datalab_ad_pipeline.py``.

Only single-run leaf blocks are measured: a Figure block's several labels share one region
bbox, so a per-label colour isn't attributable (same rule as the font-size proxy).
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from pipeline.datalab.models import DatalabDocument


def otsu(gray: np.ndarray) -> int:
    """Otsu's threshold on an 8-bit grayscale array."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_total = np.dot(np.arange(256), hist)
    w_b = 0.0
    sum_b = 0.0
    max_var = -1.0
    thresh = 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            thresh = t
    return thresh


def text_color_hex(crop_rgb: np.ndarray) -> str | None:
    """Estimate glyph colour in a text crop: mean of the Otsu minority (text) class."""
    if crop_rgb.size == 0:
        return None
    gray = crop_rgb.mean(axis=2).astype(np.uint8)
    t = otsu(gray)
    dark = gray <= t
    n_dark = int(dark.sum())
    text_mask = dark if n_dark <= (dark.size - n_dark) else ~dark
    text_px = crop_rgb[text_mask]
    if text_px.size == 0:
        return None
    r, g, b = (int(round(v)) for v in text_px.reshape(-1, 3).mean(axis=0))
    return f"#{r:02X}{g:02X}{b:02X}"


def measure_text_colors(doc: DatalabDocument, image_bytes: bytes) -> None:
    """Fill ``color_measured`` on each single-run block from the original image pixels.

    Bboxes are in Datalab's (upscaled) canvas space; scale to image pixels before cropping.
    Blocks with zero or multiple runs are left untouched (color_measured stays None).
    """
    canvas = doc.canvas
    if not canvas:
        return
    cw, ch = canvas
    if cw <= 0 or ch <= 0:
        return

    with Image.open(io.BytesIO(image_bytes)) as im:
        img = np.asarray(im.convert("RGB"))
    img_h, img_w = img.shape[:2]
    sx, sy = img_w / cw, img_h / ch

    for block in doc.blocks():
        if len(block.text_runs) != 1 or block.area <= 0:
            continue
        x0 = max(int(block.x0 * sx), 0)
        y0 = max(int(block.y0 * sy), 0)
        x1 = min(math.ceil(block.x1 * sx), img_w)
        y1 = min(math.ceil(block.y1 * sy), img_h)
        crop = img[y0:y1, x0:x1]
        block.text_runs[0].style.color_measured = text_color_hex(crop)
