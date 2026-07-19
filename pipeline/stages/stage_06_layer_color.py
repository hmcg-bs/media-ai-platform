"""Stage 6 — background colour from Qwen layer decomposition (ADR-008, optional/paid).

Runs *after* the deterministic ColorStage and overwrites ``color_profile`` with the
cleaner layer-based reading (Qwen-Image-Layered's background layer has the copy/product
removed). Per-stage fallback means if the paid call fails, the ColorStage value survives.
Text colour is handled separately (Datalab ``color_measured``).
"""

from __future__ import annotations

import time

from pipeline.clients.replicate_client import QwenLayersClient
from pipeline.color.background import background_color_profile
from pipeline.config import get_settings
from pipeline.logger import get_logger
from pipeline.models.output_schema import PipelineContext
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)


class LayerColorStage(BaseStage):
    name = "stage_06_layer_color"

    def __init__(self, client: QwenLayersClient | None = None, settings=None):
        self.settings = settings or get_settings()
        self.client = client or QwenLayersClient(settings=self.settings)

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            layers = self.client.decompose(context.image_bytes)
            if layers:  # layer_0 = background
                context.result.color_profile = background_color_profile(layers[0])
            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                layer_count=len(layers),
                background_hex=context.result.color_profile.background_hex,
            )
            return context
        except Exception as exc:  # noqa: BLE001 — wrapped for the orchestrator
            raise StageError(self.name, "layer colour extraction failed", exc) from exc
