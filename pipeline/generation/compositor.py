"""Deterministic assembly (Generation v1): places copy/CTA elements onto the
masked-inpaint-produced background+product image (background.py; Flux Fill
as of Round 6, Flux Kontext before it) via plain PIL drawing calls -- no
generative model call, matching ADR-006's deterministic-first principle
and this map's own "code-rendered by default" decision (wayfinder issue #38's
Q5). An AI agent (copywriter.py) decides *what* the text says and where
roughly it should sit (ElementSpec); this module only decides *how the pixels
get drawn*, and does so the same way every time given the same input --
"deterministically but guided by an AI agent," per the map's own framing.

Fixed after the first live smoke test (2026-08-27, wayfinder issue #36):
the original single-line renderer only constrained font size to the box
*width*, so a long headline silently overflowed past the canvas edge instead
of wrapping or shrinking further -- confirmed live (a real ad's headline was
cut off mid-word). Now wraps to multiple lines and shrinks until the whole
block fits both box dimensions. Also adds an auto-contrast check (WCAG-style
relative luminance ratio against the sampled background) since the same
smoke test produced illegible light-gray-on-white copy that the reviewer
agent correctly flagged -- rather than trusting whatever text_color_hex an
upstream agent picked, the compositor verifies it's actually legible against
the real pixels underneath and swaps to black/white if not.

Round 3 (2026-08-27): the new blend/cohesion agent's first live runs
consistently flagged the text/CTA overlay as looking "flat" -- no shadow or
depth cue, unlike the photographic background/product -- which is exactly
what a real design tool adds by default. Each text/CTA draw now composites a
soft, blurred drop-shadow layer underneath itself first (see
_composite_blurred_shadow) -- a standard, cheap depth cue, not a generative
model call.

Round 5 (2026-08-28), a bigger fix: `_load_font` had been calling
`ImageFont.truetype("DejaVuSans-Bold.ttf", size)` with a bare filename this
platform's font resolver cannot actually find -- confirmed live, every call
was silently raising `OSError` and falling through to
`ImageFont.load_default()`, PIL's tiny built-in bitmap font. Every ad
generated before this fix used that same fallback, not a real typeface --
which is exactly why every ad "looked the same." Now loads real bundled
TrueType files (pipeline/generation/assets/fonts/, DejaVu family, free/
redistributable -- see LICENSE_DEJAVU there) keyed by a font_personality the
style-reference agent assigns from real reference ads, and raises loudly
(not a silent fallback) if a bundled file goes missing, so this specific
failure mode can never again hide behind a default that still technically
"works."
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pipeline.generation.elements import AdSpec, ElementSpec, FontPersonality

_MIN_FONT_SIZE = 10
_WCAG_AA_CONTRAST_RATIO = 4.5
_SHADOW_OFFSET = (6, 8)
_SHADOW_BLUR_RADIUS = 6
_SHADOW_OPACITY = 200  # 0-255
# Round 4 (2026-08-28): the first shadow attempt (offset (3,4), blur 3,
# opacity 130) rendered but was confirmed live to be nearly invisible at
# normal viewing size -- the blend-check agent still called the CTA/text
# "flat" with no shadow. Strengthened until visually unmistakable in a
# direct crop-and-zoom check, not just "technically present in the pixels."

_FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
_FONT_FILES: dict[FontPersonality, str] = {
    "clean_modern": "DejaVuSans-Bold.ttf",
    "bold_condensed": "DejaVuSansMono-Bold.ttf",
    "elegant_serif": "DejaVuSerif-Bold.ttf",
    "playful_dynamic": "DejaVuSans-Oblique.ttf",
}
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _clamp_box(
    x: float, y: float, width: float, height: float
) -> tuple[float, float, float, float]:
    """The layout agent's coordinates are a strong prior, not a guarantee
    (see layout.py's own docstring) -- clamps to [0, 1] and ensures the box
    never extends past the canvas, so a slightly-off report degrades to a
    clipped-but-contained box rather than drawing off the visible frame."""
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    width = min(max(width, 0.0), 1.0 - x)
    height = min(max(height, 0.0), 1.0 - y)
    return x, y, width, height


def _composite_blurred_shadow(canvas: Image.Image, draw_shadow) -> Image.Image:
    """Renders a shape/text onto a transparent layer via `draw_shadow(shadow_draw)`
    (which should draw at the real position offset by _SHADOW_OFFSET, in solid
    black), blurs that layer, and alpha-composites it under the canvas's
    existing content. Returns a NEW canvas -- callers must rebuild their
    ImageDraw against the return value, since alpha_composite never mutates
    in place."""
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    draw_shadow(shadow_draw)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR_RADIUS))
    return Image.alpha_composite(canvas.convert("RGBA"), shadow_layer).convert("RGB")


def _load_font(size: int, personality: FontPersonality = "clean_modern") -> ImageFont.FreeTypeFont:
    """Loads a bundled TrueType file by path -- never a bare font-family
    name, which this platform's resolver cannot find (see module docstring).
    Cached per (personality, size) since a single compose_ad call reloads
    the same font repeatedly across the shrink-to-fit loop."""
    cache_key = (personality, size)
    if cache_key not in _font_cache:
        font_path = _FONTS_DIR / _FONT_FILES[personality]
        _font_cache[cache_key] = ImageFont.truetype(str(font_path), size)
    return _font_cache[cache_key]


def _wrap_lines(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    """Greedy word-wrap: adds words to the current line while it still fits
    max_width, else starts a new line. A single word wider than max_width on
    its own is kept as its own (overflowing) line rather than split mid-word
    -- font-size shrinking in the caller is what actually resolves that case."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _block_size(
    draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    line_height = font.size * 1.2
    max_w = max((draw.textbbox((0, 0), line, font=font)[2] for line in lines), default=0)
    return int(max_w), int(line_height * len(lines))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        c_norm = c / 255.0
        return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    lum_a, lum_b = _relative_luminance(rgb_a), _relative_luminance(rgb_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ensure_legible_color(
    canvas: Image.Image, box: tuple[int, int, int, int], requested_hex: str
) -> str:
    """Samples the average background pixel under `box` and returns
    `requested_hex` unchanged if it already meets WCAG AA contrast against
    that sample, else the higher-contrast of pure black/white."""
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(canvas.width, max(x1, x0 + 1)), min(canvas.height, max(y1, y0 + 1))
    region = canvas.crop((x0, y0, x1, y1)).convert("RGB")
    avg_rgb = tuple(int(v) for v in region.resize((1, 1)).getpixel((0, 0)))

    requested_rgb = _hex_to_rgb(requested_hex)
    if _contrast_ratio(requested_rgb, avg_rgb) >= _WCAG_AA_CONTRAST_RATIO:
        return requested_hex

    white_contrast = _contrast_ratio((255, 255, 255), avg_rgb)
    black_contrast = _contrast_ratio((0, 0, 0), avg_rgb)
    return "#ffffff" if white_contrast >= black_contrast else "#000000"


_BAND_PADDING = 14


def _draw_background_band(
    canvas: Image.Image, box: tuple[int, int, int, int], el: ElementSpec
) -> Image.Image:
    """A solid/semi-transparent backing rectangle behind a text block --
    round 5's fix for text-over-busy-background legibility (wayfinder issue
    #36): rather than hoping a single sampled pixel is representative of a
    whole box that might straddle the product's edge, this guarantees a
    known, controlled surface for the text to sit on, then lets
    _ensure_legible_color pick a color against that known surface."""
    x0, y0, x1, y1 = box
    x0, y0 = x0 - _BAND_PADDING, y0 - _BAND_PADDING
    x1, y1 = x1 + _BAND_PADDING, y1 + _BAND_PADDING
    color_hex = el.background_band_color_hex or "#ffffff"
    r, g, b = _hex_to_rgb(color_hex)
    band_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(band_layer).rounded_rectangle(
        [x0, y0, x1, y1], radius=10, fill=(r, g, b, el.background_band_opacity)
    )
    return Image.alpha_composite(canvas.convert("RGBA"), band_layer).convert("RGB")


def _draw_text_element(
    canvas: Image.Image, el: ElementSpec, canvas_w: int, canvas_h: int
) -> Image.Image:
    text = (el.text or "").upper() if el.uppercase else (el.text or "")
    if not text:
        return canvas
    cx, cy, cw, ch = _clamp_box(el.x, el.y, el.width, el.height)
    box_w = int(cw * canvas_w)
    box_h = int(ch * canvas_h)
    x = int(cx * canvas_w)
    y = int(cy * canvas_h)

    draw = ImageDraw.Draw(canvas)
    # Shrink (and re-wrap at each size) until the whole wrapped block fits
    # both box dimensions -- the bug the first live smoke test caught was
    # exactly this: font size was only ever checked against width.
    font_size = max(_MIN_FONT_SIZE, int(box_h * 0.6))
    lines: list[str] = [text]
    while font_size > _MIN_FONT_SIZE:
        font = _load_font(font_size, el.font_personality)
        lines = _wrap_lines(draw, text, font, box_w)
        block_w, block_h = _block_size(draw, lines, font)
        if block_w <= box_w and block_h <= box_h:
            break
        font_size -= 2
    font = _load_font(font_size, el.font_personality)
    lines = _wrap_lines(draw, text, font, box_w)
    _block_w, block_h = _block_size(draw, lines, font)

    if el.background_band:
        canvas = _draw_background_band(canvas, (x, y, x + _block_w, y + block_h), el)
        draw = ImageDraw.Draw(canvas)

    color = _ensure_legible_color(canvas, (x, y, x + box_w, y + block_h), el.text_color_hex)
    line_height = int(font.size * 1.2)
    shadow_color = (0, 0, 0, _SHADOW_OPACITY)
    ox, oy = _SHADOW_OFFSET

    def draw_shadow(shadow_draw: ImageDraw.ImageDraw) -> None:
        for i, line in enumerate(lines):
            shadow_draw.text((x + ox, y + i * line_height + oy), line, fill=shadow_color, font=font)

    if not el.background_band:
        # A shadow under a text block that already sits on its own solid
        # band would just shadow the band's edge, not add anything -- only
        # meaningful when text sits directly on the photographic image.
        canvas = _composite_blurred_shadow(canvas, draw_shadow)
    draw = ImageDraw.Draw(canvas)
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_height), line, fill=color, font=font)
    return canvas


