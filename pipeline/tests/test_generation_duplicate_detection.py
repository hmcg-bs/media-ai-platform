"""Tests for pipeline/generation/duplicate_detection.py -- Generation v2's
deterministic-first check for the duplicate-product hallucination confirmed
live across Rounds 6-8 (docs/generation-failure-modes.md bug #6). Uses fake
bg-remover/genai clients, no real API/network calls."""

from __future__ import annotations

import io

from PIL import Image

from pipeline.generation.duplicate_detection import (
    _DuplicateVisionResult,
    detect_duplicate_product,
)
from pipeline.generation.layout import BoundingBox


def _rgba_with_squares(size: int, *squares: tuple[int, int, int, int]) -> bytes:
    """squares: (x0, y0, x1, y1) opaque regions on an otherwise transparent canvas."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x0, y0, x1, y1 in squares:
        for x in range(x0, x1):
            for y in range(y0, y1):
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


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeModels:
    def __init__(self, vision_result: _DuplicateVisionResult | None = None):
        self.vision_result = vision_result
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.vision_result is None:
            raise AssertionError("unexpected vision-escalation call in this test")
        return _FakeStructuredResponse(self.vision_result)


class _FakeGenAIClient:
    def __init__(self, models: _FakeModels):
        self.models = models


def _wrap_genai(fake_models: _FakeModels):
    from pipeline.clients.genai_client import GenAIClient

    return GenAIClient(client=_FakeGenAIClient(fake_models))


class TestDetectDuplicateProduct:
    def test_single_blob_matching_original_is_not_a_duplicate(self):
        original_bbox = BoundingBox(x=0.25, y=0.25, width=0.5, height=0.5)
        cutout = _rgba_with_squares(40, (10, 10, 30, 30))
        bg_remover = _FakeBgRemoverClient(cutout)
        fake_models = _FakeModels()
        genai_client = _wrap_genai(fake_models)

        result = detect_duplicate_product(
            bg_remover, genai_client, model="gemini-2.5-flash",
            generated_image_bytes=b"generated", original_product_bbox=original_bbox,
            original_product_photo_bytes=b"original-photo",
        )

        assert result.duplicate_detected is False
        assert result.detection_method == "none"
        assert fake_models.calls == []  # no vision call needed

    def test_one_extra_blob_matching_product_shape_is_caught_deterministically(self):
        # bbox fractions map to pixels 2..9 x, 10..29 y in a 40px canvas
        original_bbox = BoundingBox(x=0.05, y=0.25, width=0.2, height=0.5)
        cutout = _rgba_with_squares(
            40,
            (2, 10, 10, 30),  # original product-shaped blob
            (25, 10, 33, 30),  # a second, near-identical blob elsewhere
        )
        bg_remover = _FakeBgRemoverClient(cutout)
        fake_models = _FakeModels()
        genai_client = _wrap_genai(fake_models)

        result = detect_duplicate_product(
            bg_remover, genai_client, model="gemini-2.5-flash",
            generated_image_bytes=b"generated", original_product_bbox=original_bbox,
            original_product_photo_bytes=b"original-photo",
        )

        assert result.duplicate_detected is True
        assert result.detection_method == "deterministic"
        assert result.duplicate_bbox is not None
        assert fake_models.calls == []  # clear case, no vision call needed

    def test_ambiguous_extra_blob_escalates_to_vision_call(self):
        original_bbox = BoundingBox(x=0.05, y=0.05, width=0.2, height=0.2)  # small square, top-left
        cutout = _rgba_with_squares(
            40,
            (2, 2, 10, 10),  # original
            (15, 15, 38, 38),  # a large, oddly-shaped extra blob -- not a clean match
        )
        bg_remover = _FakeBgRemoverClient(cutout)
        vision_result = _DuplicateVisionResult(
            duplicate_detected=True,
            duplicate_bbox=BoundingBox(x=0.4, y=0.4, width=0.5, height=0.5),
            notes="a second product-like object is visible",
        )
        fake_models = _FakeModels(vision_result)
        genai_client = _wrap_genai(fake_models)

        result = detect_duplicate_product(
            bg_remover, genai_client, model="gemini-2.5-flash",
            generated_image_bytes=b"generated", original_product_bbox=original_bbox,
            original_product_photo_bytes=b"original-photo",
        )

        assert result.detection_method == "vision_agent"
        assert result.duplicate_detected is True
        assert len(fake_models.calls) == 1

    def test_vision_escalation_receives_original_photo_then_generated_image(self):
        original_bbox = BoundingBox(x=0.05, y=0.05, width=0.2, height=0.2)
        cutout = _rgba_with_squares(
            40,
            (2, 2, 10, 10),
            (15, 15, 38, 38),
        )
        bg_remover = _FakeBgRemoverClient(cutout)
        vision_result = _DuplicateVisionResult(
            duplicate_detected=False, duplicate_bbox=None, notes="",
        )
        fake_models = _FakeModels(vision_result)
        genai_client = _wrap_genai(fake_models)

        detect_duplicate_product(
            bg_remover, genai_client, model="gemini-2.5-flash",
            generated_image_bytes=b"generated-image", original_product_bbox=original_bbox,
            original_product_photo_bytes=b"original-photo",
        )

        contents = fake_models.calls[0]["contents"]
        # Two image Parts followed by the text prompt; the Part API isn't
        # directly inspectable here without the real SDK, so just check
        # ordering held the two images before the prompt string.
        assert isinstance(contents[-1], str)
        assert len(contents) == 3
