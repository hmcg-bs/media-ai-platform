"""Orchestrates Generation v2's cold-start path end to end: guide extraction
-> directive-aligned reference-ad retrieval -> style-brief agent (guide-only,
text) -> copywriter agent -> product-bbox + deterministic layout planning
(layout_planner.py, computed ONCE before generation) -> masked background
inpaint (background.py, now prompt-guided by the pre-decided layout) ->
[deterministic-first duplicate-product detection + surgical repair
(duplicate_detection.py) -> lightweight layout validation] -> deterministic
compositing -> blend-cohesion agent + guide-adherence reviewer agent +
feature-fidelity agent, with a capped regeneration loop gated on all three.

Generation v2 (2026-08-30) restructures Generation v1's flow around two
findings, both confirmed live and documented in
docs/generation-failure-modes.md (bugs #6 and #12): (1) the product's
bounding box is knowable immediately after background-removal, so layout no
longer needs a vision call to search an already-generated frame -- it's
computed once, outside the retry loop, from the guide's own statistical
layout directives; and (2) duplicate-product hallucination is real and
probabilistic, so it's checked and surgically repaired directly rather than
hoped-for by the general review agents. See layout_planner.py and
duplicate_detection.py for the full reasoning behind each.

Round 7 (2026-08-29) moved reference ads out of the generation-input path
(style_reference.py no longer sees their images -- the guide's own
statistics are authoritative for anything they measure) and into a
post-generation comparison role (feature_fidelity.py) instead. See both
modules' docstrings for the full reasoning.

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
from pipeline.generation.background import generate_background_and_product, repair_duplicate_region
from pipeline.generation.blend import BlendReview, review_blend
from pipeline.generation.compositor import compose_ad
from pipeline.generation.copywriter import AdCopy, draft_copy
from pipeline.generation.duplicate_detection import (
    DuplicateDetectionResult,
    detect_duplicate_product,
)
from pipeline.generation.elements import AdSpec, ElementSpec, FontPersonality
from pipeline.generation.feature_fidelity import FeatureFidelityReview, review_feature_fidelity
from pipeline.generation.guide import GenerationGuide, extract_generation_guide
from pipeline.generation.layout import BoundingBox, LayoutPlan, LayoutValidation, validate_layout
from pipeline.generation.layout_planner import plan_layout_from_guide
from pipeline.generation.masking import compute_product_bbox
from pipeline.generation.reference_ads import ReferenceAd, get_top_reference_ads
from pipeline.generation.reviewer import AdReview, review_ad
from pipeline.generation.style_reference import StyleBrief, derive_style_brief
from pipeline.logger import get_logger

logger = get_logger(__name__)

MAX_REGENERATION_PASSES = 2  # settled during wayfinder charting (issue #36's Notes)
N_REFERENCE_ADS = 3
# Capped, not a guarantee: a surgical repair is a first line of defense
# against a detected duplicate, not certain to succeed. Runs within a single
# regeneration attempt, before falling back to the existing full-regen path.
MAX_SURGICAL_REPAIR_ATTEMPTS = 2


@dataclass
class GenerationResult:
    final_image_bytes: bytes
    ad_copy: AdCopy
    style_brief: StyleBrief
    layout_plan: LayoutPlan | None = None
    review_history: list[AdReview] = field(default_factory=list)
    blend_review_history: list[BlendReview] = field(default_factory=list)
    fidelity_review_history: list[FeatureFidelityReview] = field(default_factory=list)
    layout_validation_history: list[LayoutValidation] = field(default_factory=list)
    duplicate_detection_history: list[DuplicateDetectionResult] = field(default_factory=list)
    passes_used: int = 0
    ai_generated_disclosure: bool = True  # Meta's 2026 disclosure rule (issue #36's Notes)


def _layout_plan_zones(plan: LayoutPlan) -> list[BoundingBox]:
    zones = [plan.headline_zone, plan.secondary_copy_zone, plan.cta_zone]
    if plan.price_offer_zone is not None:
        zones.append(plan.price_offer_zone)
    return zones


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
    for every ad, every run) regardless of what the guide suggested --
    confirmed live as the reason every generated ad "looked the same." Now
    sourced from the style brief, which (as of Round 7) is itself derived
    from the guide's own statistical directives, not invented per-element."""
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
    reference_ads: list[ReferenceAd] | None = None,
    max_passes: int = MAX_REGENERATION_PASSES,
    cutout: bytes | None = None,
    product_bbox: BoundingBox | None = None,
    layout_plan: LayoutPlan | None = None,
    variant_hint: str | None = None,
) -> GenerationResult:
    """`cutout`/`product_bbox`/`layout_plan`: pass these to reuse geometry
    already computed elsewhere (generate_cold_start_ad_variants shares them
    across variants) instead of recomputing per call. `variant_hint`: folded
    into the copywriter prompt so each variant in a multi-variant batch gets
    genuinely different copy, not incidental LLM stochasticity."""
    settings = get_settings()
    genai_client = genai_client or GenAIClient()
    bg_remover_client = bg_remover_client or BackgroundRemoverClient()
    flux_fill_client = flux_fill_client or FluxFillClient()
    guide = guide or extract_generation_guide()

    # Retrieved regardless of whether style_brief is injected -- these ads no
    # longer feed the style brief (see style_reference.py), they're the
    # ground-truth exemplars feature_fidelity.py checks the final result
    # against, so they're needed even when style_brief is supplied directly.
    if reference_ads is None:
        reference_ads = get_top_reference_ads(guide, n=N_REFERENCE_ADS)
        logger.info(
            "generation_reference_ads_retrieved", n=len(reference_ads),
            alignment_scores=[a.alignment_score for a in reference_ads],
        )

    if style_brief is None:
        style_brief = derive_style_brief(
            genai_client, model=settings.gemini_deep_model, guide=guide,
        )
        logger.info(
            "generation_style_brief_derived",
            background=style_brief.background_treatment, font=style_brief.font_personality,
        )

    ad_copy = draft_copy(
        genai_client, model=settings.gemini_cheap_model,
        intention=intention, product_name=product_name, guide=guide,
        variant_hint=variant_hint,
    )
    logger.info("generation_copy_drafted", headline=ad_copy.headline)

    # Generation v2: the product's geometry is known before generation ever
    # runs (background-remover's alpha never changes after this point -- see
    # masking.py), so layout is computed ONCE here, deterministically, from
    # the guide's own layout directives -- not per-attempt from a vision
    # call searching an already-generated frame. Callers sharing this
    # geometry across multiple variants pass it in directly.
    if cutout is None:
        cutout = bg_remover_client.remove_background(product_photo_bytes)
    if product_bbox is None:
        product_bbox = compute_product_bbox(cutout)
    if layout_plan is None:
        layout_plan = plan_layout_from_guide(
            product_bbox, guide, has_price_offer=ad_copy.price_offer_text is not None,
        )
    logger.info("generation_layout_planned_from_guide", product_bbox=product_bbox.model_dump())

    background_and_product = generate_background_and_product(
        bg_remover_client, flux_fill_client, product_photo_bytes,
        intention=intention, guide=guide, style_brief=style_brief,
        cutout=cutout, keep_clear_zones=_layout_plan_zones(layout_plan),
    )
    logger.info("generation_background_product_done")

    review_history: list[AdReview] = []
    blend_history: list[BlendReview] = []
    fidelity_history: list[FeatureFidelityReview] = []
    layout_validation_history: list[LayoutValidation] = []
    duplicate_detection_history: list[DuplicateDetectionResult] = []
    final_bytes = b""
    for attempt in range(max_passes + 1):
        # Deterministic-first duplicate-product check, escalating to a
        # vision call only when ambiguous (duplicate_detection.py). A
        # surgical repair targets just the hallucinated region -- capped, not
        # a guarantee -- before falling back to the existing full-regen path
        # at the bottom of this loop.
        for _ in range(MAX_SURGICAL_REPAIR_ATTEMPTS):
            dup_result = detect_duplicate_product(
                bg_remover_client, genai_client, model=settings.gemini_deep_model,
                generated_image_bytes=background_and_product,
                original_product_bbox=product_bbox,
                original_product_photo_bytes=product_photo_bytes,
            )
            duplicate_detection_history.append(dup_result)
            if not dup_result.duplicate_detected:
                break
            logger.info(
                "generation_duplicate_detected", attempt=attempt,
                method=dup_result.detection_method,
            )
            background_and_product = repair_duplicate_region(
                flux_fill_client, background_and_product, dup_result.duplicate_bbox,
                guide, style_brief,
            )

        layout_validation = validate_layout(
            genai_client, model=settings.gemini_deep_model,
            background_and_product_image=background_and_product, layout_plan=layout_plan,
        )
        layout_validation_history.append(layout_validation)
        logger.info(
            "generation_layout_validated", attempt=attempt,
            zones_respected=layout_validation.zones_respected,
        )

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

        fidelity_review = review_feature_fidelity(
            genai_client, model=settings.gemini_deep_model,
            final_ad_image_bytes=final_bytes, reference_ads=reference_ads, guide=guide,
        )
        fidelity_history.append(fidelity_review)

        logger.info(
            "generation_review_complete", attempt=attempt,
            overall_pass=review.overall_pass, blends_well=blend_review.blends_well,
            fidelity_pass=fidelity_review.overall_fidelity_pass,
            regeneration_recommended=review.regeneration_recommended,
        )

        passed = (
            review.overall_pass
            and blend_review.blends_well
            and fidelity_review.overall_fidelity_pass
        )
        needs_regen = (
            review.regeneration_recommended
            or not blend_review.blends_well
            or not fidelity_review.overall_fidelity_pass
        )
        if passed or not needs_regen:
            break
        if attempt >= max_passes:
            break

        # Targeted-enough for v1: re-run the background/product edit with
        # whichever agent(s) flagged a problem folded into the prompt as
        # extra context, rather than a full from-scratch restart. layout_plan
        # itself is NOT recomputed here -- it's fixed for the whole run
        # (Generation v2), since it was derived from geometry, not from
        # whatever this particular re-edited frame happens to look like.
        reasons = [
            r for r in (
                review.regeneration_reason, blend_review.notes,
                None if fidelity_review.overall_fidelity_pass else fidelity_review.notes,
            ) if r
        ]
        background_and_product = generate_background_and_product(
            bg_remover_client, flux_fill_client, product_photo_bytes,
            intention=f"{intention} (fix: {'; '.join(reasons)})",
            guide=guide, style_brief=style_brief,
            cutout=cutout, keep_clear_zones=_layout_plan_zones(layout_plan),
        )

    return GenerationResult(
        final_image_bytes=final_bytes,
        ad_copy=ad_copy,
        style_brief=style_brief,
        layout_plan=layout_plan,
        review_history=review_history,
        blend_review_history=blend_history,
        fidelity_review_history=fidelity_history,
        layout_validation_history=layout_validation_history,
        duplicate_detection_history=duplicate_detection_history,
        passes_used=len(review_history) - 1,
    )


