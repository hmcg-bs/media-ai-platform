"""Tests for pipeline/generation/pipeline.py -- the cold-start orchestration
loop, using injected fake genai/flux/bg-remover clients (no real API calls).

Generation v2 (2026-08-30) restructured this flow: layout is now computed
ONCE, deterministically, before the retry loop (layout_planner.py) instead
of via a per-attempt vision search (layout.py's old plan_layout); each
attempt also runs a deterministic-first duplicate-product check
(duplicate_detection.py) with a capped surgical-repair sub-loop before
composing. Focuses on the regeneration-loop decision logic (when does it
stop, when does it retry), _layout_from_plan's conversion from LayoutPlan
zones to ElementSpecs, and the new duplicate-detection/layout-planning
wiring."""

from __future__ import annotations

import io

from PIL import Image

from pipeline.generation.blend import BlendReview
from pipeline.generation.copywriter import AdCopy
from pipeline.generation.duplicate_detection import _DuplicateVisionResult
from pipeline.generation.feature_fidelity import _FidelityLLMResult
from pipeline.generation.guide import GenerationGuide
from pipeline.generation.layout import BoundingBox, LayoutPlan, LayoutValidation
from pipeline.generation.pipeline import (
    _layout_from_plan,
    generate_cold_start_ad,
    generate_cold_start_ad_variants,
)
from pipeline.generation.reference_ads import ReferenceAd
from pipeline.generation.reviewer import AdReview
from pipeline.generation.style_reference import StyleBrief


