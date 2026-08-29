"""Style-reference agent (Generation v1, round 5): looks at real, currently-
successful ads (reference_ads.py) alongside the abstract guide directives,
and produces one concrete, actionable style brief -- grounding generation in
what actually wins, not statistics alone.

font_personality is a deliberate exception to "ground everything in real
data": nothing in this corpus's own extraction ever measured typeface
identity (Step 2 captured layout/size/alignment, not font family), so there
is no historical "this font wins" signal for this agent to defer to the way
there is for color/background. It's a qualitative read of the reference
images by the vision model -- kept honest by being visibly separate from the
guide's real statistical directives, not conflated with them.
"""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.generation.elements import FontPersonality
from pipeline.generation.guide import GenerationGuide
from pipeline.generation.reference_ads import ReferenceAd

_FALLBACK_BACKGROUND = (
    "a detailed, real-world lifestyle or contextual scene -- not a plain "
    "studio background"
)


class StyleBrief(BaseModel):
    background_treatment: str
    dominant_color_palette: list[str]
    text_needs_background_band: bool
    text_background_band_color_hex: str | None = None
    font_personality: FontPersonality
    cta_style_notes: str


def _guide_summary(guide: GenerationGuide) -> str:
    lines = [
        f"- {s.dimension}={s.value or ''}: {s.direction}" for s in guide.visual_directives[:10]
    ]
    return "\n".join(lines) if lines else "(no strong directives)"


def _reference_ads_summary(reference_ads: list[ReferenceAd]) -> str:
    lines = [
        f"- ad {a.ad_id} (score {a.composite_score:.2f}): "
        f"dominant_color={a.dominant_color}, background_style={a.background_style}, "
        f"hook={a.hook_framework}"
        for a in reference_ads
    ]
    return "\n".join(lines) if lines else "(no reference ads available)"


def derive_style_brief(
    genai_client: GenAIClient,
    *,
    model: str,
    reference_ads: list[ReferenceAd],
    guide: GenerationGuide,
) -> StyleBrief:
    """If `reference_ads` is empty (all candidates went stale -- see
    reference_ads.py), falls back to a text-only call grounded in the guide
    alone, with an explicit background_treatment that never defaults to
    "studio" -- the guide's own data says that's `lower_is_better`, and the
    old hardcoded fallback ignoring that is exactly the bug this module
    exists to fix."""
    directives_block = _guide_summary(guide)

    if not reference_ads:
        prompt = f"""No real reference ad images were available this run.
Using ONLY the statistical directives below (correlational signals from a
performance model, not hard rules), propose a concrete style brief for a
supplement ad. Do NOT default to a plain white studio background unless a
directive explicitly supports it -- that specific default has been
confirmed to underperform in this category's own data.

Directives:
{directives_block}

If no directive names a specific background style, use: {_FALLBACK_BACKGROUND}
"""
        return genai_client.extract_structured_text(model=model, prompt=prompt, schema=StyleBrief)

    prompt = f"""These images are real, currently-successful supplement ads,
ranked by a statistical performance model:
{_reference_ads_summary(reference_ads)}

Look at what they actually do visually -- background treatment, color
choices, whether text sits directly on the image or on its own colored
band, button/CTA styling.

Separately, here is what the same statistical model found correlates with
success in this category (correlational signals, not hard rules):
{directives_block}

Produce ONE concrete style brief for a NEW ad that could plausibly belong to
this same successful set -- grounded in what you actually observe in the
reference images above, not generic ad-design advice. If the reference ads
mostly use busy/detailed/contextual backgrounds rather than plain studio
backgrounds, say so explicitly in background_treatment.
"""
    images = [(ad.image_bytes, ad.image_mime_type) for ad in reference_ads]
    return genai_client.extract_structured_multi_image(
        model=model, prompt=prompt, images=images, schema=StyleBrief
    )
