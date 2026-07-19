"""Stage 5 cognitive — smoke test with a mocked GenAI client (no network)."""

from __future__ import annotations

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
