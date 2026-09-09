"""Deterministic, pre-generation layout planner (Generation v2, 2026-08-30).

Generation v1's layout.py asked a Gemini vision call to look at an
*already-generated* background+product image and find empty space for text.
But the product's bounding box is knowable immediately after
BackgroundRemoverClient.remove_background() returns -- Flux Fill only ever
repaints the masked-out (white) region (masking.py), so the product never
moves after that point. This module computes the layout from that known
bbox plus the guide's own statistical layout directives (`headline_zone`),
before generation ever runs, with no API call at all. The result is fed into
background.py as explicit "keep clear" guardrails and stays fixed across the
whole regeneration loop -- removing a real source of instability the old
per-retry vision search had (each retry's fresh search could produce wildly
different zones for no reason related to the actual image).

layout.py's plan_layout is not deleted -- it's demoted to validate_layout,
a cheaper "does this fixed plan still hold" check, not a fresh search.
"""

from __future__ import annotations

from pipeline.generation.guide import GenerationGuide
from pipeline.generation.layout import BoundingBox, LayoutPlan

# Small gap kept between the product and the empty region, and along a
# zone's own border with its neighbor -- prevents the reserved text zones
# from touching pixels the mask itself didn't grow into.
_MARGIN = 0.03
_INTERNAL_GAP = 0.01
# Below this fraction of total canvas area, the computed empty region is
# considered unusable -- degrade to a fixed default rather than emit
# zero/negative-sized boxes. Should be rare: mask feathering already keeps a
# real margin around the product.
_MIN_REGION_AREA_FRACTION = 0.15
# A product spanning more of the canvas width than this is treated as
# "wide" -- the empty region becomes a horizontal band above/below it rather
# than a vertical strip beside it (there usually isn't one).
_WIDE_PRODUCT_WIDTH_THRESHOLD = 0.7
# Aggregate copy_canvas_coverage from the guide doesn't break down per
# element -- this is a documented, tunable default split of the empty
# region among the three copy elements every ad needs.
_ZONE_HEIGHT_FRACTIONS = {"headline": 0.4, "secondary_copy": 0.4, "cta": 0.2}
# A price/offer corner only counts as "genuinely separate" if it's at least
# this tall a band below (or above) the product, on the product's own side.
_MIN_PRICE_CORNER_HEIGHT = 0.15

# Last-resort degrade when the computed empty region is too small to be
# usable -- a fixed bottom-band split, not a crash. Documented, not a novel
# guess: this mirrors the classic layout every hand-composed ad in this
# category falls back to when the product itself dominates the frame.
_FALLBACK_LAYOUT = LayoutPlan(
    product_bbox=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
    headline_zone=BoundingBox(x=0.05, y=0.03, width=0.9, height=0.15),
    secondary_copy_zone=BoundingBox(x=0.05, y=0.70, width=0.9, height=0.15),
    cta_zone=BoundingBox(x=0.3, y=0.87, width=0.4, height=0.08),
    price_offer_zone=None,
)


def _empty_region(product_bbox: BoundingBox) -> BoundingBox:
    """The canvas region opposite the product: a horizontal band above/below
    it when the product spans nearly the full width, otherwise a vertical
    strip on whichever side the product ISN'T on. Mirrors the layouts
    actually seen in this session's own live-generated ads (product on one
    side, copy on the other) rather than a novel guess."""
    if product_bbox.width > _WIDE_PRODUCT_WIDTH_THRESHOLD:
        top_space = product_bbox.y
        bottom_space = 1.0 - (product_bbox.y + product_bbox.height)
        if bottom_space >= top_space:
            y = min(1.0, product_bbox.y + product_bbox.height + _MARGIN)
            return BoundingBox(x=0.0, y=y, width=1.0, height=max(0.0, 1.0 - y))
        height = max(0.0, product_bbox.y - _MARGIN)
        return BoundingBox(x=0.0, y=0.0, width=1.0, height=height)

    product_center_x = product_bbox.x + product_bbox.width / 2
    if product_center_x < 0.5:
        x = min(1.0, product_bbox.x + product_bbox.width + _MARGIN)
        return BoundingBox(x=x, y=0.0, width=max(0.0, 1.0 - x), height=1.0)
    width = max(0.0, product_bbox.x - _MARGIN)
    return BoundingBox(x=0.0, y=0.0, width=width, height=1.0)


def _headline_first(guide: GenerationGuide) -> bool:
    """The guide's own headline_zone directive decides whether the headline
    anchors at the top or bottom of the empty region. Absent a directive
    (or one that doesn't map cleanly to a top/bottom binary, e.g. "middle"),
    default to headline-first -- the conventional reading order."""
    directive = next(
        (s for s in guide.visual_directives if s.dimension == "headline_zone" and s.value),
        None,
    )
    if directive is None:
        return True
    value = directive.value.lower()
    if directive.direction == "higher_is_better":
        return value != "bottom"  # prefer bottom -> headline goes last
    return value != "top"  # avoid top -> headline goes last; avoid bottom/other -> stays first


def _stack_zones(
    region: BoundingBox, headline_first: bool
) -> tuple[BoundingBox, BoundingBox, BoundingBox]:
    order = (
        ["headline", "secondary_copy", "cta"]
        if headline_first
        else ["cta", "secondary_copy", "headline"]
    )
    x = region.x + _INTERNAL_GAP
    width = max(0.0, region.width - 2 * _INTERNAL_GAP)
    y = region.y + _INTERNAL_GAP

    zones: dict[str, BoundingBox] = {}
    for name in order:
        raw_height = _ZONE_HEIGHT_FRACTIONS[name] * region.height
        height = max(0.0, raw_height - _INTERNAL_GAP)
        zones[name] = BoundingBox(x=x, y=y, width=width, height=height)
        y += raw_height
    return zones["headline"], zones["secondary_copy"], zones["cta"]


def _price_offer_corner(product_bbox: BoundingBox, region: BoundingBox) -> BoundingBox | None:
    """Only populated when a genuinely separate empty area remains -- the
    corner below (or above) the product, on the product's OWN side of the
    canvas, distinct from the main copy region. A top/bottom-band region
    already spans the full width, so there's no such distinct corner in that
    orientation."""
    if region.width >= 1.0:  # top/bottom band orientation -- no distinct corner
        return None
    corner_y = min(1.0, product_bbox.y + product_bbox.height + _MARGIN)
    corner_height = max(0.0, 1.0 - corner_y)
    if corner_height < _MIN_PRICE_CORNER_HEIGHT:
        return None
    return BoundingBox(
        x=product_bbox.x, y=corner_y, width=product_bbox.width, height=corner_height
    )


def plan_layout_from_guide(
    product_bbox: BoundingBox,
    guide: GenerationGuide,
    *,
    has_price_offer: bool,
) -> LayoutPlan:
    """Pure geometry -- no API call. `product_bbox` comes from
    masking.compute_product_bbox on the product's own alpha cutout, computed
    before generation ever runs."""
    region = _empty_region(product_bbox)
    if region.width * region.height < _MIN_REGION_AREA_FRACTION:
        return _FALLBACK_LAYOUT.model_copy(update={"product_bbox": product_bbox})

    headline_zone, secondary_copy_zone, cta_zone = _stack_zones(
        region, _headline_first(guide)
    )
    price_offer_zone = _price_offer_corner(product_bbox, region) if has_price_offer else None

    return LayoutPlan(
        product_bbox=product_bbox,
        headline_zone=headline_zone,
        secondary_copy_zone=secondary_copy_zone,
        cta_zone=cta_zone,
        price_offer_zone=price_offer_zone,
    )
