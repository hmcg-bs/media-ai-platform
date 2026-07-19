"""Stage 2 (alt) — copywriting + positioning via Datalab (ADR-008, optional/paid).

Replaces the Cloud Vision OCR stage when ``enable_datalab_copy`` is set. One plain-convert
call yields the full copy (incl. the headline Style Preserver drops) with per-block bboxes;
the mapping fills ``typography_hierarchy``, ``copywriting_features``, and ``placement``, and
sets ``ocr_boxes`` (image-space) so the downstream colour stage can still mask text.

Role-dependent copywriting fields (hook_type, cta_present, …) need the Datalab *extract*
step and stay at defaults for now. Text colour is handled by stage_06 / Datalab
``color_measured``, not here.
"""

from __future__ import annotations

import time

from pipeline.clients.datalab_client import DatalabDocumentClient
from pipeline.config import get_settings
from pipeline.datalab.color import measure_text_colors
from pipeline.datalab.mapping import (
    to_copywriting_features,
    to_marketing_psychology,
    to_ocr_boxes,
    to_placement,
    to_typography_hierarchy,
)
from pipeline.logger import get_logger
from pipeline.models.output_schema import PipelineContext
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)


class DatalabCopyStage(BaseStage):
    name = "stage_02_datalab_copy"

    def __init__(self, client: DatalabDocumentClient | None = None, settings=None):
        self.settings = settings or get_settings()
        self.client = client or DatalabDocumentClient(settings=self.settings)

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            doc, extract = self.client.analyze(context.image_path)
            if context.image_bytes:
                measure_text_colors(doc, context.image_bytes)  # fill color_measured

            result = context.result
            result.typography_hierarchy = to_typography_hierarchy(doc)
            result.copywriting_features = to_copywriting_features(doc, extract)
            result.placement = to_placement(doc)
            result.marketing_psychology = to_marketing_psychology(extract)

            meta = result.technical_metadata
            context.ocr_boxes = to_ocr_boxes(doc, meta.width, meta.height)

            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                copy_blocks=result.copywriting_features.copy_block_count,
                headline=result.typography_hierarchy.primary_headline.text[:50],
            )
            return context
        except Exception as exc:  # noqa: BLE001 — wrapped for the orchestrator
            raise StageError(self.name, "Datalab copy extraction failed", exc) from exc
