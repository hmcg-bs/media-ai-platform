"""Datalab → Master Schema mapping tests, against the real plain-convert output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.datalab import mapping
from pipeline.datalab.models import DatalabDocument

_CONVERT = Path(__file__).parent.parent.parent / "scripts" / "datalab_out" / "convert.json"
_EXTRACT = _CONVERT.parent / "extract.json"


@pytest.fixture
def doc() -> DatalabDocument:
    if not _CONVERT.exists():
        pytest.skip("convert.json fixture not present")
    return DatalabDocument.model_validate(json.loads(_CONVERT.read_text()))


@pytest.fixture
def extract() -> dict:
    if not _EXTRACT.exists():
        pytest.skip("extract.json fixture not present")
    d = json.loads(_EXTRACT.read_text())
    return json.loads(d) if isinstance(d, str) else d


def test_copy_runs_clean_and_complete(doc):
    texts = [r.text for r, _ in mapping.copy_runs(doc)]
    assert any("SCOOPS" in t for t in texts)                 # headline preserved
    assert not any("central graphic" in t.lower() for t in texts)  # no description leak
    assert len(texts) == 8


def test_headline_is_the_largest(doc):
    ty = mapping.to_typography_hierarchy(doc)
    assert "SCOOPS" in ty.primary_headline.text and "UNLOCKS" in ty.primary_headline.text
    assert len(ty.secondary_copy) == 7
    assert ty.headline_to_subtext_scale_ratio >= 1.0


def test_copywriting_features(doc):
    cw = mapping.to_copywriting_features(doc)
    assert cw.copy_block_count == 8
    assert cw.headline_word_count == 8
    assert cw.uppercase_ratio == 1.0                          # all-caps ad
    assert cw.total_word_count > 0
    # role-dependent fields need the extract step → default
    assert cw.hook_type == "" and cw.cta_present is False


def test_placement_blocks_and_assets(doc):
    pl = mapping.to_placement(doc)
    n_assets = len(doc.image_blocks())
    n_copy = pl.n_blocks_top + pl.n_blocks_middle + pl.n_blocks_bottom
    assert len(pl.elements) == n_copy + n_assets              # every block placed once
    assert pl.text_alignment == "center"
    assert pl.headline_zone == "top"
    assert 0.0 < pl.whitespace_ratio < 1.0
    assert pl.asset_canvas_coverage > 0


def test_ocr_boxes_are_image_space(doc):
    boxes = mapping.to_ocr_boxes(doc, 1080, 1920)
    assert boxes
    for box in boxes:
        for x, y in box:
            assert 0 <= x <= 1080 and 0 <= y <= 1920         # scaled into image bounds


def test_copywriting_features_role_fields_from_extract(doc, extract):
    cw = mapping.to_copywriting_features(doc, extract)
    assert cw.hook_type == "benefit_led"
    assert cw.cta_present is False
    assert cw.claimed_benefits_count == 5
    assert cw.has_badge is True                              # roles include 'badge'
    assert cw.has_price is False and cw.has_legal is False


def test_marketing_psychology_from_extract(extract):
    from pipeline.models.output_schema import HookFramework

    mp = mapping.to_marketing_psychology(extract)
    assert "ARMRA" in mp.primary_value_proposition
    assert mp.hook_framework == HookFramework.DIRECT_OFFER   # benefit_led → DIRECT_OFFER
