"""Tests for ingestion/utm_features.py -- offline, canned URLs, no network
calls. Several cases use real link_urls confirmed present in the corpus
this session (chewy.com, naturemade.com, snapsupplements.com,
pricespider.com, pureforyou.com) rather than invented ones."""

from __future__ import annotations

from ingestion.utm_features import (
    campaign_role_signal,
    categorize_utm_medium,
    content_granularity_score,
    extract_campaign_features,
    extract_utm_params,
    has_dynamic_naming,
)


class TestExtractUtmParams:
    def test_no_link_url_returns_empty(self):
        assert extract_utm_params(None) == {}
        assert extract_utm_params("") == {}

    def test_link_url_with_no_query_string_returns_empty(self):
        assert extract_utm_params("https://example.com/product") == {}

    def test_link_url_with_non_utm_params_only_returns_empty(self):
        assert extract_utm_params("https://example.com/?variant=123&ref=abc") == {}

    def test_extracts_real_utm_params(self):
        url = "https://www.naturemade.com/products/super-b?utm_content=Facebook_UA&utm_source=facebook&variant=17610374316103"
        params = extract_utm_params(url)
        assert params == {"utm_content": "Facebook_UA", "utm_source": "facebook"}

    def test_unparseable_url_returns_empty_not_raises(self):
        assert extract_utm_params("not a url at all :::") == {}


class TestCategorizeUtmMedium:
    def test_paid_social_variants_are_dedicated(self):
        assert categorize_utm_medium("paid-social") == "dedicated_paid_social"
        assert categorize_utm_medium("paid_social") == "dedicated_paid_social"
        assert categorize_utm_medium("paidsocial") == "dedicated_paid_social"

    def test_cpc_and_bare_social_are_legacy(self):
        assert categorize_utm_medium("cpc") == "legacy_generic"
        assert categorize_utm_medium("social") == "legacy_generic"

    def test_none_or_unrecognized_is_unknown(self):
        assert categorize_utm_medium(None) == "unknown"
        assert categorize_utm_medium("email") == "unknown"
        assert categorize_utm_medium("social ads") == "unknown"  # not an exact "social" match


class TestHasDynamicNaming:
    def test_detects_unresolved_facebook_template(self):
        # Real pattern confirmed live on a chewy.com link_url this session.
        assert has_dynamic_naming({"utm_campaign": "{{campaign.id}}"}) is True
        assert has_dynamic_naming({"utm_content": "{{ad.id}}"}) is True
        assert has_dynamic_naming({"utm_term": "{{placement}}"}) is True

    def test_static_naming_not_flagged(self):
        assert has_dynamic_naming({"utm_campaign": "BF_2026_Shoes"}) is False

    def test_empty_params_not_flagged(self):
        assert has_dynamic_naming({}) is False


class TestContentGranularityScore:
    def test_counts_distinct_creative_dimension_keywords(self):
        assert content_granularity_score("UGC_V1_Hook3_CTA2") >= 2  # ugc, hook, cta all present

    def test_real_corpus_example_matches_one_keyword(self):
        # Confirmed live: pricespider.com's utm_content contains "static"
        # (from "...staticcarousel") and nothing else from the keyword list.
        content = (
            "FFDogQuadrant_1vetreco_personpetpackageproduct_original_brand_"
            "brand_dog_shopnow_meta_staticcarousel"
        )
        assert content_granularity_score(content) == 1

    def test_no_keywords_scores_zero(self):
        assert content_granularity_score("Facebook_UA") == 0

    def test_none_scores_zero(self):
        assert content_granularity_score(None) == 0


class TestCampaignRoleSignal:
    def test_no_utm_params_is_unknown(self):
        assert campaign_role_signal({}, granularity_score=0, dynamic_naming=False) == "unknown"

    def test_dynamic_naming_signals_likely_test(self):
        params = {"utm_campaign": "{{campaign.name}}"}
        result = campaign_role_signal(params, granularity_score=0, dynamic_naming=True)
        assert result == "likely_test"

    def test_high_granularity_signals_likely_test(self):
        params = {"utm_campaign": "Q3_Push", "utm_content": "UGC_Hook3_CTA2"}
        result = campaign_role_signal(params, granularity_score=3, dynamic_naming=False)
        assert result == "likely_test"

    def test_explicit_test_keyword_in_campaign_name_signals_likely_test(self):
        params = {"utm_campaign": "creatine_test_v2"}
        result = campaign_role_signal(params, granularity_score=0, dynamic_naming=False)
        assert result == "likely_test"

    def test_clean_singular_campaign_name_signals_likely_scale(self):
        # Real corpus example: pricespider.com's utm_campaign="fortiflora2026"
        params = {"utm_campaign": "fortiflora2026"}
        result = campaign_role_signal(params, granularity_score=1, dynamic_naming=False)
        assert result == "likely_scale"

    def test_utm_params_present_but_no_campaign_name_is_unknown(self):
        params = {"utm_content": "Facebook_UA", "utm_source": "facebook"}
        assert campaign_role_signal(params, granularity_score=0, dynamic_naming=False) == "unknown"


class TestExtractCampaignFeatures:
    def test_no_link_url_returns_honest_no_data_defaults(self):
        features = extract_campaign_features(None)
        assert features["has_utm_tracking"] is False
        assert features["utm_medium_category"] == "unknown"
        assert features["utm_dynamic_naming"] is False
        assert features["utm_content_granularity_score"] == 0
        assert features["campaign_role_signal"] == "unknown"

    def test_real_chewy_url_with_dynamic_templating(self):
        # Real link_url confirmed present in the corpus this session.
        url = (
            "https://chewy.com/brands/purina-pro-plan-dog-food-7437"
            "?utm_source=facebook&utm_medium=social+ads&utm_campaign={{campaign.id}}"
            "&utm_content={{ad.id}}&utm_term={{placement}}"
        )
        features = extract_campaign_features(url)
        assert features["has_utm_tracking"] is True
        assert features["utm_dynamic_naming"] is True
        assert features["campaign_role_signal"] == "likely_test"

    def test_real_pricespider_url_dedicated_paid_social_and_scale(self):
        url = (
            "https://shop.pricespider.com/?campaignid=EY0BN5qRAN_b5095c11"
            "&productid=p3jdwgeioag79r"
            "&utm_content=FFDogQuadrant_1vetreco_personpetpackageproduct_original_brand_brand_dog_shopnow_meta_staticcarousel"
            "&utm_source=Meta&utm_medium=paidsocial&utm_campaign=fortiflora2026"
        )
        features = extract_campaign_features(url)
        assert features["utm_medium_category"] == "dedicated_paid_social"
        assert features["utm_dynamic_naming"] is False
        assert features["campaign_role_signal"] == "likely_scale"

    def test_all_keys_present(self):
        features = extract_campaign_features("https://example.com/?utm_source=facebook")
        assert set(features) == {
            "has_utm_tracking",
            "utm_medium_category",
            "utm_dynamic_naming",
            "utm_content_granularity_score",
            "campaign_role_signal",
        }
