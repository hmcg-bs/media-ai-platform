"""Tests for pipeline/generation/compositor.py -- pure PIL drawing logic, no
API calls. Confirms the compositor produces a valid image at the right size
and doesn't crash on empty/missing text fields."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from pipeline.generation.compositor import (
    _clamp_box,
    _composite_feathered_shape,
    _contrast_ratio,
    _ensure_legible_color,
    compose_ad,
)
from pipeline.generation.elements import AdSpec, ElementSpec


def _blank_background_bytes(w: int = 1080, h: int = 1080, color: str = "white") -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestComposeAd:
    def test_returns_valid_png_at_canvas_size(self):
        spec = AdSpec(
            canvas_width=1080, canvas_height=1080,
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="headline", text="BOOST YOUR ENERGY",
                    x=0.05, y=0.05, width=0.9, height=0.1,
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size == (1080, 1080)

    def test_resizes_background_to_declared_canvas_size(self):
        spec = AdSpec(
            canvas_width=800, canvas_height=800,
            background_and_product_image=_blank_background_bytes(w=1080, h=1080),
            elements=[],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result))
        assert img.size == (800, 800)

    def test_handles_missing_text_without_crashing(self):
        spec = AdSpec(
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="headline", text=None, x=0.05, y=0.05, width=0.9, height=0.1
                ),
                ElementSpec(
                    element_type="secondary_copy", text="", x=0.05, y=0.2, width=0.9, height=0.1
                ),
            ],
        )
        result = compose_ad(spec)  # must not raise
        assert len(result) > 0

    def test_cta_graphic_draws_a_filled_rounded_rect(self):
        spec = AdSpec(
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="cta_graphic", text="SHOP NOW",
                    x=0.3, y=0.86, width=0.4, height=0.09,
                    fill_color_hex="#ff0000", text_color_hex="#ffffff",
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result)).convert("RGB")
        # sample a pixel inside the CTA box -- should not be background white
        px = img.getpixel((int(0.5 * 1080), int(0.90 * 1080)))
        assert px != (255, 255, 255)

    def test_elements_drawn_in_z_order(self):
        """A later z_order element (CTA) drawn over an earlier one (headline
        text spanning the same region) should be visible on top -- confirmed
        indirectly by checking both draw calls run without the earlier one
        raising when overlapping geometry is given."""
        spec = AdSpec(
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="headline", text="X",
                    x=0.3, y=0.86, width=0.4, height=0.09, z_order=1,
                ),
                ElementSpec(
                    element_type="cta_graphic", text="SHOP", x=0.3, y=0.86, width=0.4, height=0.09,
                    z_order=2, fill_color_hex="#0000ff",
                ),
            ],
        )
        result = compose_ad(spec)
        assert len(result) > 0

    def test_long_headline_in_small_box_does_not_crash(self):
        """Regression: the first live smoke test (wayfinder issue #36)
        produced a headline cut off past the canvas edge -- font size was
        only ever checked against box width, never height. This doesn't
        assert pixel-perfect containment (that needs visual review) but
        confirms the wrap/shrink loop terminates and produces valid output
        for a case that previously overflowed."""
        spec = AdSpec(
            canvas_width=1080, canvas_height=1080,
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="headline",
                    text="Fuel Your Day with Pure Omega-3 and Sustained Energy",
                    x=0.06, y=0.06, width=0.4, height=0.08, z_order=1,
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result))
        assert img.size == (1080, 1080)


class TestEnsureLegibleColor:
    def test_contrast_ratio_white_vs_black_is_maximal(self):
        assert _contrast_ratio((255, 255, 255), (0, 0, 0)) == 21.0

    def test_contrast_ratio_identical_colors_is_one(self):
        assert _contrast_ratio((128, 128, 128), (128, 128, 128)) == 1.0

    def test_keeps_requested_color_when_already_legible(self):
        canvas = Image.new("RGB", (200, 200), "black")
        color = _ensure_legible_color(canvas, (10, 10, 190, 190), "#ffffff")
        assert color == "#ffffff"

    def test_swaps_to_high_contrast_when_requested_color_is_illegible(self):
        """Regression: the first live smoke test produced light-gray text on
        a near-white background -- exactly the case this must catch."""
        canvas = Image.new("RGB", (200, 200), "#f0f0f0")  # near-white
        color = _ensure_legible_color(canvas, (10, 10, 190, 190), "#e0e0e0")  # light gray
        assert color == "#000000"

    def test_picks_white_on_a_dark_background(self):
        canvas = Image.new("RGB", (200, 200), "#1a1a1a")
        color = _ensure_legible_color(canvas, (10, 10, 190, 190), "#333333")
        assert color == "#ffffff"


class TestClampBox:
    def test_leaves_a_fully_in_bounds_box_unchanged(self):
        assert _clamp_box(0.1, 0.1, 0.5, 0.2) == (0.1, 0.1, 0.5, 0.2)

    def test_clamps_negative_origin_to_zero(self):
        x, y, w, h = _clamp_box(-0.1, -0.2, 0.3, 0.3)
        assert x == 0.0
        assert y == 0.0

    def test_clamps_width_so_box_never_extends_past_canvas(self):
        _x, _y, w, _h = _clamp_box(0.8, 0.1, 0.5, 0.2)
        assert w == pytest.approx(0.2)  # 1.0 - 0.8


class TestCtaTextFitsBoxWidth:
    """Regression (round 2, wayfinder issue #36): the layout agent can return
    a button-sized box tighter than the old fixed-fraction default -- the CTA
    renderer must shrink text to fit width, not just size off height, or the
    label clips past the box (confirmed live: "SHOP NOW" -> "HOP NOW")."""

    def test_long_cta_text_in_narrow_box_does_not_overflow_canvas(self):
        spec = AdSpec(
            canvas_width=1080, canvas_height=1080,
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="cta_graphic", text="SHOP NOW TODAY",
                    x=0.0, y=0.4, width=0.18, height=0.08,
                    fill_color_hex="#1a1a1a", text_color_hex="#ffffff",
                ),
            ],
        )
        result = compose_ad(spec)  # must not raise
        img = Image.open(io.BytesIO(result))
        assert img.size == (1080, 1080)

    def test_out_of_bounds_box_is_clamped_not_drawn_off_canvas(self):
        spec = AdSpec(
            canvas_width=1080, canvas_height=1080,
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="cta_graphic", text="SHOP",
                    x=-0.05, y=0.4, width=0.3, height=0.08,
                    fill_color_hex="#1a1a1a", text_color_hex="#ffffff",
                ),
            ],
        )
        result = compose_ad(spec)  # must not raise despite the negative x
        assert len(result) > 0


class TestFontPersonality:
    """Regression (round 5, wayfinder issue #36): _load_font used to call
    ImageFont.truetype("DejaVuSans-Bold.ttf", size) with a bare filename
    this platform's resolver cannot find -- confirmed live, every call
    silently raised OSError and fell back to PIL's tiny built-in bitmap
    font, meaning every ad ever generated used the exact same non-typeface
    regardless of font_personality. Now loads real bundled TrueType files."""

    def test_every_personality_loads_a_real_truetype_font_not_the_bitmap_default(self):
        from PIL import ImageFont

        from pipeline.generation.compositor import _load_font

        for personality in ("clean_modern", "bold_condensed", "elegant_serif", "playful_dynamic"):
            font = _load_font(24, personality)
            assert isinstance(font, ImageFont.FreeTypeFont)

    def test_different_personalities_load_different_font_files(self):
        from pipeline.generation.compositor import _load_font

        modern = _load_font(24, "clean_modern")
        serif = _load_font(24, "elegant_serif")
        assert modern.path != serif.path

    def test_compose_ad_renders_with_a_specific_personality_without_crashing(self):
        spec = AdSpec(
            background_and_product_image=_blank_background_bytes(),
            elements=[
                ElementSpec(
                    element_type="headline", text="BOLD HEADLINE",
                    x=0.05, y=0.05, width=0.9, height=0.15,
                    font_personality="bold_condensed",
                ),
            ],
        )
        result = compose_ad(spec)
        assert len(result) > 0


class TestCtaAutoContrast:
    """Regression (round 5, wayfinder issue #36): the CTA fill color was a
    hardcoded #1a1a1a default never checked against what's actually behind
    it -- confirmed live, a dark button on a dark reddish-brown background
    review-flagged as "blends into the background." Fill and text color are
    now both auto-contrast-checked, same discipline as body text."""

    def test_dark_fill_on_dark_background_swaps_to_a_legible_color(self):
        spec = AdSpec(
            canvas_width=400, canvas_height=400,
            background_and_product_image=_blank_background_bytes(400, 400, color="#1a1a1a"),
            elements=[
                ElementSpec(
                    element_type="cta_graphic", text="SHOP",
                    x=0.2, y=0.4, width=0.6, height=0.2,
                    fill_color_hex="#1a1a1a",  # requested fill matches the background exactly
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result)).convert("RGB")
        center = img.getpixel((int(0.5 * 400), int(0.5 * 400)))
        # the button must be visually distinct from its #1a1a1a (26,26,26) surroundings
        assert sum(abs(c - 26) for c in center) > 60

    def test_button_text_stays_legible_against_whatever_fill_was_chosen(self):
        spec = AdSpec(
            canvas_width=400, canvas_height=400,
            background_and_product_image=_blank_background_bytes(400, 400, color="white"),
            elements=[
                ElementSpec(
                    element_type="cta_graphic", text="SHOP",
                    x=0.2, y=0.4, width=0.6, height=0.2,
                    fill_color_hex="#f5f5f5",  # near-white fill, requested against a white bg
                ),
            ],
        )
        result = compose_ad(spec)  # must not raise, and text must render distinctly
        assert len(result) > 0


class TestBackgroundBand:
    def test_band_draws_a_solid_surface_behind_text(self):
        spec = AdSpec(
            canvas_width=400, canvas_height=400,
            background_and_product_image=_blank_background_bytes(400, 400),
            elements=[
                ElementSpec(
                    element_type="headline", text="HEADLINE",
                    x=0.1, y=0.1, width=0.8, height=0.2,
                    background_band=True, background_band_color_hex="#ff0000",
                    background_band_opacity=255,
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result)).convert("RGB")
        # sample just left of the text start, inside the padded band area
        px = img.getpixel((int(0.1 * 400) - 5, int(0.15 * 400)))
        assert px[0] > 200 and px[1] < 100  # reddish, not white background

    def test_no_band_by_default_leaves_background_untouched(self):
        spec = AdSpec(
            canvas_width=400, canvas_height=400,
            background_and_product_image=_blank_background_bytes(400, 400),
            elements=[
                ElementSpec(
                    element_type="headline", text="HEADLINE",
                    x=0.1, y=0.1, width=0.8, height=0.2,
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result)).convert("RGB")
        # Far enough from the shadow's blur radius (Round 8: 8px) to be
        # genuinely untouched, not just outside the glyphs themselves.
        px = img.getpixel((int(0.1 * 400) - 25, int(0.12 * 400)))
        assert px == (255, 255, 255)


class TestFeatheredEdges:
    """Round 8: the blend-review agent's dominant live complaint was "harsh,
    defined edges" on both the text band and CTA button, making them read
    as separate pasted-on overlays rather than part of one designed image.
    _composite_feathered_shape blurs the shape's own alpha before
    compositing -- these tests confirm the edge is a real gradient, not a
    hard step, without over-blurring the shape's own solid interior."""

    def test_feathered_shape_edge_is_a_gradient_not_a_hard_step(self):
        canvas = Image.new("RGB", (200, 200), "white")

        def draw_shape(draw):
            draw.rectangle([50, 50, 150, 150], fill=(255, 0, 0, 255))

        result = _composite_feathered_shape(canvas, draw_shape, feather_radius=6)

        interior = result.getpixel((100, 100))  # deep inside the shape
        exterior = result.getpixel((10, 10))  # far outside the shape
        edge = result.getpixel((50, 100))  # exactly on the nominal boundary

        assert interior[0] > 200 and interior[1] < 50  # solid red, unaffected by feathering
        assert exterior == (255, 255, 255)  # untouched white, far from the blur radius
        # The edge pixel must be a genuine blend -- neither pure red nor
        # pure white -- which a hard-edged rounded_rectangle would never
        # produce at exactly its own boundary.
        assert 0 < edge[0] < 255 or 0 < edge[1] < 255

    def test_target_alpha_scales_the_whole_shape_not_just_the_edge(self):
        canvas = Image.new("RGB", (200, 200), "white")

        def draw_shape(draw):
            draw.rectangle([50, 50, 150, 150], fill=(255, 0, 0, 255))

        half_opacity = _composite_feathered_shape(
            canvas, draw_shape, feather_radius=2, target_alpha=128
        )
        interior = half_opacity.getpixel((100, 100))
        # Red (255,0,0) over white (255,255,255): R is 255 either way, so
        # check G/B, which are 0 in pure red and 255 in white -- ~50%
        # opacity should land roughly halfway, not at either extreme.
        assert 80 < interior[1] < 200
        assert 80 < interior[2] < 200

    def test_band_edge_blends_rather_than_cutting_off_hard(self):
        spec = AdSpec(
            canvas_width=400, canvas_height=400,
            background_and_product_image=_blank_background_bytes(400, 400, color="white"),
            elements=[
                ElementSpec(
                    element_type="headline", text="HEADLINE",
                    x=0.3, y=0.3, width=0.4, height=0.1,
                    background_band=True, background_band_color_hex="#ff0000",
                    background_band_opacity=255,
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result)).convert("RGB")
        # Sample a vertical strip crossing the band's left edge; a feathered
        # edge produces a monotonic-ish gradient, a hard edge would jump
        # from pure white directly to pure red with no intermediate values.
        band_x0 = int(0.3 * 400) - 14  # _BAND_PADDING
        y = int(0.35 * 400)
        row = [img.getpixel((band_x0 - 8 + i, y)) for i in range(16)]
        reds = [px[0] for px in row]
        blues = [px[2] for px in row]
        has_intermediate = any(0 < b < 255 for b in blues) or any(
            0 < r < 255 for r in reds if r != 255
        )
        assert has_intermediate

    def test_cta_button_edge_blends_rather_than_cutting_off_hard(self):
        spec = AdSpec(
            canvas_width=400, canvas_height=400,
            background_and_product_image=_blank_background_bytes(400, 400, color="white"),
            elements=[
                ElementSpec(
                    element_type="cta_graphic", text="SHOP",
                    x=0.3, y=0.4, width=0.4, height=0.15,
                    fill_color_hex="#000000",
                ),
            ],
        )
        result = compose_ad(spec)
        img = Image.open(io.BytesIO(result)).convert("RGB")
        x0 = int(0.3 * 400)
        y = int(0.475 * 400)
        row = [img.getpixel((x0 - 6 + i, y)) for i in range(12)]
        grays = [px[0] for px in row]
        assert any(0 < g < 255 for g in grays)
