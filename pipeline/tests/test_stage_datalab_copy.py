"""DatalabCopyStage + orchestrator swap — mocked client, offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.clients.datalab_client import DatalabDocumentClient
from pipeline.config import Settings
from pipeline.models.output_schema import PipelineContext, TechnicalMetadata
from pipeline.orchestrator import build_default_stages
from pipeline.stages.stage_02_datalab_copy import DatalabCopyStage
from pipeline.tests.conftest import make_image_bytes

_OUT = Path(__file__).parent.parent.parent / "scripts" / "datalab_out"
_CONVERT = _OUT / "convert.json"
_EXTRACT = _OUT / "extract.json"


@pytest.fixture
def convert_json() -> dict:
    if not _CONVERT.exists():
        pytest.skip("convert.json fixture not present")
    return json.loads(_CONVERT.read_text())


@pytest.fixture
def extract_json() -> dict:
    if not _EXTRACT.exists():
        pytest.skip("extract.json fixture not present")
    d = json.loads(_EXTRACT.read_text())
    return json.loads(d) if isinstance(d, str) else d


def test_stage_populates_copy_positioning(convert_json, extract_json):
    client = DatalabDocumentClient(
        convert_fn=lambda _path: (convert_json, None),
        extract_fn=lambda _cp, _path: extract_json,
    )
    ctx = PipelineContext(
        ad_id="t", image_path="m", image_bytes=make_image_bytes(60, 60, (200, 100, 50))
    )
    ctx.result.technical_metadata = TechnicalMetadata(width=1080, height=1920)

    out = DatalabCopyStage(client=client).process(ctx)

    assert "SCOOPS" in out.result.typography_hierarchy.primary_headline.text
    assert out.result.copywriting_features.copy_block_count == 8
    assert out.result.copywriting_features.uppercase_ratio == 1.0
    assert out.result.copywriting_features.hook_type == "benefit_led"     # from extract
    assert "ARMRA" in out.result.marketing_psychology.primary_value_proposition
    assert out.result.placement.text_alignment == "center"
    assert out.ocr_boxes  # image-space boxes handed to the colour stage


def test_extract_schema_ships_with_the_package():
    # The real extract path reads this file; the mocked stage test bypasses it.
    from pipeline.clients.datalab_client import _SCHEMA_PATH

    assert _SCHEMA_PATH.exists(), f"missing extract schema at {_SCHEMA_PATH}"
    json.loads(_SCHEMA_PATH.read_text())  # valid JSON


def test_orchestrator_swaps_ocr_for_datalab_when_enabled():
    default = build_default_stages(Settings(enable_datalab_copy=False))
    assert "stage_02_ocr" in [s.name for s in default]

    swapped = build_default_stages(Settings(enable_datalab_copy=True))
    names = [s.name for s in swapped]
    assert "stage_02_datalab_copy" in names
    assert "stage_02_ocr" not in names
