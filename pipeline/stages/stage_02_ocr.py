"""Stage 2 — OCR + typography hierarchy (Cloud Vision + deterministic math).

We use Cloud Vision purely for *what the text is and where it sits*. The
hierarchy (which block is the headline) is decided by bounding-box area, not by
asking an LLM to guess.
"""

from __future__ import annotations

import time

from pipeline.clients.vision_client import OcrBlock, VisionClient
from pipeline.logger import get_logger
from pipeline.models.output_schema import (
    PipelineContext,
    TextBlock,
    TypographyHierarchy,
)
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)


def build_hierarchy(blocks: list[OcrBlock], canvas_area: float) -> TypographyHierarchy:
    """Largest-area block = headline; the rest = secondary copy (desc by area)."""
    if not blocks:
        return TypographyHierarchy()

    ranked = sorted(blocks, key=lambda b: b.area, reverse=True)
    headline = ranked[0]
    secondary = ranked[1:]

    def coverage(block: OcrBlock) -> float:
        if canvas_area <= 0:
            return 0.0
        return round(block.area / canvas_area * 100, 2)

    headline_block = TextBlock(
        text=headline.text, canvas_coverage_percentage=coverage(headline)
    )
    secondary_blocks = [
        TextBlock(text=b.text, canvas_coverage_percentage=coverage(b))
        for b in secondary
    ]

    scale_ratio = 0.0
    if secondary and secondary[0].area > 0:
        scale_ratio = round(headline.area / secondary[0].area, 3)

    return TypographyHierarchy(
        primary_headline=headline_block,
        secondary_copy=secondary_blocks,
        headline_to_subtext_scale_ratio=scale_ratio,
    )


class OCRStage(BaseStage):
    name = "stage_02_ocr"

    def __init__(self, vision_client: VisionClient | None = None):
        self.vision_client = vision_client or VisionClient()

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            blocks = self.vision_client.detect_text(context.image_bytes)

            meta = context.result.technical_metadata
            canvas_area = float(meta.width * meta.height)
            context.result.typography_hierarchy = build_hierarchy(blocks, canvas_area)

            # Hand the raw boxes to Stage 3 so it can mask text before clustering.
            context.ocr_boxes = [b.vertices for b in blocks if b.vertices]

            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                text_block_count=len(blocks),
                headline=context.result.typography_hierarchy.primary_headline.text[:50],
            )
            return context
        except Exception as exc:  # noqa: BLE001
            raise StageError(self.name, "OCR / typography extraction failed", exc) from exc
