"""Stage 7 — imagery description via Qwen3-VL (ADR-008, optional/paid).

Free-text description of the ad's product/imagery, written to ``imagery_description``.
The prompt (in config) tells the model not to transcribe on-screen text, so this stays
distinct from the copywriting/OCR path.
"""

from __future__ import annotations

import time

from pipeline.clients.replicate_client import QwenVLClient
from pipeline.config import get_settings
from pipeline.logger import get_logger
from pipeline.models.output_schema import PipelineContext
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)


class ImageryStage(BaseStage):
    name = "stage_07_imagery"

    def __init__(self, client: QwenVLClient | None = None, settings=None):
        self.settings = settings or get_settings()
        self.client = client or QwenVLClient(settings=self.settings)

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            context.result.imagery_description = self.client.describe(
                context.image_bytes, self.settings.imagery_prompt
            )
            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                chars=len(context.result.imagery_description),
            )
            return context
        except Exception as exc:  # noqa: BLE001 — wrapped for the orchestrator
            raise StageError(self.name, "imagery description failed", exc) from exc
