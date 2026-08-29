"""Tests for pipeline/generation/background.py -- the scene-description fix
(wayfinder issue #36, round 5): negative guide signals must surface as
explicit "avoid X" instructions instead of being silently dropped, and the
fallback must never be "studio" now that real top ads are confirmed Busy.
Round 6 adds coverage for the mask-protected inpaint call itself (fake
bg-remover/flux-fill clients, no real network calls)."""

from __future__ import annotations

from pipeline.generation.background import (
    _LAST_RESORT_FALLBACK,
    _guide_to_scene_description,
    _scene_description,
    generate_background_and_product,
)
from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.style_reference import StyleBrief


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


class TestGuideToSceneDescription:
    def test_negative_signal_becomes_explicit_avoid_instruction(self):
        """Regression: this used to silently drop every lower_is_better
        signal -- including the real, live finding that background_style=
        Studio is lower_is_better -- and fall through to a hardcoded
        "studio background" default that directly contradicted the data."""
        signal = DirectionalSignal(
            dimension="background_style", value="Studio", direction="lower_is_better",
            magnitude=0.05, source="cox:days_active",
        )
        description = _guide_to_scene_description(_guide_with(signal))
        assert "avoid" in description.lower()
        assert "studio" in description.lower()

    def test_positive_signal_becomes_use_instruction(self):
        signal = DirectionalSignal(
            dimension="dominant_color", value="blue", direction="higher_is_better",
            magnitude=0.05, source="shap:composite_success_score",
        )
        description = _guide_to_scene_description(_guide_with(signal))
        assert "use" in description.lower()
        assert "blue" in description.lower()

    def test_no_signals_falls_back_to_non_studio_default(self):
        """The fallback must never *prescribe* a plain studio/white
        background -- confirmed live that 5/5 real top ads by composite
        success score are background_style=Busy, none Studio/plain. It may
        still name "studio" as something to explicitly avoid."""
        description = _guide_to_scene_description(_guide_with())
        assert description == _LAST_RESORT_FALLBACK
        assert "not a plain" in description.lower() or "avoid" in description.lower()

    def test_copy_style_directives_never_leak_into_scene_description(self):
        signal = DirectionalSignal(
            dimension="uppercase_ratio", value=None, direction="higher_is_better",
            magnitude=0.05, source="shap:composite_success_score",
        )
        description = _guide_to_scene_description(_guide_with(signal))
        assert description == _LAST_RESORT_FALLBACK


def _rgba_cutout_bytes() -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(10, 30):
        for y in range(10, 30):
            img.putpixel((x, y), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeBgRemoverClient:
    def __init__(self, cutout_bytes: bytes):
        self.cutout_bytes = cutout_bytes
        self.calls: list[bytes] = []

    def remove_background(self, image_bytes: bytes) -> bytes:
        self.calls.append(image_bytes)
        return self.cutout_bytes


class _FakeFluxFillClient:
    def __init__(self, result_bytes: bytes):
        self.result_bytes = result_bytes
        self.calls: list[tuple] = []

    def inpaint(self, image_bytes, mask_bytes, prompt, **kwargs):
        self.calls.append((image_bytes, mask_bytes, prompt))
        return self.result_bytes


class TestGenerateBackgroundAndProduct:
    """Round 6: the product photo now goes through background-remover ->
    masking -> Flux Fill's masked inpaint, not Flux Kontext's whole-image
    edit -- confirmed here via fake clients that the mask is actually built
    and passed through, and that the prompt never asks the model to
    reproduce the product (that's now structurally guaranteed by the mask,
    not requested)."""

    def test_calls_bg_remover_then_builds_mask_then_inpaints(self):
        photo = b"fake-product-photo-bytes"
        bg_remover = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill = _FakeFluxFillClient(b"final-image-bytes")

        result = generate_background_and_product(
            bg_remover, flux_fill, photo,
            intention="test intention", guide=_guide_with(),
        )

        assert result == b"final-image-bytes"
        assert bg_remover.calls == [photo]
        assert len(flux_fill.calls) == 1
        called_image, called_mask, called_prompt = flux_fill.calls[0]
        assert called_image == photo
        assert isinstance(called_mask, bytes) and len(called_mask) > 0

    def test_prompt_forbids_text_in_the_generated_scene(self):
        bg_remover = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill = _FakeFluxFillClient(b"final-image-bytes")

        generate_background_and_product(
            bg_remover, flux_fill, b"photo",
            intention="test", guide=_guide_with(),
        )

        prompt = flux_fill.calls[0][2].lower()
        assert "zero text" in prompt or "no text" in prompt
        assert "never be rendered into this image" in prompt

    def test_intention_text_is_explicitly_marked_non_renderable(self):
        """Round 8 regression: live-verified Flux Fill rendered a full ghost
        paragraph of ad copy into the background, legibly echoing fragments
        of the raw intention string -- the old prompt embedded it as plain
        "context" with no instruction against rendering it. The intention
        text itself must always be present (it's real guidance), but the
        prompt must explicitly forbid treating it as a caption to paint."""
        bg_remover = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill = _FakeFluxFillClient(b"final-image-bytes")

        generate_background_and_product(
            bg_remover, flux_fill, b"photo",
            intention="Energizing joint supplement for caring dog owners",
            guide=_guide_with(),
        )

        prompt = flux_fill.calls[0][2]
        assert "Energizing joint supplement for caring dog owners" in prompt
        assert "must never be rendered as text" in prompt.lower()

    def test_no_text_instruction_is_front_loaded_in_the_prompt(self):
        """Primacy matters for instruction-following in these models --
        the no-text/no-duplicate rules must appear before the scene
        description and intention, not buried after them."""
        bg_remover = _FakeBgRemoverClient(_rgba_cutout_bytes())
        flux_fill = _FakeFluxFillClient(b"final-image-bytes")

        generate_background_and_product(
            bg_remover, flux_fill, b"photo",
            intention="some intention text", guide=_guide_with(),
        )

        prompt = flux_fill.calls[0][2]
        assert prompt.index("zero text of any kind") < prompt.index("Fill in a new background")


class TestSceneDescription:
    def test_style_brief_takes_precedence_over_guide(self):
        brief = StyleBrief(
            background_treatment="a busy kitchen countertop scene",
            dominant_color_palette=["#204060"],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="clean_modern", cta_style_notes="",
        )
        description = _scene_description(_guide_with(), brief)
        assert "busy kitchen countertop" in description
        assert "#204060" in description

    def test_falls_back_to_guide_when_no_style_brief(self):
        description = _scene_description(_guide_with(), None)
        assert description == _LAST_RESORT_FALLBACK
