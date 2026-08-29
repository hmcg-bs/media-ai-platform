"""Stage 2 OCR/typography — deterministic hierarchy math with mocked Vision."""

from __future__ import annotations

from pipeline.clients.vision_client import OcrBlock
from pipeline.models.output_schema import PipelineContext, TechnicalMetadata
from pipeline.stages.stage_02_ocr import (
    OCRStage,
    build_hierarchy,
    derive_copywriting_features,
    derive_placement,
)


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


class TestDeriveCopywritingFeatures:
    """Regression: CopywritingFeatures used to be populated only by the
    Datalab-only stage (unused in this pipeline), leaving all 16 fields
    structurally at default for every ad. These functions derive the same
    signals from Cloud Vision's OCR output instead."""

    def test_empty_blocks_returns_defaults(self):
        cw = derive_copywriting_features([], "")
        assert cw.copy_block_count == 0
        assert cw.total_word_count == 0

    def test_word_and_char_counts(self):
        blocks = [
            OcrBlock(text="Buy Now", vertices=_box(0, 0, 10, 10)),
            OcrBlock(text="Limited time offer", vertices=_box(0, 20, 10, 30)),
        ]
        cw = derive_copywriting_features(blocks, "Buy Now")
        assert cw.copy_block_count == 2
        assert cw.total_word_count == 5  # "Buy" "Now" "Limited" "time" "offer"
        assert cw.headline_word_count == 2
        assert cw.headline_char_count == 7
        assert cw.avg_words_per_block == 2.5

    def test_uppercase_ratio(self):
        blocks = [OcrBlock(text="AB cd", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        # letters: A,B,c,d -> uppercase A,B = 2/4
        assert cw.uppercase_ratio == 0.5

    def test_punctuation_and_emoji_counts(self):
        blocks = [OcrBlock(text="Wow!! Really? \U0001F525", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.exclamation_count == 2
        assert cw.question_count == 1
        assert cw.emoji_count == 1

    def test_reading_grade_level_is_nonzero_for_real_text(self):
        blocks = [
            OcrBlock(
                text="This comprehensive formula supports cardiovascular health.",
                vertices=_box(0, 0, 10, 10),
            )
        ]
        cw = derive_copywriting_features(blocks, "")
        assert cw.reading_grade_level > 0

    def test_cta_present_detects_known_phrase(self):
        blocks = [OcrBlock(text="Shop Now and save 20%", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.cta_present is True

    def test_cta_present_false_without_cta_phrase(self):
        blocks = [OcrBlock(text="A great supplement for you", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.cta_present is False

    def test_has_price_detects_dollar_amount(self):
        blocks = [OcrBlock(text="Only $19.99 today", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.has_price is True

    def test_has_price_false_without_currency(self):
        blocks = [OcrBlock(text="30 servings per bottle", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.has_price is False

    def test_has_legal_detects_disclaimer_phrase(self):
        blocks = [OcrBlock(text="Individual results may vary", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.has_legal is True

    def test_role_fields_without_semantic_classification_stay_default(self):
        """Datalab-only fields (needs real semantic role classification, not
        available from Cloud Vision) must stay at schema default, not guessed."""
        blocks = [OcrBlock(text="Shop Now! Only $19.99", vertices=_box(0, 0, 10, 10))]
        cw = derive_copywriting_features(blocks, "")
        assert cw.has_badge is False
        assert cw.hook_type == ""
        assert cw.claimed_benefits_count == 0


class TestDerivePlacement:
    """Regression: Placement used to be populated only by the Datalab-only
    stage, leaving all 10 fields structurally at default. Derived here from
    Cloud Vision's OCR bounding boxes instead — pure geometry, no semantic
    gap for the fields this covers."""

    def test_empty_blocks_returns_defaults(self):
        placement = derive_placement([], "", 100, 100)
        assert placement.copy_canvas_coverage == 0.0
        assert placement.elements == []

    def test_copy_canvas_coverage(self):
        # 200x200 canvas; one 50x50 block -> 2500/40000 = 0.0625
        blocks = [OcrBlock(text="X", vertices=_box(0, 0, 50, 50))]
        placement = derive_placement(blocks, "", 200, 200)
        assert placement.copy_canvas_coverage == 0.0625
        assert placement.whitespace_ratio == round(1 - 0.0625, 4)

    def test_headline_zone_and_center(self):
        # Headline in the top third of a 300-tall canvas.
        blocks = [
            OcrBlock(text="HEADLINE", vertices=_box(0, 0, 100, 20)),
            OcrBlock(text="body copy down here", vertices=_box(0, 250, 100, 280)),
        ]
        placement = derive_placement(blocks, "HEADLINE", 200, 300)
        assert placement.headline_zone == "top"
        assert placement.n_blocks_top == 1
        assert placement.n_blocks_bottom == 1
        assert placement.n_blocks_middle == 0

    def test_center_alignment(self):
        # Both blocks centered around x=0.5 of a 200-wide canvas.
        blocks = [
            OcrBlock(text="a", vertices=_box(80, 0, 120, 10)),
            OcrBlock(text="b", vertices=_box(85, 20, 115, 30)),
        ]
        placement = derive_placement(blocks, "", 200, 100)
        assert placement.text_alignment == "center"

    def test_left_alignment(self):
        blocks = [
            OcrBlock(text="a", vertices=_box(0, 0, 20, 10)),
            OcrBlock(text="b", vertices=_box(0, 20, 30, 30)),
        ]
        placement = derive_placement(blocks, "", 200, 100)
        assert placement.text_alignment == "left"

    def test_asset_and_balance_fields_stay_default_not_fabricated(self):
        """Text-only OCR can't detect image/product regions, so these must
        stay at schema default rather than being guessed at."""
        blocks = [OcrBlock(text="X", vertices=_box(0, 0, 50, 50))]
        placement = derive_placement(blocks, "", 200, 200)
        assert placement.asset_canvas_coverage == 0.0
        assert placement.copy_vs_image_balance == 0.0


def test_stage_populates_copywriting_features_and_placement():
    """Integration: OCRStage.process() must wire both new derivations in,
    not just typography_hierarchy — the whole point of this fix."""
    class _MockVision:
        def detect_text(self, _bytes):
            return [OcrBlock(text="Shop Now for $9.99", vertices=_box(10, 10, 100, 40))]

    ctx = PipelineContext(ad_id="t", image_path="m", image_bytes=b"x")
    ctx.result.technical_metadata = TechnicalMetadata(width=200, height=200)
    result = OCRStage(vision_client=_MockVision()).process(ctx)

    assert result.result.copywriting_features.copy_block_count == 1
    assert result.result.copywriting_features.cta_present is True
    assert result.result.copywriting_features.has_price is True
    assert result.result.placement.copy_canvas_coverage > 0
