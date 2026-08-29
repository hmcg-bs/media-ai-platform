"""Tests for pipeline/feature_engineering/extractor.py — the main feature
extractor wiring text/CTA/positioning/color/product/account/platform/Step-2
features together."""

from __future__ import annotations

from pipeline.feature_engineering.extractor import extract_all_features


class _FakeEmbeddingClient:
    """Deterministic fake — no real Replicate calls in tests. Confirms the
    injected client is actually used, not silently falling back to a real
    (network-calling) EmbeddingClient()."""

    def __init__(self):
        self.calls: list[str] = []

    def embed(self, text: str, task: str = "retrieval_document") -> list[float]:
        self.calls.append(text)
        return [1.0, 2.0, 3.0]


class TestExtractAllFeatures:
    def test_returns_features_and_price_tier_separately(self) -> None:
        ad = {
            "title": "Best Supplement Ever",
            "body": "Try our amazing product today, limited time offer!",
            "cta_text": "Shop Now",
            "days_active": 30,
            "collation_count": 2,
            "publisher_platforms": ["facebook", "instagram"],
            "product_page": {"price": 44.99, "rating": 4.8, "product_category": "Supplements"},
        }
        features, price_tier = extract_all_features(ad, embedding_client=_FakeEmbeddingClient())

        assert price_tier == "premium"  # $44.99 is in the $35-60 band
        assert "price" not in features
        assert "product_category" not in features
        assert features["rating"] == 4.8
        assert features["days_active"] == 30
        assert features["collation_count"] == 2
        assert features["publisher_count"] == 2

    def test_campaign_taxonomy_features_wired_from_link_url(self) -> None:
        ad = {
            "link_url": "https://example.com/?utm_source=facebook&utm_medium=paid-social"
            "&utm_campaign={{campaign.id}}",
        }
        features, _price_tier = extract_all_features(ad, embedding_client=_FakeEmbeddingClient())
        assert features["has_utm_tracking"] is True
        assert features["utm_medium_category"] == "dedicated_paid_social"
        assert features["utm_dynamic_naming"] is True
        assert features["campaign_role_signal"] == "likely_test"

    def test_campaign_taxonomy_features_default_safely_without_link_url(self) -> None:
        features, _price_tier = extract_all_features({}, embedding_client=_FakeEmbeddingClient())
        assert features["has_utm_tracking"] is False
        assert features["campaign_role_signal"] == "unknown"

    def test_no_product_page_still_returns_unknown_tier(self) -> None:
        features, price_tier = extract_all_features(
            {"title": "X", "body": "Y"}, embedding_client=_FakeEmbeddingClient()
        )
        assert price_tier == "unknown"
        assert features["rating"] is None

    def test_creative_features_default_to_none_without_step2_data(self) -> None:
        """An ad Step 2 hasn't processed yet gets null creative_* fields
        rather than the old hardcoded placeholders — distinguishable from a
        real "Step 2 ran and found nothing" result."""
        features, _ = extract_all_features({"title": "X"}, embedding_client=_FakeEmbeddingClient())
        assert features["creative_hook_framework"] is None
        assert features["creative_copy_block_count"] is None
        assert features["dominant_color"] == "unknown"
        assert features["palette_vibrancy"] == 0.5

    def test_real_step2_creative_features_wired_through(self) -> None:
        """Regression: pipeline/feature_engineering/extractor.py:78-85 used
        to hardcode dominant_hex="#808080" etc. with a comment saying "would
        come from Step 2 creative analysis" — this confirms merged Step 2
        output (flatten_features() + color_profile fields) actually flows
        into the final feature row now."""
        ad = {"title": "X", "product_page": {"price": 9.99}}
        creative_features = {
            "hook_framework": "PAS",
            "copy_block_count": 3,
            "headline_zone": "top",
            "uppercase_ratio": 0.4,
            "dominant_hex_palette": ["#CFEDDB", "#D8F89E", "#59B5BA"],
            "background_hex": "#CFEDDB",
            "contrast_ratio_type": "High",
            "background_style": "Studio",
        }
        features, price_tier = extract_all_features(
            ad, creative_features=creative_features, embedding_client=_FakeEmbeddingClient()
        )

        assert price_tier == "budget"
        assert features["creative_hook_framework"] == "PAS"
        assert features["creative_copy_block_count"] == 3
        assert features["creative_headline_zone"] == "top"
        assert features["creative_uppercase_ratio"] == 0.4
        assert features["contrast_ratio_type"] == "High"
        assert features["background_style"] == "Studio"
        assert features["palette_vibrancy"] != 0.5  # real computed value, not the old placeholder

    def test_all_price_tiers_reachable_end_to_end(self) -> None:
        cases = [
            (9.99, "budget"),
            (20.0, "mid"),
            (50.0, "premium"),
            (100.0, "luxury_bundle"),
        ]
        for price, expected_tier in cases:
            _, price_tier = extract_all_features(
                {"product_page": {"price": price}}, embedding_client=_FakeEmbeddingClient()
            )
            assert price_tier == expected_tier

    def test_uses_injected_embedding_client_not_a_real_one(self) -> None:
        """Regression: extract_all_features must actually pass embedding_client
        through to extract_embedding_features, not silently construct its own
        real (network-calling) EmbeddingClient()."""
        fake = _FakeEmbeddingClient()
        ad = {"title": "Real Title", "body": "Real body copy", "product_page": {"usp": "Real USP"}}
        features, _ = extract_all_features(ad, embedding_client=fake)

        assert fake.calls == ["Real Title", "Real body copy", "Real USP"]
        assert features["title_embedding"] == [1.0, 2.0, 3.0]
        assert features["body_embedding"] == [1.0, 2.0, 3.0]
        assert features["usp_embedding"] == [1.0, 2.0, 3.0]

    def test_empty_text_fields_skip_embedding_call(self) -> None:
        fake = _FakeEmbeddingClient()
        features, _ = extract_all_features({}, embedding_client=fake)

        assert fake.calls == []
        assert features["title_embedding"] == []
        assert features["body_embedding"] == []
        assert features["usp_embedding"] == []