def generate_cold_start_ad_variants(
    product_photo_bytes: bytes,
    *,
    intention: str,
    product_name: str,
    n_variants: int = 3,
    guide: GenerationGuide | None = None,
    genai_client: GenAIClient | None = None,
    bg_remover_client: BackgroundRemoverClient | None = None,
    flux_fill_client: FluxFillClient | None = None,
    reference_ads: list[ReferenceAd] | None = None,
    max_passes: int = MAX_REGENERATION_PASSES,
) -> list[GenerationResult]:
    """Generates `n_variants` full candidate ads for human selection (the
    user's own confirmed decision: return all variants, never auto-pick).

    The guide's statistical findings (background style, color direction,
    layout geometry) are not "options" -- they're the model's real answer --
    so `guide`, `reference_ads`, and the geometry-driven `product_bbox`/
    `layout_plan` are computed ONCE and shared across every variant. Only the
    qualitative, statistically-unmeasured dimensions vary per variant: each
    gets its own `style_brief` (font_personality rotated explicitly across
    the full FontPersonality set, rather than relying on incidental LLM
    stochasticity to produce genuine diversity) and its own `ad_copy`.

    Cost note: this multiplies every expensive step (Flux Fill inpainting,
    every vision call in the regeneration loop) by `n_variants` -- only the
    steps above are shared."""
    settings = get_settings()
    genai_client = genai_client or GenAIClient()
    bg_remover_client = bg_remover_client or BackgroundRemoverClient()
    flux_fill_client = flux_fill_client or FluxFillClient()
    guide = guide or extract_generation_guide()

    if reference_ads is None:
        reference_ads = get_top_reference_ads(guide, n=N_REFERENCE_ADS)

    # Shared, computed once -- guide/geometry-driven, not per-variant.
    # has_price_offer=True is permissive: each variant's own copy may or may
    # not end up with price text, and _layout_from_plan already omits the
    # price_offer element harmlessly when a variant's copy has none, so
    # reserving the zone up front costs nothing for variants that skip it.
    cutout = bg_remover_client.remove_background(product_photo_bytes)
    product_bbox = compute_product_bbox(cutout)
    layout_plan = plan_layout_from_guide(product_bbox, guide, has_price_offer=True)

    font_rotation: list[FontPersonality] = [
        "clean_modern", "bold_condensed", "elegant_serif", "playful_dynamic",
    ]

    results: list[GenerationResult] = []
    for i in range(n_variants):
        variant_hint = (
            f"Variant {i + 1} of {n_variants} -- vary creative expression from the other variants."
        )
        style_brief = derive_style_brief(
            genai_client, model=settings.gemini_deep_model, guide=guide,
            variant_hint=variant_hint,
        )
        style_brief.font_personality = font_rotation[i % len(font_rotation)]
        logger.info(
            "generation_variant_style_brief_derived", variant=i,
            background=style_brief.background_treatment, font=style_brief.font_personality,
        )

        result = generate_cold_start_ad(
            product_photo_bytes, intention=intention, product_name=product_name,
            guide=guide, genai_client=genai_client, bg_remover_client=bg_remover_client,
            flux_fill_client=flux_fill_client, style_brief=style_brief,
            reference_ads=reference_ads, max_passes=max_passes,
            cutout=cutout, product_bbox=product_bbox, layout_plan=layout_plan,
            variant_hint=variant_hint,
        )
        results.append(result)

    return results
