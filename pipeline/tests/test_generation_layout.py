"""Tests for pipeline/generation/layout.py. Generation v2 (2026-08-30)
retired the vision-based `plan_layout` (searched an already-generated frame
for empty space) in favor of layout_planner.py's deterministic
`plan_layout_from_guide` -- see that module's own tests. What remains here
is `validate_layout`, a cheaper vision check of whether a specific generated
frame still respects an already-decided plan."""

from __future__ import annotations

from pipeline.generation.layout import BoundingBox, LayoutPlan, LayoutValidation, validate_layout


def _layout_plan() -> LayoutPlan:
    return LayoutPlan(
        product_bbox=BoundingBox(x=0.3, y=0.3, width=0.4, height=0.5),
        headline_zone=BoundingBox(x=0.05, y=0.05, width=0.9, height=0.15),
        secondary_copy_zone=BoundingBox(x=0.05, y=0.85, width=0.9, height=0.1),
        cta_zone=BoundingBox(x=0.3, y=0.9, width=0.4, height=0.08),
    )


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeModels:
    def __init__(self, validation: LayoutValidation):
        self.validation = validation
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStructuredResponse(self.validation)


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


class TestValidateLayout:
    def test_returns_parsed_layout_validation(self):
        from pipeline.clients.genai_client import GenAIClient

        validation = LayoutValidation(zones_respected=True, notes="all clear")
        fake_models = _FakeModels(validation)
        client = GenAIClient(client=_FakeGenAIClient(fake_models))

        result = validate_layout(
            client, model="gemini-2.5-flash",
            background_and_product_image=b"fake-png", layout_plan=_layout_plan(),
        )

        assert result == validation
        assert fake_models.calls[0]["model"] == "gemini-2.5-flash"

    def test_prompt_asks_to_confirm_not_propose_a_new_layout(self):
        from pipeline.clients.genai_client import GenAIClient

        fake_models = _FakeModels(LayoutValidation(zones_respected=True, notes=""))
        client = GenAIClient(client=_FakeGenAIClient(fake_models))

        validate_layout(
            client, model="gemini-2.5-flash",
            background_and_product_image=b"fake-png", layout_plan=_layout_plan(),
        )

        prompt = fake_models.calls[0]["contents"][-1]
        assert "do not propose a different layout" in prompt.lower()
        assert "headline_zone" in prompt
