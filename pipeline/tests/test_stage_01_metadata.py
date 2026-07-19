"""Stage 1 metadata — deterministic, fully offline."""

from __future__ import annotations

import pytest

from pipeline.models.output_schema import PipelineContext
from pipeline.stages.base_stage import StageError
from pipeline.stages.stage_01_metadata import MetadataStage, _aspect_ratio_label
from pipeline.tests.conftest import make_image_bytes


def test_extracts_dimensions_and_square_ratio(square_context):
    result = MetadataStage().process(square_context)
    meta = result.result.technical_metadata
    assert meta.width == 200
    assert meta.height == 200
    assert meta.aspect_ratio == "1:1"
    assert meta.file_type == "png"


def test_snaps_to_common_vertical_ratio():
    assert _aspect_ratio_label(1080, 1920) == "9:16"
    assert _aspect_ratio_label(1080, 1350) == "4:5"


def test_reduces_uncommon_ratio_with_gcd():
    assert _aspect_ratio_label(300, 200) == "3:2"


def test_loads_bytes_from_path_when_missing(tmp_path):
    img_path = tmp_path / "ad.png"
    img_path.write_bytes(make_image_bytes(100, 50, (0, 0, 0)))
    ctx = PipelineContext(ad_id="ad", image_path=str(img_path))  # no image_bytes
    result = MetadataStage().process(ctx)
    assert result.result.technical_metadata.width == 100
    assert result.image_bytes is not None  # stage populated it


def test_corrupt_image_raises_stage_error():
    ctx = PipelineContext(ad_id="bad", image_path="x", image_bytes=b"not-an-image")
    with pytest.raises(StageError) as exc:
        MetadataStage().process(ctx)
    assert exc.value.stage_name == "stage_01_metadata"
