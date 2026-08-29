"""Tests for pipeline/generation/feature_fidelity.py -- Round 7's
post-generation comparison: does the generated ad actually replicate the
feature pattern the guide's own statistics found, checked against real
reference ads rather than assumed. Uses injected fake GenAIClient responses,
no real API calls."""

from __future__ import annotations

from pipeline.generation.feature_fidelity import (
    FeatureFidelityReview,
    _FidelityLLMResult,
    review_feature_fidelity,
)
from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.reference_ads import ReferenceAd


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


def _reference_ad(ad_id: str) -> ReferenceAd:
    return ReferenceAd(
        ad_id=ad_id, composite_score=1.0, alignment_score=1, image_bytes=b"fake-jpeg",
        image_mime_type="image/jpeg", dominant_color="blue",
        background_style="Busy", hook_framework="Direct Offer",
    )


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeModels:
    def __init__(self, result: _FidelityLLMResult):
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


class TestReviewFeatureFidelity:
    def test_returns_unchecked_pass_when_no_reference_ads(self):
        """Never blocks generation just because the corpus's reference-ad
        images were stale this run -- degrades gracefully, doesn't fabricate
        a verdict with nothing to compare against."""
        from pipeline.clients.genai_client import GenAIClient

        client = GenAIClient(client=object())  # never touched
        result = review_feature_fidelity(
            client, model="gemini-2.5-flash", final_ad_image_bytes=b"final",
            reference_ads=[], guide=_guide_with(),
        )

        assert result.checked is False
        assert result.overall_fidelity_pass is True

    def test_sends_reference_images_then_final_image_in_one_call(self):
        llm_result = _FidelityLLMResult(
            replicated_directives=["background_style=Busy"],
            missed_directives=[], overall_fidelity_pass=True, notes="matches",
        )
        fake_models = _FakeModels(llm_result)
        client = _wrap(fake_models)
        refs = [_reference_ad("a1"), _reference_ad("a2")]

        result = review_feature_fidelity(
            client, model="gemini-2.5-flash", final_ad_image_bytes=b"final-ad-bytes",
            reference_ads=refs, guide=_guide_with(),
        )

        assert isinstance(result, FeatureFidelityReview)
        assert result.checked is True
        assert result.overall_fidelity_pass is True
        assert result.replicated_directives == ["background_style=Busy"]

        contents = fake_models.calls[0]["contents"]
        # 2 reference images + 1 final-ad image + the prompt string
        assert len(contents) == 4

    def test_directives_summary_only_includes_directives_with_a_value(self):
        llm_result = _FidelityLLMResult(
            replicated_directives=[], missed_directives=[],
            overall_fidelity_pass=True, notes="",
        )
        fake_models = _FakeModels(llm_result)
        client = _wrap(fake_models)
        numeric_signal = DirectionalSignal(
            dimension="palette_vibrancy", value=None, direction="higher_is_better",
            magnitude=0.1, source="shap:composite_success_score",
        )
        categorical_signal = DirectionalSignal(
            dimension="background_style", value="Busy", direction="higher_is_better",
            magnitude=0.2, source="shap:composite_success_score",
        )

        review_feature_fidelity(
            client, model="gemini-2.5-flash", final_ad_image_bytes=b"final",
            reference_ads=[_reference_ad("a1")],
            guide=_guide_with(numeric_signal, categorical_signal),
        )

        prompt = fake_models.calls[0]["contents"][-1]
        assert "background_style=Busy" in prompt
        assert "palette_vibrancy" not in prompt
