"""Stage 3 — colour profile (OpenCV K-Means, deterministic).

1. Mask out OCR bounding boxes so dark text doesn't skew the palette.
2. K-Means over the remaining pixels -> dominant HEX palette.
3. Sample the outermost perimeter band -> background colour + style.
4. Classify contrast from palette luminance spread.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from pipeline.config import get_settings
from pipeline.logger import get_logger
from pipeline.models.output_schema import ColorProfile, PipelineContext
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)


def _bgr_to_hex(bgr: np.ndarray) -> str:
    b, g, r = (int(round(c)) for c in bgr[:3])
    return f"#{r:02X}{g:02X}{b:02X}".upper()


def _luminance(bgr: np.ndarray) -> float:
    b, g, r = (float(c) for c in bgr[:3])
    return 0.114 * b + 0.587 * g + 0.299 * r


def _classify_contrast(palette_bgr: list[np.ndarray]) -> str:
    if len(palette_bgr) < 2:
        return "Monochromatic"
    lums = [_luminance(c) for c in palette_bgr]
    spread = max(lums) - min(lums)
    if spread >= 140:
        return "High"
    if spread <= 50:
        return "Low"
    return "Medium"


def _classify_background_style(perimeter_pixels: np.ndarray) -> str:
    """Studio (near-uniform), Gradient (smooth variance), or Busy."""
    if perimeter_pixels.size == 0:
        return ""
    std = float(np.mean(np.std(perimeter_pixels.reshape(-1, 3), axis=0)))
    if std < 12:
        return "Studio"
    if std < 40:
        return "Gradient"
    return "Busy"


class ColorStage(BaseStage):
    name = "stage_03_color"

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            arr = np.frombuffer(context.image_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("cv2 could not decode image")

            height, width = img.shape[:2]

            # 1. Mask out OCR text regions.
            mask = np.ones((height, width), dtype=bool)
            for box in context.ocr_boxes:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x0, x1 = max(0, min(xs)), min(width, max(xs))
                y0, y1 = max(0, min(ys)), min(height, max(ys))
                mask[y0:y1, x0:x1] = False

            non_text = img[mask]
            if non_text.size == 0:  # whole image was text — fall back to all pixels
                non_text = img.reshape(-1, 3)

            # 2. K-Means palette.
            palette, palette_bgr = self._kmeans_palette(non_text)

            # 3. Perimeter band -> background.
            perimeter = self._perimeter_pixels(img, self.settings.kmeans_perimeter_pct)
            background_hex = (
                _bgr_to_hex(np.mean(perimeter.reshape(-1, 3), axis=0))
                if perimeter.size
                else (palette[0] if palette else "")
            )

            context.result.color_profile = ColorProfile(
                background_hex=background_hex,
                background_style=_classify_background_style(perimeter),
                dominant_hex_palette=palette,
                contrast_ratio_type=_classify_contrast(palette_bgr),
            )
            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                palette=palette,
            )
            return context
        except Exception as exc:  # noqa: BLE001
            raise StageError(self.name, "colour analysis failed", exc) from exc

    def _kmeans_palette(self, pixels: np.ndarray) -> tuple[list[str], list[np.ndarray]]:
        """Returns (hex_palette, bgr_palette) — both derived, kept as local
        return values (not instance state) so a single ColorStage instance is
        safe to reuse concurrently across ads (Step 2's across-ad concurrency
        shares one stage list across worker threads)."""
        data = np.float32(pixels.reshape(-1, 3))
        k = min(self.settings.kmeans_clusters, len(data))
        if k < 1:
            return [], []
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, labels, centers = cv2.kmeans(
            data, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS
        )
        # Order palette by cluster size (most dominant first).
        counts = np.bincount(labels.flatten(), minlength=k)
        order = np.argsort(counts)[::-1]
        palette_bgr = [centers[i] for i in order]
        return [_bgr_to_hex(centers[i]) for i in order], palette_bgr

    @staticmethod
    def _perimeter_pixels(img: np.ndarray, pct: float) -> np.ndarray:
        height, width = img.shape[:2]
        band_h = max(1, int(height * pct))
        band_w = max(1, int(width * pct))
        top, bottom = img[:band_h, :], img[-band_h:, :]
        left, right = img[:, :band_w], img[:, -band_w:]
        return np.concatenate(
            [top.reshape(-1, 3), bottom.reshape(-1, 3),
             left.reshape(-1, 3), right.reshape(-1, 3)]
        )
