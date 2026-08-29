"""Tests for pipeline/generation/masking.py -- Round 6's mask-construction
utility (see background.py's own module docstring for why this exists:
Flux Kontext's whole-image edit was silently garbling product label text,
and a mask-protected inpaint structurally prevents that instead of just
asking the model nicely)."""

from __future__ import annotations

import io

from PIL import Image

from pipeline.generation.masking import build_inpaint_mask


def _rgba_with_center_square(size: int = 40, square: tuple[int, int] = (10, 30)) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    lo, hi = square
    for x in range(lo, hi):
        for y in range(lo, hi):
            img.putpixel((x, y), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestBuildInpaintMask:
    def test_returns_valid_grayscale_png_same_size_as_input(self):
        cutout = _rgba_with_center_square(size=40)
        mask_bytes = build_inpaint_mask(cutout)

        mask = Image.open(io.BytesIO(mask_bytes))
        assert mask.mode == "L"
        assert mask.size == (40, 40)

    def test_product_region_is_black_background_region_is_white(self):
        """Flux Fill's own convention (confirmed against its schema): black
        = preserved, white = inpainted. The product sits at [10,30)x[10,30)
        in a 40x40 canvas -- its center must be black (preserve), and a
        corner far from the product must be white (inpaint). Feathering
        disabled here to isolate the pure preserve/inpaint logic from the
        edge-softening behavior (covered separately below)."""
        cutout = _rgba_with_center_square(size=40, square=(10, 30))
        mask = Image.open(io.BytesIO(build_inpaint_mask(cutout, dilate_px=0, feather_radius=0)))

        assert mask.getpixel((20, 20)) < 50  # center of product -> preserved
        assert mask.getpixel((2, 2)) > 200  # far corner -> inpainted

    def test_dilation_grows_the_preserved_region_outward(self):
        """A safety margin around the product's real silhouette means a
        pixel just outside the raw alpha boundary must still come back black
        (preserved) once dilation is applied, not just anything strictly
        inside the original square -- this is what protects label text right
        at the product's edge from being exposed to inpainting."""
        cutout = _rgba_with_center_square(size=40, square=(10, 30))
        no_dilate = Image.open(
            io.BytesIO(build_inpaint_mask(cutout, dilate_px=0, feather_radius=0))
        )
        dilated = Image.open(
            io.BytesIO(build_inpaint_mask(cutout, dilate_px=12, feather_radius=0))
        )

        # Just outside the raw square (x=32) -- undilated must be white
        # (inpaint), dilated must be black (now protected by the margin).
        assert no_dilate.getpixel((32, 20)) > 200
        assert dilated.getpixel((32, 20)) < 50

    def test_fully_transparent_cutout_yields_all_white_mask(self):
        img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mask = Image.open(io.BytesIO(build_inpaint_mask(buf.getvalue())))

        assert mask.getpixel((10, 10)) > 200

    def test_feathering_softens_the_preserve_inpaint_boundary(self):
        """Round 6 live finding: a hard 0/255 mask edge left a visible white
        halo ring (the raw original background, preserved verbatim right up
        to a cliff) around the product in the generated scene. Feathering
        must leave a gradient near the boundary rather than a hard step."""
        cutout = _rgba_with_center_square(size=40, square=(10, 30))
        hard = Image.open(io.BytesIO(build_inpaint_mask(cutout, dilate_px=0, feather_radius=0)))
        feathered = Image.open(
            io.BytesIO(build_inpaint_mask(cutout, dilate_px=0, feather_radius=3))
        )

        # Right at the product's edge (x=30, just outside the square), the
        # hard mask jumps straight to white; the feathered mask must sit
        # somewhere between preserved and inpainted, not at either extreme.
        assert hard.getpixel((30, 20)) > 200
        edge_value = feathered.getpixel((30, 20))
        assert 20 < edge_value < 235
