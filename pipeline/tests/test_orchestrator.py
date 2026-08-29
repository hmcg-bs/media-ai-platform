"""Orchestrator — per-stage fallback + end-to-end validation, fully offline."""

from __future__ import annotations

import json
import time

from pipeline.models.output_schema import (
    ExtractionResult,
    HookFramework,
    MarketingPsychology,
    PipelineContext,
)
from pipeline.orchestrator import process_folder, run_one
from pipeline.stages.base_stage import BaseStage, StageError
from pipeline.stages.stage_05_cognitive import CognitiveStage, _DeepReasoningResult
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


class _RequiresBytesStage(BaseStage):
    """Mirrors the real stages' own contract: assert image_bytes is
    pre-supplied, never touch image_path."""

    name = "requires_bytes_stage"

    def process(self, context: PipelineContext) -> PipelineContext:
        assert context.image_bytes is not None, "image_bytes must be pre-supplied"
        context.result.ad_id = f"processed_{len(context.image_bytes)}_bytes"
        return context


def test_run_one_accepts_pre_fetched_image_bytes():
    """Regression: the Step 2 bridge (ingestion/run_step2_pipeline.py)
    fetches each ad's creative image into memory and must be able to hand
    those bytes to the stage chain directly — no image is ever persisted to
    disk (confirmed by reading every stage: all of them operate on
    context.image_bytes, not image_path). run_one's optional image_bytes
    param is what makes that possible without a fake/placeholder file."""
    image_bytes = make_image_bytes(10, 10, (5, 5, 5))
    ctx = run_one(_FakePath("ad_99"), [_RequiresBytesStage()], image_bytes=image_bytes)
    assert ctx.image_bytes == image_bytes
    assert ctx.result.ad_id == f"processed_{len(image_bytes)}_bytes"
    assert ctx.failed_stages == []


def test_run_one_without_image_bytes_defaults_to_none():
    """Existing callers (process_folder, and any test not passing
    image_bytes) must see unchanged behavior — image_bytes stays None,
    letting Stage 1's own file-read fallback do its job as before."""
    ctx = run_one(_FakePath("ad_1"), [_OkStage()])
    assert ctx.image_bytes is None


class _SlowPreStage(BaseStage):
    """A pre-cognitive stage with an artificial delay, to prove run_one runs
    it concurrently with CognitiveStage rather than waiting for it first."""

    name = "slow_pre_stage"

    def __init__(self, delay: float = 0.3):
        self.delay = delay

    def process(self, context: PipelineContext) -> PipelineContext:
        time.sleep(self.delay)
        context.result.ad_id = "chain_ran"
        return context


class _FailingPreStage(BaseStage):
    name = "failing_pre_stage"

    def process(self, context: PipelineContext) -> PipelineContext:
        raise StageError(self.name, "boom", RuntimeError("kaboom"))


class _SlowMockGenAI:
    """Mirrors test_stage_05_cognitive.py's _MockGenAI shape but with an
    artificial delay, so timing-based concurrency tests are meaningful."""

    def __init__(self, delay: float = 0.3):
        self.delay = delay

    def extract_structured(self, *, model, prompt, image_bytes, image_mime_type, schema):
        time.sleep(self.delay)
        if schema is MarketingPsychology:
            return MarketingPsychology(hook_framework=HookFramework.PAS)
        if schema is _DeepReasoningResult:
            return _DeepReasoningResult()
        raise AssertionError(f"unexpected schema {schema}")


class _PostCognitiveMarkerStage(BaseStage):
    name = "post_cognitive_marker"

    def process(self, context: PipelineContext) -> PipelineContext:
        context.result.imagery_description = "post-cognitive ran"
        return context


def _cognitive_ctx() -> PipelineContext:
    return PipelineContext(
        ad_id="t", image_path="m", image_bytes=make_image_bytes(20, 20, (10, 20, 30))
    )


class TestRunOneCognitiveConcurrency:
    """Regression: CognitiveStage's two Gemini calls dominate per-ad latency
    (~18-19s of ~23-27s total, confirmed live). run_one must run the
    pre-cognitive chain concurrently with CognitiveStage, not sequentially —
    the two write disjoint ExtractionResult fields (confirmed by reading every
    stage: technical_metadata/typography_hierarchy/color_profile/ocr_boxes vs.
    marketing_psychology/spatial_and_nested_objects/human_model_analysis)."""

    def test_chain_and_cognitive_both_populate_result(self) -> None:
        ctx = run_one(
            _FakePath("ad_1"),
            [_SlowPreStage(delay=0.05), CognitiveStage(genai_client=_SlowMockGenAI(delay=0.05))],
            image_bytes=make_image_bytes(20, 20, (1, 2, 3)),
        )
        assert ctx.result.ad_id == "chain_ran"
        assert ctx.result.marketing_psychology.hook_framework == HookFramework.PAS
        assert ctx.failed_stages == []

    def test_chain_and_cognitive_run_concurrently_not_sequentially(self) -> None:
        delay = 0.3
        start = time.monotonic()
        run_one(
            _FakePath("ad_1"),
            [_SlowPreStage(delay=delay), CognitiveStage(genai_client=_SlowMockGenAI(delay=delay))],
            image_bytes=make_image_bytes(20, 20, (1, 2, 3)),
        )
        elapsed = time.monotonic() - start
        # Sequential would take ~2*delay (0.6s); concurrent should be ~delay (0.3s).
        assert elapsed < delay * 1.7

    def test_pre_chain_failure_does_not_block_cognitive_result(self) -> None:
        ctx = run_one(
            _FakePath("ad_1"),
            [_FailingPreStage(), CognitiveStage(genai_client=_SlowMockGenAI(delay=0.01))],
            image_bytes=make_image_bytes(20, 20, (1, 2, 3)),
        )
        assert "failing_pre_stage" in ctx.failed_stages
        assert ctx.result.marketing_psychology.hook_framework == HookFramework.PAS

    def test_cognitive_failure_does_not_block_chain_result(self) -> None:
        class _FailingGenAI:
            def extract_structured(self, **kwargs):
                raise RuntimeError("cognitive backend down")

        ctx = run_one(
            _FakePath("ad_1"),
            [_SlowPreStage(delay=0.01), CognitiveStage(genai_client=_FailingGenAI())],
            image_bytes=make_image_bytes(20, 20, (1, 2, 3)),
        )
        assert ctx.result.ad_id == "chain_ran"
        # CognitiveStage degrades per-tier internally (fallback_applied), so
        # process() itself doesn't raise StageError here — no crash either way.

    def test_post_cognitive_stages_still_run_after_join(self) -> None:
        ctx = run_one(
            _FakePath("ad_1"),
            [
                _SlowPreStage(delay=0.01),
                CognitiveStage(genai_client=_SlowMockGenAI(delay=0.01)),
                _PostCognitiveMarkerStage(),
            ],
            image_bytes=make_image_bytes(20, 20, (1, 2, 3)),
        )
        assert ctx.result.imagery_description == "post-cognitive ran"


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
