"""LayerColorStage / ImageryStage + flag-driven stage list — mocked, offline."""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.models.output_schema import PipelineContext
from pipeline.orchestrator import build_default_stages
from pipeline.stages.stage_06_layer_color import LayerColorStage
from pipeline.stages.stage_07_imagery import ImageryStage
from pipeline.tests.conftest import make_image_bytes


class _FakeLayers:
    def __init__(self, layers: list[bytes]):
        self._layers = layers

    def decompose(self, image_bytes: bytes) -> list[bytes]:
        return self._layers


class _FakeVL:
    def describe(self, image_bytes: bytes, prompt: str) -> str:
        return "a hand holding a scoop of white powder"


def _ctx() -> PipelineContext:
    return PipelineContext(
        ad_id="t", image_path="m", image_bytes=make_image_bytes(50, 50, (10, 20, 30))
    )


def test_layer_color_stage_sets_profile_from_layer():
    layer = make_image_bytes(40, 40, (200, 100, 50))  # solid → known bg colour
    ctx = LayerColorStage(client=_FakeLayers([layer])).process(_ctx())
    assert ctx.result.color_profile.background_hex == "#C86432"  # RGB(200,100,50)


def test_layer_color_stage_no_layers_leaves_default():
    ctx = LayerColorStage(client=_FakeLayers([])).process(_ctx())
    assert ctx.result.color_profile.background_hex == ""  # unchanged default


def test_imagery_stage_sets_description():
    ctx = ImageryStage(client=_FakeVL()).process(_ctx())
    assert ctx.result.imagery_description == "a hand holding a scoop of white powder"


def test_build_default_stages_flags_off():
    stages = build_default_stages(Settings(enable_layer_color=False, enable_imagery=False))
    assert [s.name for s in stages] == [
        "stage_01_metadata", "stage_02_ocr", "stage_03_color", "stage_05_cognitive"
    ]


def test_build_default_stages_flags_on_appends_paid_stages():
    stages = build_default_stages(Settings(enable_layer_color=True, enable_imagery=True))
    assert [s.name for s in stages][-2:] == ["stage_06_layer_color", "stage_07_imagery"]
