"""Blend/cohesion agent (Generation v1): a distinct, narrowly-scoped check
from the guide-adherence reviewer (reviewer.py) -- deliberately separate
rather than folded into one mega-review call, per the same "don't ask one
model call to judge too many unrelated things at once" concern that drove
the Stage 5 cognitive-extraction eval framework earlier in this project.
This agent's only job: does the code-composited text/CTA layer look like it
belongs with the AI-generated background/product, or like a sticker slapped
on top (lighting/color harmony, edge integration, shadow consistency) --
never whether the *content* is good, never whether directives were followed."""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient


class BlendReview(BaseModel):
    blends_well: bool
    issues: list[str]
    notes: str


def review_blend(
    genai_client: GenAIClient,
    *,
    model: str,
    ad_image_bytes: bytes,
) -> BlendReview:
    prompt = """Look ONLY at how visually unified this ad image is -- not
whether the copy is good, not whether it follows any marketing best
practice. Specifically judge:
- Does the text/button overlay look like it was composited on top (harsh
  edges, mismatched lighting, a color that clashes with the scene), or does
  it look like a natural part of one designed image?
- Are shadows/lighting direction on the product consistent with the
  background?
- Any visible seams, halos, or compositing artifacts around the product?

Do not comment on copy quality, layout choices, or marketing effectiveness --
only visual cohesion.
"""
    return genai_client.extract_structured(
        model=model,
        prompt=prompt,
        image_bytes=ad_image_bytes,
        image_mime_type="image/png",
        schema=BlendReview,
    )
