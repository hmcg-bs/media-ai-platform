"""Layout models (Generation v1/v2). Originally (v1) this module's own
`plan_layout` was a Gemini vision call that looked at an *already-generated*
frame and searched for empty space -- a fix for the collision bug found in
the first live smoke test (wayfinder issue #36), where a static default
layout had no idea where the product actually landed and text visibly
overlapped it.

Generation v2 (2026-08-30) replaces that search with layout_planner.py's
deterministic `plan_layout_from_guide`: the product's bounding box is known
immediately after background-removal, well before Flux Fill ever runs, so
there's no need to wait for a generated image and guess. `plan_layout` is
retired; what's left of the vision call is demoted to `validate_layout`
below -- a cheaper check of whether a specific generated frame still
respects the *already-decided* plan, not a fresh search. `BoundingBox` and
`LayoutPlan` are unchanged and still the shared contract every other module
in this package uses."""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient


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


class LayoutValidation(BaseModel):
    zones_respected: bool
    notes: str


def _zone_lines(plan: LayoutPlan) -> str:
    lines = [
        f"- headline_zone: x={plan.headline_zone.x:.2f}, y={plan.headline_zone.y:.2f}, "
        f"width={plan.headline_zone.width:.2f}, height={plan.headline_zone.height:.2f}",
        f"- secondary_copy_zone: x={plan.secondary_copy_zone.x:.2f}, "
        f"y={plan.secondary_copy_zone.y:.2f}, width={plan.secondary_copy_zone.width:.2f}, "
        f"height={plan.secondary_copy_zone.height:.2f}",
        f"- cta_zone: x={plan.cta_zone.x:.2f}, y={plan.cta_zone.y:.2f}, "
        f"width={plan.cta_zone.width:.2f}, height={plan.cta_zone.height:.2f}",
    ]
    if plan.price_offer_zone is not None:
        z = plan.price_offer_zone
        lines.append(
            f"- price_offer_zone: x={z.x:.2f}, y={z.y:.2f}, "
            f"width={z.width:.2f}, height={z.height:.2f}"
        )
    return "\n".join(lines)


def validate_layout(
    genai_client: GenAIClient,
    *,
    model: str,
    background_and_product_image: bytes,
    layout_plan: LayoutPlan,
) -> LayoutValidation:
    """Confirms whether a plan already decided from the product's own
    geometry (layout_planner.plan_layout_from_guide) still holds for this
    specific generated frame -- not a fresh search. All coordinates are
    fractions of the image (0.0-1.0), matching LayoutPlan's own convention."""
    prompt = f"""This image shows a product on a generated background, with
no text yet. A layout has already been decided ahead of time from the
product's own geometry -- do not propose a different layout. Only confirm
whether each zone below is still visually clear (no part of the product, or
any other object, extends into it):

{_zone_lines(layout_plan)}

All coordinates are fractions of the image (0.0-1.0).
"""
    return genai_client.extract_structured(
        model=model,
        prompt=prompt,
        image_bytes=background_and_product_image,
        image_mime_type="image/png",
        schema=LayoutValidation,
    )
