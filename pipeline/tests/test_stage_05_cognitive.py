"""Stage 5 cognitive — smoke test with a mocked GenAI client (no network)."""

from __future__ import annotations

import time

from pipeline.models.output_schema import (
    HookFramework,
    HumanModelAnalysis,
    MarketingPsychology,
    PipelineContext,
    SpatialAndNestedObjects,
)
from pipeline.stages.stage_05_cognitive import CognitiveStage, _DeepReasoningResult
from pipeline.tests.conftest import make_image_bytes


class _MockGenAI:
    """Returns canned, schema-typed objects based on the requested schema."""

    def __init__(self, fail_deep: bool = False):
        self.fail_deep = fail_deep
        self.calls: list[str] = []

    def extract_structured(self, *, model, prompt, image_bytes, image_mime_type, schema):
        self.calls.append(schema.__name__)
        if schema is MarketingPsychology:
            return MarketingPsychology(
                hook_framework=HookFramework.PAS, emoji_count=2
            )
        if schema is _DeepReasoningResult:
            if self.fail_deep:
                raise RuntimeError("vertex unavailable")
            return _DeepReasoningResult(
                spatial_and_nested_objects=SpatialAndNestedObjects(),
                human_model_analysis=HumanModelAnalysis(human_presence=True, model_count=1),
            )
        raise AssertionError(f"unexpected schema {schema}")


def _ctx() -> PipelineContext:
    return PipelineContext(
        ad_id="t", image_path="m", image_bytes=make_image_bytes(50, 50, (10, 20, 30))
    )


def test_both_tiers_populate_result():
    mock = _MockGenAI()
    result = CognitiveStage(genai_client=mock).process(_ctx())
    assert result.result.marketing_psychology.hook_framework == HookFramework.PAS
    assert result.result.human_model_analysis.human_presence is True
    assert mock.calls == ["MarketingPsychology", "_DeepReasoningResult"]


def test_deep_tier_failure_degrades_gracefully():
    # Cheap tier succeeds, deep tier raises -> deep fields stay default, no crash.
    result = CognitiveStage(genai_client=_MockGenAI(fail_deep=True)).process(_ctx())
    assert result.result.marketing_psychology.hook_framework == HookFramework.PAS
    assert result.result.human_model_analysis.human_presence is False  # default


def test_cheap_tier_skipped_when_marketing_already_set():
    # If Datalab already filled marketing_psychology, the Gemini cheap tier must not overwrite.
    ctx = _ctx()
    ctx.result.marketing_psychology = MarketingPsychology(
        primary_value_proposition="from datalab"
    )
    mock = _MockGenAI()
    CognitiveStage(genai_client=mock).process(ctx)
    assert "MarketingPsychology" not in mock.calls              # cheap tier skipped
    assert ctx.result.marketing_psychology.primary_value_proposition == "from datalab"


class _SlowMockGenAI:
    """Mimics _MockGenAI but with an artificial per-call delay, so a timing
    assertion can prove the two tiers genuinely overlap in wall-clock time."""

    def __init__(self, delay: float = 0.3):
        self.delay = delay

    def extract_structured(self, *, model, prompt, image_bytes, image_mime_type, schema):
        time.sleep(self.delay)
        if schema is MarketingPsychology:
            return MarketingPsychology(hook_framework=HookFramework.PAS)
        if schema is _DeepReasoningResult:
            return _DeepReasoningResult(
                human_model_analysis=HumanModelAnalysis(human_presence=True)
            )
        raise AssertionError(f"unexpected schema {schema}")


class TestCheapAndDeepTiersRunConcurrently:
    """Regression: the two Gemini calls used to run sequentially inside
    process() (cheap tier fully awaited, then deep tier started) — confirmed
    live as the dominant per-ad cost (~18-19s combined). They're independent
    (different prompts/schemas, same read-only image bytes), so they must
    overlap rather than sum."""

    def test_both_tiers_complete_in_roughly_one_delay_not_two(self):
        delay = 0.3
        start = time.monotonic()
        CognitiveStage(genai_client=_SlowMockGenAI(delay=delay)).process(_ctx())
        elapsed = time.monotonic() - start
        # Sequential would take ~2*delay (0.6s); concurrent should be ~delay (0.3s).
        assert elapsed < delay * 1.7

    def test_both_tiers_still_populate_result_when_concurrent(self):
        result = CognitiveStage(genai_client=_SlowMockGenAI(delay=0.01)).process(_ctx())
        assert result.result.marketing_psychology.hook_framework == HookFramework.PAS
        assert result.result.human_model_analysis.human_presence is True
