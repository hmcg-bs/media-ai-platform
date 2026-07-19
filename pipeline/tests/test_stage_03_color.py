"""Stage 3 colour — real OpenCV math on synthetic images, fully offline."""

from __future__ import annotations

import numpy as np

from pipeline.models.output_schema import PipelineContext
from pipeline.stages.stage_03_color import (
    ColorStage,
    _bgr_to_hex,
    _classify_contrast,
)
from pipeline.tests.conftest import make_image_bytes


def test_solid_image_palette_matches_fill_colour(square_context):
    # square_context is solid (220, 30, 30) RGB.
    result = ColorStage().process(square_context)
    profile = result.result.color_profile
    assert profile.dominant_hex_palette[0] == "#DC1E1E"   # 220,30,30
    assert profile.background_hex == "#DC1E1E"
    assert profile.background_style == "Studio"            # uniform perimeter


def test_bgr_to_hex_orders_channels_correctly():
    # OpenCV is BGR: [blue, green, red].
    assert _bgr_to_hex(np.array([0, 0, 255])) == "#FF0000"   # pure red
    assert _bgr_to_hex(np.array([255, 0, 0])) == "#0000FF"   # pure blue


def test_contrast_classification():
    black = np.array([0, 0, 0])
    white = np.array([255, 255, 255])
    assert _classify_contrast([black, white]) == "High"
    assert _classify_contrast([black]) == "Monochromatic"


def test_masks_text_boxes_before_clustering():
    # White image with a black text box; masking should keep palette white.
    img_bytes = make_image_bytes(100, 100, (255, 255, 255))
    ctx = PipelineContext(ad_id="t", image_path="m", image_bytes=img_bytes)
    ctx.ocr_boxes = [[(0, 0), (100, 0), (100, 100), (0, 100)]]  # full image masked
    # Whole image masked -> stage falls back to all pixels (still white).
    result = ColorStage().process(ctx)
    assert result.result.color_profile.dominant_hex_palette[0] == "#FFFFFF"
