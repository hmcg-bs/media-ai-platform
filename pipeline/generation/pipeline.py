"""Orchestrates Generation v1's cold-start path end to end: guide extraction
-> reference-ad retrieval -> style-brief agent -> copywriter agent -> masked
background inpaint (Round 6: background-remover + Flux Fill, see
pipeline/generation/background.py) -> layout agent -> deterministic
compositing -> blend-cohesion agent + guide-adherence reviewer agent, with a
capped regeneration loop.

This is the cold-start entry point specifically (raw product photo +
intention, no existing draft) -- the re-render path (existing draft ad ->
Critique -> targeted/full regeneration) is wayfinder issue #41's own open
question (how Critique's output maps to a specific element) and isn't wired
here yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pipeline.clients.genai_client import GenAIClient
from pipeline.clients.replicate_client import BackgroundRemoverClient, FluxFillClient
from pipeline.config import get_settings
from pipeline.generation.background import generate_background_and_product
from pipeline.generation.blend import BlendReview, review_blend
from pipeline.generation.compositor import compose_ad
from pipeline.generation.copywriter import AdCopy, draft_copy
from pipeline.generation.elements import AdSpec, ElementSpec
from pipeline.generation.guide import GenerationGuide, extract_generation_guide
from pipeline.generation.layout import LayoutPlan, plan_layout
from pipeline.generation.reference_ads import get_top_reference_ads
from pipeline.generation.reviewer import AdReview, review_ad
from pipeline.generation.style_reference import StyleBrief, derive_style_brief
from pipeline.logger import get_logger

logger = get_logger(__name__)

MAX_REGENERATION_PASSES = 2  # settled during wayfinder charting (issue #36's Notes)
N_REFERENCE_ADS = 3


@dataclass
class GenerationResult:
    final_image_bytes: bytes
    ad_copy: AdCopy
    style_brief: StyleBrief
    review_history: list[AdReview] = field(default_factory=list)
    blend_review_history: list[BlendReview] = field(default_factory=list)
    passes_used: int = 0
    ai_generated_disclosure: bool = True  # Meta's 2026 disclosure rule (issue #36's Notes)


def _layout_from_plan(
    copy_text: AdCopy, plan: LayoutPlan, style_brief: StyleBrief
) -> list[ElementSpec]:
    """Converts the layout agent's reserved zones into ElementSpecs -- fixes
    the collision bug the first live smoke test found (wayfinder issue #36):
    the old static default layout had no idea where the product actually
    landed, so headline/body text visibly overlapped the bottle. These boxes
    come from looking at the *actual* generated frame instead of guessing
    fixed fractions.

    Round 5 fix: font/colors used to be hardcoded (#ffffff/#f0f0f0/#1a1a1a
    for every ad, every run) regardless of what the guide or any reference
    ad suggested -- confirmed live as the reason every generated ad "looked
    the same." Now sourced from the style brief, which is itself grounded in
    real reference ads, not invented per-element."""
    band = style_brief.text_needs_background_band
    band_color = style_brief.text_background_band_color_hex
    font = style_brief.font_personality

    elements = [
        ElementSpec(
            element_type="headline", text=copy_text.headline,
            x=plan.headline_zone.x, y=plan.headline_zone.y,
            width=plan.headline_zone.width, height=plan.headline_zone.height,
            z_order=1, font_personality=font,
            background_band=band, background_band_color_hex=band_color,
        ),
        ElementSpec(
            element_type="secondary_copy", text=copy_text.secondary_copy,
            x=plan.secondary_copy_zone.x, y=plan.secondary_copy_zone.y,
            width=plan.secondary_copy_zone.width, height=plan.secondary_copy_zone.height,
            z_order=1, font_personality=font,
            background_band=band, background_band_color_hex=band_color,
        ),
        ElementSpec(
            element_type="cta_graphic", text=copy_text.cta_text,
            x=plan.cta_zone.x, y=plan.cta_zone.y,
            width=plan.cta_zone.width, height=plan.cta_zone.height,
            z_order=2, uppercase=True, font_personality=font,
        ),
    ]
    if copy_text.price_offer_text and plan.price_offer_zone:
        elements.append(
            ElementSpec(
                element_type="price_offer", text=copy_text.price_offer_text,
                x=plan.price_offer_zone.x, y=plan.price_offer_zone.y,
                width=plan.price_offer_zone.width, height=plan.price_offer_zone.height,
                z_order=1, font_personality=font,
                background_band=band, background_band_color_hex=band_color,
            )
        )
    return elements


def generate_cold_start_ad(
    product_photo_bytes: bytes,
    *,
    intention: str,
    product_name: str,
    guide: GenerationGuide | None = None,
    genai_client: GenAIClient | None = None,
    bg_remover_client: BackgroundRemoverClient | None = None,
    flux_fill_client: FluxFillClient | None = None,
    style_brief: StyleBrief | None = None,
    max_passes: int = MAX_REGENERATION_PASSES,
) -> GenerationResult:
    settings = get_settings()
    genai_client = genai_client or GenAIClient()
    bg_remover_client = bg_remover_client or BackgroundRemoverClient()
    flux_fill_client = flux_fill_client or FluxFillClient()
    guide = guide or extract_generation_guide()

    if style_brief is None:
        reference_ads = get_top_reference_ads(n=N_REFERENCE_ADS)
        logger.info("generation_reference_ads_retrieved", n=len(reference_ads))
        style_brief = derive_style_brief(
            genai_client, model=settings.gemini_deep_model,
            reference_ads=reference_ads, guide=guide,
        )
        logger.info(
            "generation_style_brief_derived",
            background=style_brief.background_treatment, font=style_brief.font_personality,
        )

    ad_copy = draft_copy(
        genai_client, model=settings.gemini_cheap_model,
        intention=intention, product_name=product_name, guide=guide,
    )
    logger.info("generation_copy_drafted", headline=ad_copy.headline)

    background_and_product = generate_background_and_product(
        bg_remover_client, flux_fill_client, product_photo_bytes,
        intention=intention, guide=guide, style_brief=style_brief,
    )
    logger.info("generation_background_product_done")

    review_history: list[AdReview] = []
    blend_history: list[BlendReview] = []
    final_bytes = b""
    for attempt in range(max_passes + 1):
        layout_plan = plan_layout(
            genai_client, model=settings.gemini_deep_model,
            background_and_product_image=background_and_product, guide=guide,
        )
        logger.info("generation_layout_planned", attempt=attempt)

        spec = AdSpec(
            background_and_product_image=background_and_product,
            elements=_layout_from_plan(ad_copy, layout_plan, style_brief),
        )
        final_bytes = compose_ad(spec)

        blend_review = review_blend(
            genai_client, model=settings.gemini_deep_model, ad_image_bytes=final_bytes,
        )
        blend_history.append(blend_review)

        review = review_ad(
            genai_client, model=settings.gemini_deep_model,
            ad_image_bytes=final_bytes, guide=guide,
        )
        review_history.append(review)
        logger.info(
            "generation_review_complete", attempt=attempt,
            overall_pass=review.overall_pass, blends_well=blend_review.blends_well,
            regeneration_recommended=review.regeneration_recommended,
        )

        passed = review.overall_pass and blend_review.blends_well
        needs_regen = review.regeneration_recommended or not blend_review.blends_well
        if passed or not needs_regen:
            break
        if attempt >= max_passes:
            break

        # Targeted-enough for v1: re-run the background/product edit with
        # whichever agent(s) flagged a problem folded into the prompt as
        # extra context, rather than a full from-scratch restart. The next
        # loop iteration re-runs the layout agent too, since a re-edited
        # frame may place the product differently.
        reasons = [r for r in (review.regeneration_reason, blend_review.notes) if r]
        background_and_product = generate_background_and_product(
            bg_remover_client, flux_fill_client, product_photo_bytes,
            intention=f"{intention} (fix: {'; '.join(reasons)})",
            guide=guide, style_brief=style_brief,
        )

    return GenerationResult(
        final_image_bytes=final_bytes,
        ad_copy=ad_copy,
        style_brief=style_brief,
        review_history=review_history,
        blend_review_history=blend_history,
        passes_used=len(review_history) - 1,
    )
