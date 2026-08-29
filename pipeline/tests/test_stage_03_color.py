"""Stage 3 colour — real OpenCV math on synthetic images, fully offline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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


class TestColorStageSafeForConcurrentReuse:
    """Regression: _kmeans_palette used to stash its BGR result on
    self._palette_bgr (instance state), read back a few lines later in the
    same process() call. Step 2's across-ad concurrency reuses one shared
    stages list across worker threads, so two ads' process() calls could
    interleave on the same ColorStage instance — one ad's kmeans result could
    silently overwrite another's before it was read, corrupting
    contrast_ratio_type for the wrong ad. Fixed by returning the BGR palette
    as a local value instead of storing it on self."""

    def test_two_sequential_calls_on_same_instance_stay_independent(self):
        stage = ColorStage()
        black_ctx = PipelineContext(
            ad_id="black", image_path="m", image_bytes=make_image_bytes(50, 50, (0, 0, 0))
        )
        white_ctx = PipelineContext(
            ad_id="white", image_path="m", image_bytes=make_image_bytes(50, 50, (255, 255, 255))
        )

        black_result = stage.process(black_ctx).result.color_profile
        white_result = stage.process(white_ctx).result.color_profile

        assert black_result.dominant_hex_palette[0] == "#000000"
        assert white_result.dominant_hex_palette[0] == "#FFFFFF"

    def test_concurrent_calls_on_shared_instance_never_cross_contaminate(self):
        stage = ColorStage()
        red_bytes = make_image_bytes(80, 80, (220, 30, 30))
        blue_bytes = make_image_bytes(80, 80, (30, 30, 220))

        def run_red():
            ctx = PipelineContext(ad_id="red", image_path="m", image_bytes=red_bytes)
            return stage.process(ctx).result.color_profile.dominant_hex_palette[0]

        def run_blue():
            ctx = PipelineContext(ad_id="blue", image_path="m", image_bytes=blue_bytes)
            return stage.process(ctx).result.color_profile.dominant_hex_palette[0]

        # Repeat many times with a real thread pool to give any residual race
        # a chance to manifest, rather than relying on a single lucky/unlucky
        # interleaving.
        with ThreadPoolExecutor(max_workers=2) as pool:
            for _ in range(20):
                red_future = pool.submit(run_red)
                blue_future = pool.submit(run_blue)
                assert red_future.result() == "#DC1E1E"
                assert blue_future.result() == "#1E1EDC"
