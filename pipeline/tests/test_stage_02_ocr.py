"""Stage 2 OCR/typography — deterministic hierarchy math with mocked Vision."""

from __future__ import annotations

from pipeline.clients.vision_client import OcrBlock
from pipeline.models.output_schema import PipelineContext, TechnicalMetadata
from pipeline.stages.stage_02_ocr import OCRStage, build_hierarchy


def _box(x0, y0, x1, y1) -> list[tuple[int, int]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def test_largest_block_becomes_headline():
    blocks = [
        OcrBlock(text="small print", vertices=_box(0, 0, 50, 10)),       # area 500
        OcrBlock(text="BIG HEADLINE", vertices=_box(0, 0, 200, 60)),     # area 12000
        OcrBlock(text="medium", vertices=_box(0, 0, 100, 30)),           # area 3000
    ]
    hierarchy = build_hierarchy(blocks, canvas_area=40000.0)
    assert hierarchy.primary_headline.text == "BIG HEADLINE"
    assert [b.text for b in hierarchy.secondary_copy] == ["medium", "small print"]
    # headline (12000) / next-largest secondary (3000) = 4.0
    assert hierarchy.headline_to_subtext_scale_ratio == 4.0
    # coverage = 12000 / 40000 * 100 = 30%
    assert hierarchy.primary_headline.canvas_coverage_percentage == 30.0


def test_empty_ocr_yields_empty_hierarchy():
    hierarchy = build_hierarchy([], canvas_area=40000.0)
    assert hierarchy.primary_headline.text == ""
    assert hierarchy.secondary_copy == []
    assert hierarchy.headline_to_subtext_scale_ratio == 0.0


def test_stage_passes_boxes_to_context_for_stage3():
    class _MockVision:
        def detect_text(self, _bytes):
            return [OcrBlock(text="HELLO", vertices=_box(10, 10, 60, 30))]

    ctx = PipelineContext(ad_id="t", image_path="m", image_bytes=b"x")
    ctx.result.technical_metadata = TechnicalMetadata(width=200, height=200)
    result = OCRStage(vision_client=_MockVision()).process(ctx)

    assert result.result.typography_hierarchy.primary_headline.text == "HELLO"
    assert result.ocr_boxes == [_box(10, 10, 60, 30)]
