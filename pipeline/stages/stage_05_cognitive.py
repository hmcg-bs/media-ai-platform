"""Stage 5 — cognitive layer (Gemini via Vertex AI or Replicate).

Only features that genuinely need cognitive understanding are sent to an LLM.
Two tiers keep cost proportional to difficulty:

- Cheap tier: marketing psychology / hook / vibe.
- Deep tier: objects, spatial relationships, human detail.

Each tier degrades independently: if one call fails, its fields stay at schema
defaults and a ``fallback_applied`` event is logged — the stage does not abort.

Provider: Vertex AI Gemini (default) or Replicate google/gemini-3-flash (configurable).

(Stage 4 — landing-page scraping + visual verification — is deferred in v1, so
``product_verification`` is left null by the orchestrator.)
"""

from __future__ import annotations

import io
import time
from typing import Any

from PIL import Image
from pydantic import BaseModel, Field

from pipeline.clients.genai_client import GenAIClient
from pipeline.config import get_settings
from pipeline.logger import get_logger
from pipeline.models.output_schema import (
    HookFramework,
    HumanModelAnalysis,
    MarketingPsychology,
    PipelineContext,
    SpatialAndNestedObjects,
)
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)


class _DeepReasoningResult(BaseModel):
    """Combined response schema for the deep-tier call."""

    spatial_and_nested_objects: SpatialAndNestedObjects = Field(
        default_factory=SpatialAndNestedObjects
    )
    human_model_analysis: HumanModelAnalysis = Field(
        default_factory=HumanModelAnalysis
    )


_CHEAP_PROMPT = (
    "You are a performance-marketing analyst. Analyse this ad creative's copy and "
    "visual style. Identify the core marketing hook framework (must be one of: "
    "PAS, AIDA, Before/After, Testimonial, Direct Offer, Social Proof, Unknown), "
    "the primary value proposition, any authority/social-proof flags, the emoji count, and the "
    "approximate reading grade level. Respond only with JSON matching the schema."
)

_DEEP_PROMPT = (
    "You are an expert visual analyst. Examine this ad creative and extract: the "
    "primary product and its visual state, secondary props, directional spatial "
    "relationships between objects, any texture demonstration, and — if humans are "
    "present — per-model demographic, action, micro-expression, wardrobe and "
    "environmental modifiers. Respond only with JSON matching the schema."
)


class CognitiveStage(BaseStage):
    name = "stage_05_cognitive"

    def __init__(
        self,
        genai_client: GenAIClient | None = None,
        use_replicate: bool = False,
        settings: Any = None,
    ):
        self.settings = settings or get_settings()
        self.use_replicate = use_replicate

        if use_replicate:
            from pipeline.clients.replicate_client import ReplicateVisionClient

            self.client = ReplicateVisionClient()
        else:
            self.client = genai_client or GenAIClient()

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            image_bytes = context.image_bytes

            # Cheap tier — marketing psychology. Skip if a prior stage (Datalab extract)
            # already filled it, so the Datalab hook/value-prop wins in the full pipeline.
            mp = context.result.marketing_psychology
            if mp.hook_framework == HookFramework.UNKNOWN and not mp.primary_value_proposition:
                try:
                    if self.use_replicate:
                        context.result.marketing_psychology = self.client.extract_structured(
                            prompt=_CHEAP_PROMPT,
                            image_bytes=image_bytes,
                            schema=MarketingPsychology,
                        )
                    else:
                        image_bytes_resized, mime = self._resize_for_vertex(image_bytes)
                        context.result.marketing_psychology = self.client.extract_structured(
                            model=self.settings.gemini_cheap_model,
                            prompt=_CHEAP_PROMPT,
                            image_bytes=image_bytes_resized,
                            image_mime_type=mime,
                            schema=MarketingPsychology,
                        )
                except Exception as exc:  # noqa: BLE001 — degrade this tier only
                    logger.warning(
                        "fallback_applied", stage=self.name, tier="cheap", error=str(exc)
                    )
            else:
                logger.info(
                    "stage_skipped", stage=self.name, tier="cheap",
                    reason="marketing_psychology already set",
                )

            # Deep tier — spatial + human analysis.
            try:
                if self.use_replicate:
                    deep = self.client.extract_structured(
                        prompt=_DEEP_PROMPT,
                        image_bytes=image_bytes,
                        schema=_DeepReasoningResult,
                    )
                else:
                    image_bytes_resized, mime = self._resize_for_vertex(image_bytes)
                    deep = self.client.extract_structured(
                        model=self.settings.gemini_deep_model,
                        prompt=_DEEP_PROMPT,
                        image_bytes=image_bytes_resized,
                        image_mime_type=mime,
                        schema=_DeepReasoningResult,
                    )
                context.result.spatial_and_nested_objects = deep.spatial_and_nested_objects
                context.result.human_model_analysis = deep.human_model_analysis
            except Exception as exc:  # noqa: BLE001 — degrade this tier only
                logger.warning(
                    "fallback_applied", stage=self.name, tier="deep", error=str(exc)
                )

            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            return context
        except Exception as exc:  # noqa: BLE001 — only non-tier errors reach here
            raise StageError(self.name, "cognitive stage failed", exc) from exc

    def _resize_for_vertex(self, image_bytes: bytes) -> tuple[bytes, str]:
        """Resize to max longest-edge to cut token cost; return (bytes, mime)."""
        max_dim = self.settings.max_image_dimension_px
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            longest = max(img.size)
            if longest > max_dim:
                scale = max_dim / longest
                new_size = (round(img.width * scale), round(img.height * scale))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
        return buf.getvalue(), "image/jpeg"
