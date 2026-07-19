"""Stage 1 — technical metadata (deterministic).

Reads image dimensions and file type from the header. Computes a simplified
aspect-ratio label. Never sends anything to an LLM.
"""

from __future__ import annotations

import io
import time
from math import gcd

from PIL import Image

from pipeline.logger import get_logger
from pipeline.models.output_schema import PipelineContext, TechnicalMetadata
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)

# Common ad aspect ratios we snap to when the exact reduction is awkward.
_COMMON_RATIOS: dict[float, str] = {
    1.0: "1:1",
    4 / 5: "4:5",
    9 / 16: "9:16",
    16 / 9: "16:9",
    1.91: "1.91:1",
}


def _aspect_ratio_label(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return ""
    ratio = width / height
    # Snap to a common ad ratio if close (within 3%).
    for value, label in _COMMON_RATIOS.items():
        if abs(ratio - value) / value <= 0.03:
            return label
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


class MetadataStage(BaseStage):
    name = "stage_01_metadata"

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            if context.image_bytes is None:
                with open(context.image_path, "rb") as fh:
                    context.image_bytes = fh.read()

            with Image.open(io.BytesIO(context.image_bytes)) as img:
                width, height = img.size
                file_type = (img.format or "").lower()

            context.result.technical_metadata = TechnicalMetadata(
                width=width,
                height=height,
                aspect_ratio=_aspect_ratio_label(width, height),
                file_type=file_type,
            )
            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                width=width,
                height=height,
            )
            return context
        except Exception as exc:  # noqa: BLE001 — wrapped into StageError
            raise StageError(self.name, "failed to read image metadata", exc) from exc
