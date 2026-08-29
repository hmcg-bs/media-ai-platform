"""Tests for pipeline/generation/pipeline.py -- the cold-start orchestration
loop, using injected fake genai/flux clients (no real API calls). Focuses on
the regeneration-loop decision logic (when does it stop, when does it retry)
and _layout_from_plan's conversion from LayoutPlan zones to ElementSpecs --
the fix for the collision bug found in the first live smoke test."""

from __future__ import annotations

import io

from PIL import Image

from pipeline.generation.blend import BlendReview
from pipeline.generation.copywriter import AdCopy
from pipeline.generation.feature_fidelity import _FidelityLLMResult
from pipeline.generation.guide import GenerationGuide
from pipeline.generation.layout import BoundingBox, LayoutPlan
from pipeline.generation.pipeline import _layout_from_plan, generate_cold_start_ad
from pipeline.generation.reference_ads import ReferenceAd
from pipeline.generation.reviewer import AdReview
from pipeline.generation.style_reference import StyleBrief


def _blank_png() -> bytes:
    img = Image.new("RGB", (100, 100), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _empty_guide() -> GenerationGuide:
    return GenerationGuide(
        visual_directives=[], copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


def _layout_plan() -> LayoutPlan:
    return LayoutPlan(
        product_bbox=BoundingBox(x=0.3, y=0.3, width=0.4, height=0.5),
        headline_zone=BoundingBox(x=0.05, y=0.05, width=0.9, height=0.15),
        secondary_copy_zone=BoundingBox(x=0.05, y=0.85, width=0.9, height=0.1),
        cta_zone=BoundingBox(x=0.3, y=0.9, width=0.4, height=0.08),
        price_offer_zone=None,
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
    def test_elements_positioned_at_plan_zones_not_static_defaults(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        plan = _layout_plan()
        elements = _layout_from_plan(copy, plan, _style_brief())

        headline = next(e for e in elements if e.element_type == "headline")
        assert headline.x == plan.headline_zone.x
        assert headline.y == plan.headline_zone.y
        assert headline.width == plan.headline_zone.width

    def test_price_offer_omitted_when_no_zone_or_no_text(self):
        copy_no_text = AdCopy(
            headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None
        )
        plan = _layout_plan()
        elements = _layout_from_plan(copy_no_text, plan, _style_brief())
        assert not any(e.element_type == "price_offer" for e in elements)

        copy_with_text = AdCopy(
            headline="H", secondary_copy="S", cta_text="Shop", price_offer_text="$19.99"
        )
        elements2 = _layout_from_plan(copy_with_text, plan, _style_brief())  # no price zone
        assert not any(e.element_type == "price_offer" for e in elements2)

    def test_font_personality_and_band_sourced_from_style_brief(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        plan = _layout_plan()
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


class _FakeImageResponse:
    def __init__(self, image_bytes: bytes):
        self.parts = [_FakePart(_FakeInlineData(image_bytes))]


def _passing_fidelity_result() -> _FidelityLLMResult:
    return _FidelityLLMResult(
        replicated_directives=[], missed_directives=[],
        overall_fidelity_pass=True, notes="",
    )


class _FakeModels:
    """Routes generate_content calls by schema type in config, mimicking real
    google-genai dispatch closely enough for these orchestration tests."""

    def __init__(
        self, copy: AdCopy, layout: LayoutPlan, blend: BlendReview, review: AdReview,
        fidelity: _FidelityLLMResult | None = None,
    ):
        self._copy, self._layout, self._blend, self._review = copy, layout, blend, review
        self._fidelity = fidelity or _passing_fidelity_result()
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        config = kwargs.get("config")
        schema = getattr(config, "response_schema", None) if config is not None else None
        if schema is AdCopy:
            return _FakeStructuredResponse(self._copy)
        if schema is LayoutPlan:
            return _FakeStructuredResponse(self._layout)
        if schema is BlendReview:
            return _FakeStructuredResponse(self._blend)
        if schema is AdReview:
            return _FakeStructuredResponse(self._review)
        if schema is _FidelityLLMResult:
            return _FakeStructuredResponse(self._fidelity)
        raise AssertionError(f"unexpected schema in test fake: {schema}")


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


class _FakeBgRemoverClient:
    def __init__(self, cutout_bytes: bytes):
        self.cutout_bytes = cutout_bytes
        self.calls: list[bytes] = []

    def remove_background(self, image_bytes: bytes) -> bytes:
        self.calls.append(image_bytes)
        return self.cutout_bytes


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
        layout = _layout_plan()
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        fake_models = _FakeModels(copy, layout, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_blank_png())
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

    def test_retries_up_to_max_passes_when_review_keeps_failing(self):
        copy = AdCopy(headline="H", secondary_copy="S", cta_text="Shop", price_offer_text=None)
        layout = _layout_plan()
        blend = BlendReview(blends_well=True, issues=[], notes="")
        review = AdReview(
            overall_pass=False, directive_checks=[], visual_quality_issues=["bad"],
            regeneration_recommended=True, regeneration_reason="looks bad",
        )
        fake_models = _FakeModels(copy, layout, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_blank_png())
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
        layout = _layout_plan()
        blend = BlendReview(blends_well=False, issues=["seam visible"], notes="bad seam")
        review = AdReview(
            overall_pass=True, directive_checks=[], visual_quality_issues=[],
            regeneration_recommended=False,
        )
        fake_models = _FakeModels(copy, layout, blend, review)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_blank_png())
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
        layout = _layout_plan()
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
        fake_models = _FakeModels(copy, layout, blend, review, fidelity=fidelity)
        genai_client = _wrap_genai(fake_models)
        bg_remover_client = _FakeBgRemoverClient(_blank_png())
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
