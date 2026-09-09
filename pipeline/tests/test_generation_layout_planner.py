"""Tests for pipeline/generation/layout_planner.py -- Generation v2's
deterministic, pre-generation replacement for the old vision-based
plan_layout (layout.py). Pure geometry, no fakes/API calls needed."""

from __future__ import annotations

from pipeline.generation.guide import DirectionalSignal, GenerationGuide
from pipeline.generation.layout import BoundingBox
from pipeline.generation.layout_planner import _FALLBACK_LAYOUT, plan_layout_from_guide


def _guide_with(*signals: DirectionalSignal) -> GenerationGuide:
    return GenerationGuide(
        visual_directives=list(signals), copy_style_directives=[], positioning_context=[],
        non_directional_signals=[], excluded_notes=[],
    )


class TestPlanLayoutFromGuideOrientation:
    def test_product_on_left_puts_zones_on_the_right(self):
        product_bbox = BoundingBox(x=0.05, y=0.1, width=0.4, height=0.8)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=False)

        assert plan.headline_zone.x >= product_bbox.x + product_bbox.width
        assert plan.secondary_copy_zone.x >= product_bbox.x + product_bbox.width
        assert plan.cta_zone.x >= product_bbox.x + product_bbox.width

    def test_product_on_right_puts_zones_on_the_left(self):
        product_bbox = BoundingBox(x=0.55, y=0.1, width=0.4, height=0.8)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=False)

        assert plan.headline_zone.x + plan.headline_zone.width <= product_bbox.x
        assert plan.cta_zone.x + plan.cta_zone.width <= product_bbox.x

    def test_wide_centered_product_puts_zones_above_or_below(self):
        product_bbox = BoundingBox(x=0.05, y=0.55, width=0.9, height=0.3)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=False)

        headline_above = plan.headline_zone.y + plan.headline_zone.height <= product_bbox.y
        headline_below = plan.headline_zone.y >= product_bbox.y + product_bbox.height
        assert headline_above or headline_below

    def test_degenerate_empty_region_falls_back_to_fixed_layout(self):
        """A product that dominates nearly the whole frame leaves no usable
        empty region on any side -- must degrade to the documented fallback,
        not emit zero/negative-sized zones."""
        product_bbox = BoundingBox(x=0.02, y=0.02, width=0.96, height=0.96)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=False)

        assert plan.headline_zone == _FALLBACK_LAYOUT.headline_zone
        assert plan.secondary_copy_zone == _FALLBACK_LAYOUT.secondary_copy_zone
        assert plan.cta_zone == _FALLBACK_LAYOUT.cta_zone
        assert plan.product_bbox == product_bbox  # still reports the real bbox


class TestHeadlineZoneDirective:
    def test_no_directive_anchors_headline_first_by_default(self):
        product_bbox = BoundingBox(x=0.05, y=0.1, width=0.4, height=0.8)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=False)

        assert plan.headline_zone.y < plan.cta_zone.y

    def test_prefer_bottom_directive_anchors_headline_last(self):
        signal = DirectionalSignal(
            dimension="headline_zone", value="bottom", direction="higher_is_better",
            magnitude=0.1, source="cox:days_active",
        )
        product_bbox = BoundingBox(x=0.05, y=0.1, width=0.4, height=0.8)
        plan = plan_layout_from_guide(product_bbox, _guide_with(signal), has_price_offer=False)

        assert plan.headline_zone.y > plan.cta_zone.y

    def test_avoid_top_directive_anchors_headline_last(self):
        signal = DirectionalSignal(
            dimension="headline_zone", value="top", direction="lower_is_better",
            magnitude=0.1, source="cox:days_active",
        )
        product_bbox = BoundingBox(x=0.05, y=0.1, width=0.4, height=0.8)
        plan = plan_layout_from_guide(product_bbox, _guide_with(signal), has_price_offer=False)

        assert plan.headline_zone.y > plan.cta_zone.y


class TestPriceOfferZone:
    def test_omitted_when_has_price_offer_is_false(self):
        product_bbox = BoundingBox(x=0.05, y=0.1, width=0.4, height=0.5)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=False)

        assert plan.price_offer_zone is None

    def test_populated_when_has_price_offer_and_corner_area_remains(self):
        """Product doesn't reach the bottom of the canvas, leaving a
        genuinely separate corner on the product's own side."""
        product_bbox = BoundingBox(x=0.05, y=0.1, width=0.4, height=0.4)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=True)

        assert plan.price_offer_zone is not None
        assert plan.price_offer_zone.y >= product_bbox.y + product_bbox.height

    def test_omitted_when_no_corner_area_remains_even_if_requested(self):
        """Product spans the full height of its side -- no separate corner
        exists to reserve, regardless of has_price_offer."""
        product_bbox = BoundingBox(x=0.05, y=0.0, width=0.4, height=1.0)
        plan = plan_layout_from_guide(product_bbox, _guide_with(), has_price_offer=True)

        assert plan.price_offer_zone is None
