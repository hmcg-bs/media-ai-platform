"""Orchestrator — per-stage fallback + end-to-end validation, fully offline."""

from __future__ import annotations

import json

from pipeline.models.output_schema import ExtractionResult, PipelineContext
from pipeline.orchestrator import process_folder, run_one
from pipeline.stages.base_stage import BaseStage, StageError
from pipeline.tests.conftest import make_image_bytes


class _OkStage(BaseStage):
    name = "ok_stage"

    def process(self, context: PipelineContext) -> PipelineContext:
        context.result.ad_id = context.ad_id
        return context


class _ExplodingStage(BaseStage):
    name = "exploding_stage"

    def process(self, context: PipelineContext) -> PipelineContext:
        raise StageError(self.name, "boom", RuntimeError("kaboom"))


class _FakePath:
    """Minimal stand-in for a Path with a .stem and str()."""

    def __init__(self, stem: str):
        self.stem = stem

    def __str__(self) -> str:
        return f"memory://{self.stem}.png"


def test_run_one_continues_past_failed_stage():
    ctx = run_one(_FakePath("ad_42"), [_OkStage(), _ExplodingStage(), _OkStage()])
    assert "exploding_stage" in ctx.failed_stages
    assert ctx.ad_id == "ad_42"  # later stage still ran


def test_process_folder_writes_valid_json(tmp_path):
    # Drop two images.
    for name in ("a", "b"):
        (tmp_path / f"{name}.png").write_bytes(make_image_bytes(64, 64, (1, 2, 3)))
    out_dir = tmp_path / "out"

    count = process_folder(tmp_path, out_dir, [_OkStage()])
    assert count == 2

    for name in ("a", "b"):
        doc = json.loads((out_dir / f"{name}.json").read_text())
        # Output validates against the frozen contract.
        ExtractionResult.model_validate(doc)
        assert doc["ad_id"] == name
        # Stage 4 is deferred -> verification stays null.
        assert doc["product_verification"]["is_visually_verified_match"] is None