def _blank_png() -> bytes:
    img = Image.new("RGB", (100, 100), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rgba_cutout_bytes(
    size: int = 100, square: tuple[int, int, int, int] = (30, 20, 70, 80)
) -> bytes:
    """A single product-shaped opaque blob on an otherwise transparent
    canvas -- used as the bg-remover's cutout for both the original product
    photo and (by default, since the fake ignores its input) the generated
    image, so compute_product_bbox and duplicate_detection's "no duplicate"
    path both have real, meaningful alpha to work with."""
    x0, y0, x1, y1 = square
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x in range(x0, x1):
        for y in range(y0, y1):
            img.putpixel((x, y), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rgba_with_duplicate(
    size: int = 100,
    original: tuple[int, int, int, int] = (30, 20, 70, 80),
    # A second blob closely matching the original's size/aspect ratio
    # (width=29 vs 40, height=44 vs 60 -- same ~0.66 aspect), placed with a
    # 1px gap (x1=29, original starts at x0=30) so connected-component
    # labeling treats them as two separate blobs, not one merged region.
    extra: tuple[int, int, int, int] = (0, 20, 29, 64),
) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x0, y0, x1, y1 in (original, extra):
        for x in range(x0, x1):
            for y in range(y0, y1):
                img.putpixel((x, y), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _empty_guide() -> GenerationGuide:
    return GenerationGuide(
        visual_directives=[], copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


def _style_brief() -> StyleBrief:
    return StyleBrief(
        background_treatment="a busy lifestyle scene",
        dominant_color_palette=["#204060", "#f0f0f0"],
        text_needs_background_band=False,
        text_background_band_color_hex=None,
        font_personality="clean_modern",
        cta_style_notes="rounded, high-contrast",
    )


class TestLayoutFromPlan:
    def _layout_plan(self) -> LayoutPlan:
        return LayoutPlan(
            product_bbox=BoundingBox(x=0.3, y=0.3, width=0.4, height=0.5),
            headline_zone=BoundingBox(x=0.05, y=0.05, width=0.9, height=0.15),
            secondary_copy_zone=BoundingBox(x=0.05, y=0.85, width=0.9, height=0.1),
            cta_zone=BoundingBox(x=0.3, y=0.9, width=0.4, height=0.08),
            price_offer_zone=None,
        )

    def test_elements_positioned_at_plan_zones_not_static_defaults(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        plan = self._layout_plan()
        elements = _layout_from_plan(copy, plan, _style_brief())

        headline = next(e for e in elements if e.element_type == "headline")
        assert headline.x == plan.headline_zone.x
        assert headline.y == plan.headline_zone.y
        assert headline.width == plan.headline_zone.width

    def test_price_offer_omitted_when_no_zone_or_no_text(self):
        copy_no_text = AdCopy(
            headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None
        )
        plan = self._layout_plan()
        elements = _layout_from_plan(copy_no_text, plan, _style_brief())
        assert not any(e.element_type == "price_offer" for e in elements)

        copy_with_text = AdCopy(
            headline="H", secondary_copy="S", cta_text="Shop", price_offer_text="$19.99"
        )
        elements2 = _layout_from_plan(copy_with_text, plan, _style_brief())  # no price zone
        assert not any(e.element_type == "price_offer" for e in elements2)

    def test_font_personality_and_band_sourced_from_style_brief(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        plan = self._layout_plan()
        brief = StyleBrief(
            background_treatment="x", dominant_color_palette=["#000000"],
            text_needs_background_band=True, text_background_band_color_hex="#ffffff",
            font_personality="elegant_serif", cta_style_notes="",
        )
        elements = _layout_from_plan(copy, plan, brief)
        headline = next(e for e in elements if e.element_type == "headline")
        assert headline.font_personality == "elegant_serif"
        assert headline.background_band is True
        assert headline.background_band_color_hex == "#ffffff"


class _FakeInlineData:
    def __init__(self, data: bytes):
        self.data = data


class _FakePart:
    def __init__(self, inline_data=None):
        self.inline_data = inline_data


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


def _passing_fidelity_result() -> _FidelityLLMResult:
    return _FidelityLLMResult(
        replicated_directives=[], missed_directives=[],
        overall_fidelity_pass=True, notes="",
    )


def _passing_layout_validation() -> LayoutValidation:
    return LayoutValidation(zones_respected=True, notes="")


class _FakeModels:
    """Routes generate_content calls by schema type in config, mimicking real
    google-genai dispatch closely enough for these orchestration tests."""

    def __init__(
        self, copy: AdCopy, blend: BlendReview, review: AdReview,
        fidelity: _FidelityLLMResult | None = None,
        layout_validation: LayoutValidation | None = None,
        duplicate_vision: _DuplicateVisionResult | None = None,
        style_brief: StyleBrief | None = None,
    ):
        self._copy, self._blend, self._review = copy, blend, review
        self._fidelity = fidelity or _passing_fidelity_result()
        self._layout_validation = layout_validation or _passing_layout_validation()
        self._duplicate_vision = duplicate_vision
        self._style_brief = style_brief or _style_brief()
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        config = kwargs.get("config")
        schema = getattr(config, "response_schema", None) if config is not None else None
        if schema is AdCopy:
            return _FakeStructuredResponse(self._copy)
        if schema is LayoutValidation:
            return _FakeStructuredResponse(self._layout_validation)
        if schema is BlendReview:
            return _FakeStructuredResponse(self._blend)
        if schema is AdReview:
            return _FakeStructuredResponse(self._review)
        if schema is _FidelityLLMResult:
            return _FakeStructuredResponse(self._fidelity)
        if schema is StyleBrief:
            # A fresh copy per call, mirroring the real SDK's behavior of
            # parsing a new instance each time -- pipeline.py mutates a
            # variant's style_brief.font_personality in place, and reusing
            # one shared instance here would let later variants silently
            # overwrite earlier ones' results.
            return _FakeStructuredResponse(self._style_brief.model_copy())
        if schema is _DuplicateVisionResult:
            if self._duplicate_vision is None:
                raise AssertionError("unexpected duplicate-vision escalation in this test")
            return _FakeStructuredResponse(self._duplicate_vision)
        raise AssertionError(f"unexpected schema in test fake: {schema}")


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


class _FakeBgRemoverClient:
    """Accepts either a single cutout (always returned) or a sequence of
    cutouts consumed one per call (holding the last once exhausted) -- the
    sequence form lets a test script "the generated image now has a
    duplicate blob" for one specific call in the loop."""

    def __init__(self, cutout_bytes_or_sequence):
        if isinstance(cutout_bytes_or_sequence, (bytes, bytearray)):
            cutout_bytes_or_sequence = [cutout_bytes_or_sequence]
        self.sequence = list(cutout_bytes_or_sequence)
        self.calls: list[bytes] = []

    def remove_background(self, image_bytes: bytes) -> bytes:
        self.calls.append(image_bytes)
        idx = min(len(self.calls) - 1, len(self.sequence) - 1)
        return self.sequence[idx]


class _FakeFluxFillClient:
    def __init__(self, image_bytes: bytes):
        self.image_bytes = image_bytes
        self.calls: list[tuple] = []

    def inpaint(self, image_bytes, mask_bytes, prompt, **kwargs):
        self.calls.append((image_bytes, mask_bytes, prompt))
        return self.image_bytes


def _wrap_genai(fake_models: _FakeModels):
    from pipeline.clients.genai_client import GenAIClient

    return GenAIClient(client=_FakeGenAIClient(fake_models))


class TestGenerateColdStartAd:
    def test_stops_after_first_pass_when_review_and_blend_both_pass(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        fake_models = _FakeModels(copy, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        result = generate_cold_start_ad(
            _blank_png(), intention="test", product_name="Test Product",
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            style_brief=_style_brief(), reference_ads=[], max_passes=2,
        )

        assert result.passes_used == 0
        assert len(result.review_history) == 1
        assert len(flux_fill_client.calls) == 1  # only the initial background/product edit
        assert result.layout_plan is not None
        assert not any(d.duplicate_detected for d in result.duplicate_detection_history)

    def test_retries_up_to_max_passes_when_review_keeps_failing(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=False, directive_checks=[], visual_quality_issues=["bad"],
            regeneration_recommended=True, regeneration_reason="looks bad",
        )
        fake_models = _FakeModels(copy, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        result = generate_cold_start_ad(
            _blank_png(), intention="test", product_name="Test Product",
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            style_brief=_style_brief(), reference_ads=[], max_passes=2,
        )

        assert result.passes_used == 2
        assert len(result.review_history) == 3  # initial + 2 retries
        assert len(flux_fill_client.calls) == 3  # initial edit + 2 retry edits

    def test_regenerates_when_blend_fails_even_if_review_passes(self):
        """A passing guide-adherence review alone must not short-circuit the
        loop if the blend agent found a cohesion problem -- they're
        deliberately separate, independent gates."""
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=False, issues=["seam visible"], notes="bad seam")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        fake_models = _FakeModels(copy, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        result = generate_cold_start_ad(
            _blank_png(), intention="test", product_name="Test Product",
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            style_brief=_style_brief(), reference_ads=[], max_passes=1,
        )

        assert result.passes_used == 1
        assert len(flux_fill_client.calls) == 2

    def test_regenerates_when_fidelity_check_fails_even_if_review_and_blend_pass(self):
        """Round 7: a third, independent gate. Passing content review and
        blend cohesion must not short-circuit the loop if the generated ad
        doesn't actually replicate the reference ads' confirmed traits."""
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        fidelity = _FidelityLLMResult(
            replicated_directives=[], missed_directives=["background_style=Busy"],
            overall_fidelity_pass=False,
            notes="background is plain, not busy like the reference ads",
        )
        fake_models = _FakeModels(copy, blend, review, fidelity=fidelity)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())
        reference_ads = [
            ReferenceAd(
                ad_id="a1", composite_score=1.0, alignment_score=1, image_bytes=b"ref-jpeg",
                image_mime_type="image/jpeg", dominant_color="blue",
                background_style="Busy", hook_framework="Direct Offer",
            )
        ]

        result = generate_cold_start_ad(
            _blank_png(), intention="test", product_name="Test Product",
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            style_brief=_style_brief(), reference_ads=reference_ads, max_passes=1,
        )

        assert result.passes_used == 1
        assert len(flux_fill_client.calls) == 2
        assert result.fidelity_review_history[0].checked is True
        assert result.fidelity_review_history[0].overall_fidelity_pass is False

    def test_layout_plan_is_not_a_vision_call_and_stays_fixed_across_retries(self):
        """Generation v2: layout is decided once, deterministically, before
        the loop -- no LayoutPlan-schema vision call should ever occur, and
        the same plan object must be used on every retry."""
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=False, directive_checks=[], visual_quality_issues=["bad"],
            regeneration_recommended=True, regeneration_reason="looks bad",
        )
        fake_models = _FakeModels(copy, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        result = generate_cold_start_ad(
            _blank_png(), intention="test", product_name="Test Product",
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            style_brief=_style_brief(), reference_ads=[], max_passes=2,
        )

        assert result.passes_used == 2
        assert result.layout_plan is not None
        # No schema in this run's calls is LayoutPlan -- it's never derived
        # via a vision call in Generation v2.
        from pipeline.generation.layout import LayoutPlan as _LayoutPlanSchema

        for call in fake_models.calls:
            config = call.get("config")
            schema = getattr(config, "response_schema", None) if config is not None else None
            assert schema is not _LayoutPlanSchema

    def test_detected_duplicate_triggers_repair_before_compose(self):
        """A deterministically-detected duplicate on the first check must be
        surgically repaired (an extra Flux Fill call) before the loop moves
        on to layout validation/compositing."""
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        fake_models = _FakeModels(copy, blend, review)
        genai_client = _wrap_genai(fake_models)
        # Call sequence: (1) initial product_bbox from the product photo,
        # (2) duplicate check on the generated image -- scripted WITH an
        # extra product-shaped blob, (3) duplicate check after repair --
        # scripted clean.
        bg_remover_client = _FakeBgRemoverClient([
            _rgba_cutout_bytes(), _rgba_with_duplicate(), _rgba_cutout_bytes(),
        ])
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        result = generate_cold_start_ad(
            _blank_png(), intention="test", product_name="Test Product",
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            style_brief=_style_brief(), reference_ads=[], max_passes=0,
        )

        assert any(d.duplicate_detected for d in result.duplicate_detection_history)
        # initial background/product edit + 1 surgical repair edit
        assert len(flux_fill_client.calls) == 2
        assert "erase" in flux_fill_client.calls[1][2].lower()


class TestGenerateColdStartAdVariants:
    """Generation v2's multi-variant orchestrator: N full candidates for
    human selection, sharing every guide/geometry-driven computation once
    across the whole batch."""

    def _passing_fake_models(self) -> _FakeModels:
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        return _FakeModels(copy, blend, review)

    def test_returns_n_variants(self):
        fake_models = self._passing_fake_models()
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        results = generate_cold_start_ad_variants(
            _blank_png(), intention="test", product_name="Test Product", n_variants=3,
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            reference_ads=[],
        )

        assert len(results) == 3

    def test_font_personality_rotates_across_variants(self):
        fake_models = self._passing_fake_models()
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())

        results = generate_cold_start_ad_variants(
            _blank_png(), intention="test", product_name="Test Product", n_variants=3,
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            reference_ads=[],
        )

        fonts = [r.style_brief.font_personality for r in results]
        assert fonts == ["clean_modern", "bold_condensed", "elegant_serif"]

    def test_shared_geometry_computed_once_regardless_of_variant_count(self):
        """The original product photo's own cutout/bbox/layout are
        guide/geometry-driven, not per-variant creative choices -- computed
        once and reused, even though duplicate-detection separately re-runs
        background-removal on each variant's own generated image."""
        fake_models = self._passing_fake_models()
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill_client = _FakeFluxFillClient(_blank_png())
        # Deliberately distinct from flux_fill_client's fixed return value
        # (_blank_png()) -- duplicate-detection separately re-runs
        # background-removal on each variant's own *generated* image, and
        # this test only wants to count calls against the ORIGINAL photo.
        product_photo = b"original-product-photo-bytes"

        results = generate_cold_start_ad_variants(
            product_photo, intention="test", product_name="Test Product", n_variants=3,
            guide=_empty_guide(), genai_client=genai_client,
            bg_remover_client=bg_remover_client, flux_fill_client=flux_fill_client,
            reference_ads=[],
        )

        assert len(results) == 3
        calls_on_original_photo = [c for c in bg_remover_client.calls if c == product_photo]
        assert len(calls_on_original_photo) == 1
        assert all(r.layout_plan == results[0].layout_plan for r in results)
