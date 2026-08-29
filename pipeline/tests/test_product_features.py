"""Tests for pipeline/feature_engineering/product_features.py."""

from __future__ import annotations

from pipeline.feature_engineering.product_features import (
    calculate_price_tier,
    extract_product_features,
)


class TestCalculatePriceTier:
    """Fixed-dollar boundaries matching Phase 1's data-exploration report
    exactly (2,617-ad filtered set: p10 $11.99, median $44.99, p90 $70.00)
    — user decision: segment by these tiers rather than feed raw price into
    the model as a numeric feature."""

    def test_none_price_is_unknown(self) -> None:
        assert calculate_price_tier(None) == "unknown"

    def test_budget_tier(self) -> None:
        assert calculate_price_tier(9.99) == "budget"
        assert calculate_price_tier(14.99) == "budget"

    def test_mid_tier(self) -> None:
        assert calculate_price_tier(15.0) == "mid"
        assert calculate_price_tier(29.99) == "mid"

    def test_premium_tier(self) -> None:
        assert calculate_price_tier(35.0) == "premium"
        assert calculate_price_tier(44.99) == "premium"
        assert calculate_price_tier(59.99) == "premium"

    def test_luxury_bundle_tier(self) -> None:
        assert calculate_price_tier(60.0) == "luxury_bundle"
        assert calculate_price_tier(599.0) == "luxury_bundle"

    def test_boundary_values_are_inclusive_on_lower_bound(self) -> None:
        # < boundary goes to the lower tier; == boundary goes to the next.
        assert calculate_price_tier(14.999) == "budget"
        assert calculate_price_tier(15.0) == "mid"


class TestExtractProductFeatures:
    def test_price_and_product_category_excluded_from_feature_dict(self) -> None:
        """Regression: price and product_category used to be raw
        feature-dict keys — per the user's segmentation decision (price)
        and the plan's own "category for segmentation only" rule, neither
        belongs in the model's feature vector. Both are still available to
        the caller separately (price via calculate_price_tier,
        product_category directly from product_page)."""
        features = extract_product_features({
            "price": 44.99,
            "product_category": "Supplements",
            "rating": 4.8,
        })
        assert "price" not in features
        assert "price_normalized" not in features
        assert "product_category" not in features

    def test_none_product_page_returns_safe_defaults(self) -> None:
        features = extract_product_features(None)
        assert features == {
            "rating": None,
            "shows_all_variants": False,
            "variants_featured_count": 0,
            "cultural_branding_count": 0,
        }

    def test_real_fields_extracted_correctly(self) -> None:
        features = extract_product_features({
            "rating": 4.8,
            "shows_all_variants": True,
            "variants_featured": ["Flavor: Vanilla", "Size: 500g"],
            "cultural_branding": ["American Made"],
        })
        assert features["rating"] == 4.8
        assert features["shows_all_variants"] is True
        assert features["variants_featured_count"] == 2
        assert features["cultural_branding_count"] == 1
