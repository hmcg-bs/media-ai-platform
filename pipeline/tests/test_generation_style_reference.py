"""Tests for pipeline/generation/style_reference.py -- the retrieval-grounded
style agent, using injected fake GenAIClient responses (no real API calls)."""

from __future__ import annotations

from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.reference_ads import ReferenceAd
from pipeline.generation.style_reference import StyleBrief, derive_style_brief


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


def _reference_ad(ad_id: str) -> ReferenceAd:
    return ReferenceAd(
        ad_id=ad_id, composite_score=1.0, image_bytes=b"fake-jpeg",
        image_mime_type="image/jpeg", dominant_color="blue",
        background_style="Busy", hook_framework="Direct Offer",
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
    def test_uses_multi_image_call_when_reference_ads_present(self):
        expected = StyleBrief(
            background_treatment="busy kitchen scene", dominant_color_palette=["#204060"],
            text_needs_background_band=True, text_background_band_color_hex="#ffffff",
            font_personality="bold_condensed", cta_style_notes="rounded",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)
        refs = [_reference_ad("a1"), _reference_ad("a2")]

        result = derive_style_brief(
            client, model="gemini-2.5-flash", reference_ads=refs, guide=_guide_with()
        )

        assert result == expected
        contents = fake_models.calls[0]["contents"]
        # 2 reference images + the prompt string
        assert len(contents) == 3

    def test_falls_back_to_text_only_call_when_no_reference_ads(self):
        expected = StyleBrief(
            background_treatment="a lifestyle scene", dominant_color_palette=[],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="clean_modern", cta_style_notes="",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)

        result = derive_style_brief(
            client, model="gemini-2.5-flash", reference_ads=[], guide=_guide_with()
        )

        assert result == expected
        contents = fake_models.calls[0]["contents"]
        assert len(contents) == 1  # prompt only, no image parts

    def test_fallback_prompt_never_suggests_studio_default(self):
        """Regression: this fallback path must not repeat the old
        hardcoded-studio-default bug (background.py's fix) in its own prompt
        wording."""
        expected = StyleBrief(
            background_treatment="x", dominant_color_palette=[],
            text_needs_background_band=False, text_background_band_color_hex=None,
            font_personality="clean_modern", cta_style_notes="",
        )
        fake_models = _FakeModels(expected)
        client = _wrap(fake_models)

        derive_style_brief(client, model="gemini-2.5-flash", reference_ads=[], guide=_guide_with())

        prompt = fake_models.calls[0]["contents"][0]
        assert "do not default to a plain white studio" in prompt.lower()
