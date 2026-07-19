"""Background colour-profile tests (deterministic K-Means over a layer)."""

from __future__ import annotations

from pipeline.color.background import background_color_profile
from pipeline.tests.conftest import make_image_bytes


def test_solid_background_profile():
    # A solid layer → single dominant colour, Studio style.
    cp = background_color_profile(make_image_bytes(80, 80, (220, 30, 30)), k=3)
    assert cp.background_hex == "#DC1E1E"          # RGB(220,30,30)
    assert cp.background_style == "Studio"          # near-zero variance
    assert cp.dominant_hex_palette                  # non-empty
    assert all(h == "#DC1E1E" for h in cp.dominant_hex_palette)


def test_two_tone_background_has_both_colors():
    # Left half red, right half blue → palette should surface both.
    import io

    import numpy as np
    from PIL import Image

    arr = np.zeros((60, 60, 3), dtype=np.uint8)
    arr[:, :30] = (220, 30, 30)   # red
    arr[:, 30:] = (30, 30, 220)   # blue
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")

    cp = background_color_profile(buf.getvalue(), k=2)
    palette = set(cp.dominant_hex_palette)
    assert "#DC1E1E" in palette
    assert "#1E1EDC" in palette
    assert cp.background_style == "Busy"            # high variance across halves
