"""Tests for pipeline/generation/blend.py -- the visual-cohesion agent, kept
deliberately separate from the guide-adherence reviewer (see the module's own
docstring: one call, one narrow judgment)."""

from __future__ import annotations

from pipeline.generation.blend import BlendReview, review_blend


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeModels:
    def __init__(self, result: BlendReview):
        self.result = result
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStructuredResponse(self.result)


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


class TestReviewBlend:
    def test_returns_parsed_blend_review(self):
        from pipeline.clients.genai_client import GenAIClient

        expected = BlendReview(blends_well=False, issues=["visible seam"], notes="edge mismatch")
        fake_models = _FakeModels(expected)
        client = GenAIClient(client=_FakeGenAIClient(fake_models))

        result = review_blend(client, model="gemini-2.5-flash", ad_image_bytes=b"fake-png")

        assert result == expected
        assert fake_models.calls[0]["model"] == "gemini-2.5-flash"
        contents = fake_models.calls[0]["contents"]
        assert contents[0].inline_data.data == b"fake-png"
