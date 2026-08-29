"""Data models for one generated ad's element layout -- the "Canva-style
layers" the compositor assembles deterministically. Positions are 0-1
fractions of the canvas so the same spec works at any output resolution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ElementType = Literal[
    "background_and_product",  # background.py's masked-inpaint output (Round 6: Flux Fill)
    "headline", "secondary_copy", "cta_graphic", "price_offer",
]

# Round 5 (2026-08-28): a real, if qualitative, font choice -- nothing in the
# corpus's own extraction ever measured typeface identity, so this isn't a
# statistical directive the way color/layout are; it's the style-reference
# agent's own qualitative judgment, given the guide's other directives
# (style_reference.py) -- never grounded in reference-ad images (Round 7
# moved those to feature_fidelity.py's post-generation comparison instead).
# Each maps to one bundled font file (pipeline/generation/assets/fonts/) --
# never a bare font-family name, which silently fails to resolve on this
# platform and previously masked itself by falling back to PIL's tiny
# built-in bitmap font (confirmed live: every prior generated ad used that
# same fallback, not a real typeface at all -- see compositor.py).
FontPersonality = Literal["clean_modern", "bold_condensed", "elegant_serif", "playful_dynamic"]


class ElementSpec(BaseModel):
    element_type: ElementType
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0
    z_order: int = 0
    text: str | None = None
    text_color_hex: str = "#111111"
    fill_color_hex: str | None = None  # background fill for cta_graphic
    uppercase: bool = False
    font_personality: FontPersonality = "clean_modern"
    # A solid/semi-transparent backing band behind text -- a real design
    # lever (per docs/meta-ad-image-model-stack.md-style ad conventions),
    # and a robust fix for text-over-busy-background legibility: rather than
    # hoping a single sampled background color is representative, this
    # guarantees a known, controlled surface for the text to sit on.
    background_band: bool = False
    background_band_color_hex: str | None = None
    background_band_opacity: int = 235  # 0-255, only used when background_band=True


class AdSpec(BaseModel):
    """Everything the compositor needs: the masked-inpaint-produced
    background+product image (background.py) plus the deterministically-
    rendered copy/CTA layers on top of it."""

    canvas_width: int = 1080
    canvas_height: int = 1080
    background_and_product_image: bytes
    elements: list[ElementSpec]
