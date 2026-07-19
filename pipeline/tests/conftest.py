"""Shared test fixtures — in-memory images, no network, no GCP."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from pipeline.models.output_schema import PipelineContext


def make_image_bytes(
    width: int, height: int, color: tuple[int, int, int], fmt: str = "PNG"
) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def solid_red_png() -> bytes:
    return make_image_bytes(200, 200, (220, 30, 30))


@pytest.fixture
def square_context(solid_red_png: bytes) -> PipelineContext:
    return PipelineContext(
        ad_id="test_001", image_path="memory://test_001.png", image_bytes=solid_red_png
    )
