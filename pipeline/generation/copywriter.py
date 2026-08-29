"""Copywriter agent: drafts the ad's actual text content (headline, secondary
copy, CTA label) from the client's stated intention + product info, informed
by the generation guide's copy-style/hook-framework directives. A text-only
Gemini call (reuses GenAIClient.extract_structured_text -- no new client
method needed); the *rendering* of this text stays deterministic code
(compositor.py), matching this project's ADR-006 default rather than a
generative text-in-image model (see wayfinder issue #38's Q5, unresolved)."""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.generation.guide import GenerationGuide


class AdCopy(BaseModel):
    headline: str
    secondary_copy: str
    cta_text: str
    price_offer_text: str | None = None


def _guide_directives_as_prompt_lines(guide: GenerationGuide) -> str:
    lines: list[str] = []
    for s in guide.visual_directives:
        label = f"{s.dimension}" + (f"={s.value}" if s.value else "")
        lines.append(f"- {label}: {s.direction.replace('_', ' ')} (source: {s.source})")
    for s in guide.copy_style_directives:
        lines.append(f"- {s.dimension}: {s.direction.replace('_', ' ')} (source: {s.source})")
    return "\n".join(lines) if lines else "(no reliable directives extracted)"


def draft_copy(
    genai_client: GenAIClient,
    *,
    model: str,
    intention: str,
    product_name: str,
    guide: GenerationGuide,
) -> AdCopy:
    """One structured text call. `guide`'s directives are descriptive
    ("cta_type=order tends to correlate with higher_is_better") not
    prescriptive commands -- the prompt says so explicitly so the model
    doesn't over-fit to a single historical correlation."""
    prompt = f"""You are writing ad copy for a supplement product ad.

Product: {product_name}
Client's stated intention: {intention}

Below are data-driven directives from a statistical model of what correlates
with ad performance in this product category. These are correlational
signals from past ads, not hard rules -- use them as informed guidance, not
literal instructions:

{_guide_directives_as_prompt_lines(guide)}

Write:
- headline: a short, punchy primary hook (under 60 characters)
- secondary_copy: 1-2 supporting sentences (benefit/claim/ingredient focus)
- cta_text: a short call-to-action label (2-4 words, e.g. "Shop Now", "Order Today")
- price_offer_text: a short price/offer callout if the intention mentions pricing, else null
"""
    return genai_client.extract_structured_text(model=model, prompt=prompt, schema=AdCopy)
