"""Feature-fidelity review (Generation v1, Round 7): checks whether the
final generated ad actually replicates the visual feature pattern the
statistical model found in real winning ads.

This is the comparison role reference ads were rescoped into once
style_reference.py stopped using them to *inform* generation (see that
module's own docstring): reference_ads.py now selects ads that are
independently confirmed, via their own extracted feature values, to embody
the guide's top directives -- not just any high-scoring ad. Those become
ground-truth exemplars here, compared against the generated result in one
multi-image vision call, rather than being fed into the generation prompt
itself where a small, uncontrolled sample could override the guide's own
larger-sample statistical answer.
"""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.generation.guide import GenerationGuide
from pipeline.generation.reference_ads import ReferenceAd


class FeatureFidelityReview(BaseModel):
    checked: bool
    replicated_directives: list[str]
    missed_directives: list[str]
    overall_fidelity_pass: bool
    notes: str


class _FidelityLLMResult(BaseModel):
    """The vision model never decides `checked` -- that's a code-level fact
    (were reference ads even available this run), not a judgment call."""

    replicated_directives: list[str]
    missed_directives: list[str]
    overall_fidelity_pass: bool
    notes: str


def _directives_summary(guide: GenerationGuide) -> str:
    lines = [
        f"- {s.dimension}={s.value}: should be {s.direction.replace('_', ' ')}"
        for s in guide.visual_directives[:10] if s.value
    ]
    return "\n".join(lines) if lines else "(no checkable directives)"


def review_feature_fidelity(
    genai_client: GenAIClient,
    *,
    model: str,
    final_ad_image_bytes: bytes,
    reference_ads: list[ReferenceAd],
    guide: GenerationGuide,
) -> FeatureFidelityReview:
    """Compares the final generated ad (last image) against the reference
    ads (preceding images) -- real ads reference_ads.py has already
    confirmed embody the guide's own top directives. Degrades gracefully --
    never raises, never blocks generation -- when no reference ads were
    fetchable this run (corpus image staleness): returns `checked=False`
    rather than fabricating a verdict with nothing to compare against."""
    if not reference_ads:
        return FeatureFidelityReview(
            checked=False, replicated_directives=[], missed_directives=[],
            overall_fidelity_pass=True,
            notes="No reference ads were fetchable this run -- fidelity not checked.",
        )

    prompt = f"""The first {len(reference_ads)} image(s) are real, currently-
successful ads, independently confirmed to embody these data-driven
directives (correlational signals from a statistical model of ad
performance in this category):

{_directives_summary(guide)}

The LAST image is a newly generated ad, meant to belong to the same
successful pattern. For each directive above, judge whether the generated
ad visually replicates the same trait the reference ads share. List which
directives were replicated and which were missed. Do not comment on copy
content, layout choices, or anything the directives above don't name --
only whether the generated ad's visual features match the pattern the real
reference ads demonstrate.
"""
    images = [(ad.image_bytes, ad.image_mime_type) for ad in reference_ads]
    images.append((final_ad_image_bytes, "image/png"))
    llm_result = genai_client.extract_structured_multi_image(
        model=model, prompt=prompt, images=images, schema=_FidelityLLMResult,
    )
    return FeatureFidelityReview(checked=True, **llm_result.model_dump())
