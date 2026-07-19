"""Pixel-sampled colour (color_measured) tests — offline, synthetic images, no cv2/network."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from pipeline.datalab.color import measure_text_colors, text_color_hex
from pipeline.datalab.models import DatalabDocument


def _image_with_dark_text(size: int = 100, fg=(30, 40, 50)) -> bytes:
    """White image with a dark band over the top 20% — the minority = 'text'."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    arr = np.asarray(img).copy()
    arr[: int(size * 0.2), :] = fg
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _doc(block_type: str, html: str) -> DatalabDocument:
    data = {
        "children": [{
            "id": "/page/0/Page/0", "block_type": "Page", "html": "",
            "bbox": [0, 0, 100, 100],
            "children": [{
                "id": "/page/0/B/0", "block_type": block_type, "html": html,
                "bbox": [0, 0, 100, 100],
            }],
        }],
    }
    return DatalabDocument.model_validate(data)


def test_text_color_hex_picks_minority_class():
    arr = np.full((10, 10, 3), 255, dtype=np.uint8)
    arr[:2, :] = (30, 40, 50)  # 20% dark = minority = text
    assert text_color_hex(arr) == "#1E2832"


def test_empty_crop_returns_none():
    assert text_color_hex(np.empty((0, 0, 3), dtype=np.uint8)) is None


def test_measure_fills_single_run_block():
    doc = _doc("Text", '<p style="text-align: center;">HELLO</p>')
    measure_text_colors(doc, _image_with_dark_text())
    run = doc.all_text_runs()[0]
    assert run.style.color_measured == "#1E2832"       # sampled from pixels
    assert run.style.color_reported is None            # no color: in this HTML


def test_multi_run_block_is_not_measured():
    # A leaf block with two <p> → two runs share one bbox → not attributable → skipped.
    doc = _doc("Figure", "<p>ONE</p><p>TWO</p>")
    measure_text_colors(doc, _image_with_dark_text())
    runs = doc.all_text_runs()
    assert len(runs) == 2
    assert all(r.style.color_measured is None for r in runs)
