"""Style-brief agent (Generation v1). Round 7 (2026-08-29) rewrite: this
module used to feed real reference-ad *images* into a vision call and ask it
to independently read off "background treatment, color choices" -- but those
are dimensions the guide (guide.py) already has a rigorous, larger-sample
SHAP/Cox answer for (`dominant_color`, `background_style`,
`contrast_ratio_type`, ...). Letting a vision model re-derive an already-
measured dimension from 3 (often 0, given corpus image staleness) cherry-
picked images let a small, uncontrolled qualitative sample override or
dilute a real statistical one -- exactly the kind of ungrounded LLM judgment
this project is built to avoid wherever a deterministic/statistical answer
already exists (CLAUDE.md's own stated principle).

This agent's job is now narrower and text-only, in two parts:
1. Translate each measured, direction-reliable guide directive into concrete,
   renderable creative language (e.g. `background_style=Busy` -> a specific
   kind of cluttered real-world scene) -- anchored to the directive as
   ground truth, not re-derived from anything else.
2. Make a qualitative call on `font_personality`, the one dimension Step 2's
   extraction never measures at all (no font-family feature exists anywhere
   in the schema) -- kept honestly separate from the guide's real
   statistical directives, never conflated with them.

Reference ads have not been dropped from the pipeline -- they moved to
feature_fidelity.py, which uses them *after* generation to check whether the
output actually replicates the guide's directives, rather than *before*
generation to (mis)inform what those directives even are.
"""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.generation.elements import FontPersonality
from pipeline.generation.guide import GenerationGuide


class StyleBrief(BaseModel):
    background_treatment: str
    dominant_color_palette: list[str]
    text_needs_background_band: bool
    text_background_band_color_hex: str | None = None
    font_personality: FontPersonality
    cta_style_notes: str


def _directives_block(guide: GenerationGuide) -> str:
    lines = [
        f"- {s.dimension}={s.value or ''}: {s.direction}" for s in guide.visual_directives[:10]
    ]
    return "\n".join(lines) if lines else "(no strong directives)"


def derive_style_brief(
    genai_client: GenAIClient,
    *,
    model: str,
    guide: GenerationGuide,
) -> StyleBrief:
    """Text-only call -- no reference-ad images. The guide's directives are
    the sole source of truth for every dimension they cover; the model's job
    is to translate them into concrete creative language and fill in
    font_personality, which has no statistical source to defer to."""
    prompt = f"""Translate the data-driven directives below (correlational
signals from a statistical model of what predicts ad performance in this
category -- not hard rules, but the authoritative source for any dimension
they name) into ONE concrete, renderable style brief for a supplement ad.

Directives:
{_directives_block(guide)}

Rules:
- Every directive above must be reflected concretely in your answer -- do
  not substitute your own judgment for a dimension the directives already
  cover (e.g. if a directive names a background_style or dominant_color,
  your background_treatment/dominant_color_palette must embody that
  directive, not some other stylistic choice).
- Do NOT default to a plain white studio background unless a directive
  explicitly supports it -- that specific default has been confirmed to
  underperform in this category's own data.
- font_personality has no statistical directive backing it (Step 2's
  extraction never measures typeface) -- use your own best qualitative
  judgment for what would suit this product and the directives above.
- If no directive names a specific background style, use: a detailed,
  real-world lifestyle or contextual scene -- not a plain studio background.
"""
    return genai_client.extract_structured_text(model=model, prompt=prompt, schema=StyleBrief)