def _draw_cta_element(
    canvas: Image.Image, el: ElementSpec, canvas_w: int, canvas_h: int
) -> Image.Image:
    """Regression (round 2 of the layout fix, wayfinder issue #36): this
    function shrank font size off box *height* only, same bug class as the
    original headline overflow -- confirmed live, a CTA label clipped past
    the canvas edge once the layout agent (not the old fixed-fraction
    default) started returning tighter, button-sized boxes. Now shrinks to
    fit width too, same discipline as _draw_text_element.

    Round 5 fix, found live: the fill color was a hardcoded #1a1a1a default,
    never checked against what's actually behind it -- confirmed live, a
    dark button on a dark reddish background review-flagged as "blends into
    the background, reducing clickability." Same auto-contrast discipline as
    text now applies to the button fill itself, and the label text's color
    is picked against the button's *actual final* fill, not the photo."""
    cx, cy, cw, ch = _clamp_box(el.x, el.y, el.width, el.height)
    x0, y0 = int(cx * canvas_w), int(cy * canvas_h)
    x1, y1 = int((cx + cw) * canvas_w), int((cy + ch) * canvas_h)
    box_w, box_h = x1 - x0, y1 - y0
    radius = int(box_h * 0.3)
    ox, oy = _SHADOW_OFFSET

    requested_fill = el.fill_color_hex or "#1a1a1a"
    fill = _ensure_legible_color(canvas, (x0, y0, x1, y1), requested_fill)
    fill_rgb = _hex_to_rgb(fill)
    text_color = (
        "#ffffff"
        if _contrast_ratio((255, 255, 255), fill_rgb) >= _contrast_ratio((0, 0, 0), fill_rgb)
        else "#000000"
    )

    def draw_shadow(shadow_draw: ImageDraw.ImageDraw) -> None:
        shadow_draw.rounded_rectangle(
            [x0 + ox, y0 + oy, x1 + ox, y1 + oy], radius=radius,
            fill=(0, 0, 0, _SHADOW_OPACITY),
        )

    canvas = _composite_blurred_shadow(canvas, draw_shadow)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)

    if not el.text:
        return canvas
    text = el.text.upper() if el.uppercase else el.text
    # Leave a small horizontal margin so text never touches the pill's curve.
    max_text_w = max(1, int(box_w * 0.85))
    font_size = max(_MIN_FONT_SIZE, int(box_h * 0.45))
    while font_size > _MIN_FONT_SIZE:
        font = _load_font(font_size, el.font_personality)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_text_w:
            break
        font_size -= 2
    font = _load_font(font_size, el.font_personality)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x0 + (box_w - tw) // 2
    ty = y0 + (box_h - th) // 2 - bbox[1]
    draw.text((tx, ty), text, fill=text_color, font=font)
    return canvas


def compose_ad(spec: AdSpec) -> bytes:
    """Returns PNG bytes of the fully assembled ad. Each element's drawing
    function returns the (possibly new, if it composited a shadow layer)
    canvas -- never mutate-and-ignore, since Image.alpha_composite always
    returns a new object rather than mutating in place."""
    canvas = Image.open(io.BytesIO(spec.background_and_product_image)).convert("RGB")
    if canvas.size != (spec.canvas_width, spec.canvas_height):
        canvas = canvas.resize((spec.canvas_width, spec.canvas_height))

    for el in sorted(spec.elements, key=lambda e: e.z_order):
        if el.element_type == "cta_graphic":
            canvas = _draw_cta_element(canvas, el, spec.canvas_width, spec.canvas_height)
        elif el.element_type in ("headline", "secondary_copy", "price_offer"):
            canvas = _draw_text_element(canvas, el, spec.canvas_width, spec.canvas_height)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
