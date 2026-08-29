"""Tests for pipeline/generation/style_reference.py -- Round 7 rescope: this
agent is now text-only, guide-directed, no reference-ad images (those moved
to feature_fidelity.py's post-generation comparison role). Uses injected
fake GenAIClient responses, no real API calls."""

from __future__ import annotations

from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.style_reference import StyleBrief, derive_style_brief


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeModels:
    def __init__(self, result: StyleBrief):
        self.result = result
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStructuredResponse(self.result)


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


def _wrap(fake_models: _FakeModels):
    from pipeline.clients.genai_client import GenAIClient

    return GenAIClient(client=_FakeGenAIClient(fake_models))


class TestDeriveStyleBrief:
    def test_is_text_only_no_images(self):
        """Regression: this call must never send image Parts -- the whole
        point of Round 7's rescope is that the guide's own statistics are
        authoritative, not a vision read of reference-ad images."""
        expected = StyleBrief(
            background_treatment="a busy kitchen scene", dominant_color_palette=["#204060"],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="clean_modern", cta_style_notes="rounded",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)
        signal = DirectionalSignal(
            dimension="background_style", value="Busy", direction="higher_is_better",
            magnitude=0.1, source="shap:composite_success_score",
        )

        result = derive_style_brief(client, model="gemini-2.5-flash", guide=_guide_with(signal))

        assert result == expected
        contents = fake_models.calls[0]["contents"]
        assert len(contents) == 1  # prompt text only, no image parts
        assert isinstance(contents[0], str)

    def test_directives_are_stated_as_authoritative_in_the_prompt(self):
        expected = StyleBrief(
            background_treatment="x", dominant_color_palette=[],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="clean_modern", cta_style_notes="",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)
        signal = DirectionalSignal(
            dimension="dominant_color", value="blue", direction="higher_is_better",
            magnitude=0.1, source="shap:composite_success_score",
        )

        derive_style_brief(client, model="gemini-2.5-flash", guide=_guide_with(signal))

        prompt = fake_models.calls[0]["contents"][0]
        assert "dominant_color=blue" in prompt
        assert "authoritative" in prompt.lower()

    def test_fallback_prompt_never_suggests_studio_default(self):
        """Regression: must not repeat the old hardcoded-studio-default bug
        (background.py's Round 5 fix) in its own prompt wording, even with
        no directives at all."""
        expected = StyleBrief(
            background_treatment="x", dominant_color_palette=[],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="clean_modern", cta_style_notes="",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)

        derive_style_brief(client, model="gemini-2.5-flash", guide=_guide_with())

        prompt = fake_models.calls[0]["contents"][0]
        assert "do not default to a plain white studio" in prompt.lower()

    def test_font_personality_framed_as_qualitative_not_statistical(self):
        expected = StyleBrief(
            background_treatment="x", dominant_color_palette=[],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="bold_condensed", cta_style_notes="",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)

        derive_style_brief(client, model="gemini-2.5-flash", guide=_guide_with())

        prompt = fake_models.calls[0]["contents"][0].lower()
        assert "font_personality" in prompt
        assert "no statistical directive" in prompt or "never measures" in prompt
