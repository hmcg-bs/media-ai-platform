"""Full-ad-level reviewer agent (Generation v1): rates the assembled ad
against the generation guide -- "an agent that rates whether the ad follows
the guide from the model," per the map's own framing. Reuses
GenAIClient.extract_structured (vision-in, structured-JSON-out), the same
mechanism already proven throughout Step 2's cognitive stage -- no new client
capability needed."""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.generation.guide import GenerationGuide


class DirectiveCheck(BaseModel):
    directive: str
    followed: bool
    note: str


class AdReview(BaseModel):
    overall_pass: bool
    directive_checks: list[DirectiveCheck]
    visual_quality_issues: list[str]
    regeneration_recommended: bool
    regeneration_reason: str | None = None


def _guide_directives_summary(guide: GenerationGuide) -> str:
    lines = []
    for s in guide.visual_directives:
        label = f"{s.dimension}" + (f"={s.value}" if s.value else "")
        lines.append(f"- {label}: should be {s.direction.replace('_', ' ')}")
    return "\n".join(lines) if lines else "(no directives to check)"


def review_ad(
    genai_client: GenAIClient,
    *,
    model: str,
    ad_image_bytes: bytes,
    guide: GenerationGuide,
) -> AdReview:
    prompt = f"""You are reviewing a generated supplement ad image against a
set of data-driven directives (correlational signals from a statistical model
of past ad performance, not hard design rules).

Directives to check against this image:
{_guide_directives_summary(guide)}

For each directive, judge whether the ad visually follows it (e.g. if a
directive says "dominant_color=green: lower is better", check whether the ad
avoids a green-dominant palette). Also flag any general visual-quality
problems (garbled/illegible text, a warped or duplicated product, an obviously
unnatural composite, awkward cropping). Recommend regeneration only for a
real defect -- not for stylistic disagreement with a low-magnitude directive.
"""
    return genai_client.extract_structured(
        model=model,
        prompt=prompt,
        image_bytes=ad_image_bytes,
        image_mime_type="image/png",
        schema=AdReview,
    )
