"""Tests for pipeline/generation/layout.py -- the box-identification agent
that fixes the text/product collision bug (wayfinder issue #36)."""

from __future__ import annotations

from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.layout import BoundingBox, LayoutPlan, _headline_zone_hint, plan_layout


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


class TestHeadlineZoneHint:
    def test_no_hint_when_guide_has_no_headline_zone_directive(self):
        hint = _headline_zone_hint(_guide_with())
        assert "avoiding the product" in hint

    def test_prefer_wording_for_higher_is_better(self):
        signal = DirectionalSignal(
            dimension="headline_zone", value="top", direction="higher_is_better",
            magnitude=0.1, source="cox:days_active",
        )
        hint = _headline_zone_hint(_guide_with(signal))
        assert "prefer" in hint.lower()
        assert "top" in hint

    def test_avoid_wording_for_lower_is_better(self):
        signal = DirectionalSignal(
            dimension="headline_zone", value="middle", direction="lower_is_better",
            magnitude=0.1, source="cox:days_active",
        )
        hint = _headline_zone_hint(_guide_with(signal))
        assert "avoid" in hint.lower()
        assert "middle" in hint


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeModels:
    def __init__(self, plan: LayoutPlan):
        self.plan = plan
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStructuredResponse(self.plan)


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


class TestPlanLayout:
    def test_returns_parsed_layout_plan(self):
        from pipeline.clients.genai_client import GenAIClient

        plan = LayoutPlan(
            product_bbox=BoundingBox(x=0.3, y=0.3, width=0.4, height=0.5),
            headline_zone=BoundingBox(x=0.05, y=0.05, width=0.9, height=0.15),
            secondary_copy_zone=BoundingBox(x=0.05, y=0.85, width=0.9, height=0.1),
            cta_zone=BoundingBox(x=0.3, y=0.9, width=0.4, height=0.08),
        )
        fake_models = _FakeModels(plan)
        client = GenAIClient(client=_FakeGenAIClient(fake_models))

        result = plan_layout(
            client, model="gemini-2.5-flash",
            background_and_product_image=b"fake-png", guide=_guide_with(),
        )

        assert result == plan
        assert fake_models.calls[0]["model"] == "gemini-2.5-flash"
