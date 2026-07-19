"""HTML-parse tests for the Datalab ingestion models, using the real ARMRA output."""

from __future__ import annotations

from pipeline.datalab.html_parse import parse_block_html, parse_style
from pipeline.datalab.models import DatalabDocument

# ── real Style-Preserver block HTML (from meta_ad_exp.jpg) ──────────────

_FACT = '<p style="color: #333333; text-align: center;">FACT</p>'

_FIGURE = (
    '<img alt="Diagram showing a scoop of Armra Colostrum powder." src="a_img.jpg"/>'
    '<div class="img-description" style="border: 1px solid #ccc;">'
    '<p style="color: #333333; display: none;">A central graphic shows a white scoop.</p>'
    '<p style="color: #333333; text-align: center;">STRENGTHENED<br/>IMMUNITY</p>'
    '<p style="color: #333333; text-align: right;">VITALIZED<br/>HAIR</p>'
    '<p style="color: #333333; text-align: right;">ENHANCED<br/>SKIN</p>'
    '<p style="color: #333333; text-align: right;">COMBATTED<br/>BLOATING</p>'
    '<p style="color: #333333; text-align: center;">'
    'AND THOUSANDS<br/>MORE WHOLE BODY<br/>BENEFITS</p>'
    '<div class="img-alt">Diagram showing a scoop of Armra Colostrum powder.</div></div>'
)

_LOGO = (
    '<p style="color: #333333; text-align: center;">'
    '<b style="color: #333333;">ARMRA</b>™<br/>COLOSTRUM</p>'
)

_PICTURE = (
    '<img alt="A hand holding a scoop of white powder." src="b_img.jpg"/>'
    '<div class="img-description">'
    '<p style="color: #333333; display: none;">A close-up photograph of a hand.</p>'
    '<div class="img-alt">A hand holding a scoop of white powder.</div></div>'
)


def _document() -> DatalabDocument:
    data = {
        "children": [{
            # A Page block carries the CONCATENATED html of its children — parsing it
            # too would double-count every run, so leaf-only parsing must skip it.
            "id": "/page/0/Page/0", "block_type": "Page",
            "html": _FACT + _FIGURE + _LOGO + _PICTURE, "page": 0,
            "bbox": [0, 0, 1092, 1932],
            "children": [
                {"id": "/page/0/Text/0", "block_type": "Text", "html": _FACT,
                 "bbox": [482.664, 361.284, 603.876, 407.652]},
                {"id": "/page/0/Diagram/2", "block_type": "Figure", "html": _FIGURE,
                 "bbox": [159.432, 792.12, 698.88, 1458.66]},
                {"id": "/page/0/Text/3", "block_type": "Text", "html": _LOGO,
                 "bbox": [386.568, 1547.532, 697.788, 1669.248]},
                {"id": "/page/0/Picture/4", "block_type": "Picture", "html": _PICTURE,
                 "bbox": [681.408, 668.472, 1092, 1634.472]},
            ],
        }],
        "metadata": {"page_stats": [{"page_id": 0, "num_blocks": 5}]},
    }
    return DatalabDocument.model_validate(data)


# ── unit: parse_style / parse_block_html ───────────────────────────────

def test_parse_style():
    assert parse_style("color: #333333; text-align: right;") == {
        "color": "#333333", "text-align": "right"
    }
    assert parse_style(None) == {}


def test_bold_and_linebreak():
    content = parse_block_html(_LOGO)
    assert len(content.runs) == 1
    run = content.runs[0]
    assert run.text == "ARMRA™\nCOLOSTRUM"
    assert run.bold is True
    assert run.text_align == "center"


def test_hidden_description_split_from_runs():
    content = parse_block_html(_FIGURE)
    # 5 visible labels; hidden description not among them
    assert len(content.runs) == 5
    assert content.image_description == "A central graphic shows a white scoop."
    assert content.image_alt == "Diagram showing a scoop of Armra Colostrum powder."
    assert all("central graphic" not in r.text for r in content.runs)


# ── document-level ─────────────────────────────────────────────────────

def test_seven_visible_runs_in_reading_order():
    runs = _document().all_text_runs()
    assert [r.text.replace("\n", " ") for r in runs] == [
        "FACT",
        "STRENGTHENED IMMUNITY",
        "VITALIZED HAIR",
        "ENHANCED SKIN",
        "COMBATTED BLOATING",
        "AND THOUSANDS MORE WHOLE BODY BENEFITS",
        "ARMRA™ COLOSTRUM",
    ]


def test_alignment_and_color():
    by_text = {r.text.split("\n")[0]: r for r in _document().all_text_runs()}
    assert by_text["FACT"].style.text_align == "center"
    assert by_text["VITALIZED"].style.text_align == "right"
    assert by_text["ENHANCED"].style.text_align == "right"
    assert by_text["AND THOUSANDS"].style.text_align == "center"
    assert all(r.style.color_reported == "#333333" for r in _document().all_text_runs())


def test_bold_only_on_logo():
    bold = [r for r in _document().all_text_runs() if r.style.bold]
    assert len(bold) == 1
    assert bold[0].text.startswith("ARMRA")


def test_font_size_proxy_only_on_single_run_blocks():
    runs = {r.block_id: r for r in _document().all_text_runs() if r.style.font_size_pct_canvas}
    # FACT and ARMRA COLOSTRUM are single-run blocks → size filled
    assert "/page/0/Text/0" in runs
    assert "/page/0/Text/3" in runs
    # Figure labels share one bbox → no size
    figure_runs = [
        r for r in _document().all_text_runs() if r.block_id == "/page/0/Diagram/2"
    ]
    assert len(figure_runs) == 5
    assert all(r.style.font_size_pct_canvas is None for r in figure_runs)


def test_image_descriptions_captured():
    blocks = {b.id: b for b in _document().blocks()}
    assert blocks["/page/0/Diagram/2"].image_description == "A central graphic shows a white scoop."
    assert blocks["/page/0/Picture/4"].image_description == "A close-up photograph of a hand."
    assert blocks["/page/0/Picture/4"].text_runs == []  # picture has no visible copy


def test_round_trips():
    doc = _document()
    again = DatalabDocument.model_validate(doc.model_dump())
    assert [r.text for r in again.all_text_runs()] == [r.text for r in doc.all_text_runs()]
