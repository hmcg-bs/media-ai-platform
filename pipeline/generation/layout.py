"""Layout agent (Generation v1): fixes the collision bug found in the first
live smoke test (wayfinder issue #36) -- the compositor previously used a
static default layout with no idea where the product actually landed in the
Flux-Kontext-produced frame, so headline/body text visibly overlapped the
bottle. This agent looks at that *actual* image and reports where the
product is and which regions are genuinely empty, so the compositor can
reserve real space instead of guessing fixed fractions.

A Gemini vision call (reuses GenAIClient.extract_structured -- no new client
capability). Coordinates are normalized 0-1 fractions of the image, matching
ElementSpec's own convention. Not blindly trusted as ground truth: this
project's own established discipline (never treat an LLM's reported
measurement as fact without a check -- see ADR-008's color_reported vs
color_measured split) applies here too, so callers should treat these boxes
as a strong prior, not an exact guarantee, and the compositor still clips
text to stay within whatever box it's given."""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.generation.guide import GenerationGuide


class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class LayoutPlan(BaseModel):
    product_bbox: BoundingBox
    headline_zone: BoundingBox
    secondary_copy_zone: BoundingBox
    cta_zone: BoundingBox
    price_offer_zone: BoundingBox | None = None


def _headline_zone_hint(guide: GenerationGuide) -> str:
    """Surfaces the guide's own headline_zone directive (e.g. "avoid middle")
    as a hint the layout agent should weigh, not override -- avoiding the
    product still comes first."""
    for s in guide.visual_directives:
        if s.dimension == "headline_zone" and s.value:
            verdict = "prefer" if s.direction == "higher_is_better" else "avoid"
            return (
                f"Data suggests you should {verdict} placing the headline "
                f"in the '{s.value}' zone."
            )
    return "No strong zone preference from the data -- prioritize avoiding the product."


def plan_layout(
    genai_client: GenAIClient,
    *,
    model: str,
    background_and_product_image: bytes,
    guide: GenerationGuide,
) -> LayoutPlan:
    prompt = f"""This image shows a product on a background, with no text yet.
Identify:
1. product_bbox: the bounding box tightly around the visible product.
2. headline_zone, secondary_copy_zone, cta_zone, price_offer_zone (price_offer_zone
   only if there is a 5th genuinely separate empty area, else null): bounding
   boxes for placing ad copy, each fully within the empty background -- none of
   them may overlap product_bbox or each other.

All coordinates are fractions of the image (0.0-1.0), as x (left edge), y (top
edge), width, height.

{_headline_zone_hint(guide)}
"""
    return genai_client.extract_structured(
        model=model,
        prompt=prompt,
        image_bytes=background_and_product_image,
        image_mime_type="image/png",
        schema=LayoutPlan,
    )
