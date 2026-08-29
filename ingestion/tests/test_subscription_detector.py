from ingestion.subscription_detector import (
    detect_subscription_app,
    determine_subscription_status,
    extract_subscription_price,
)


class TestDetectSubscriptionApp:
    def test_detects_recharge_via_window_config(self) -> None:
        html = "<script>window.RechargeStorefrontConfig = {customer: null};</script>"
        assert detect_subscription_app(html) == "recharge"

    def test_detects_recharge_via_generic_signature(self) -> None:
        html = '<div class="recharge-widget" data-recharge-add-to-cart></div>'
        assert detect_subscription_app(html) == "recharge"

    def test_detects_bold_subscriptions(self) -> None:
        html = '<div class="bold-subscription-widget"></div>'
        assert detect_subscription_app(html) == "bold_subscriptions"

    def test_no_signature_returns_none(self) -> None:
        html = "<html><body><p>Just a regular product page.</p></body></html>"
        assert detect_subscription_app(html) is None


class TestDetermineSubscriptionStatus:
    def test_no_signal_returns_unknown(self) -> None:
        html = "<html><body><p>Regular one-time product.</p></body></html>"
        assert determine_subscription_status(html, "Regular one-time product.") == "unknown"

    def test_app_signature_defaults_to_optional(self) -> None:
        html = '<div class="recharge-widget"></div>'
        text = "Buy now"
        assert determine_subscription_status(html, text) == "subscription_optional"

    def test_keyword_text_alone_defaults_to_optional(self) -> None:
        html = "<html><body>no app markup here</body></html>"
        text = "Subscribe & Save 20% or choose one-time purchase."
        assert determine_subscription_status(html, text) == "subscription_optional"

    def test_explicit_subscription_only_language_is_required(self) -> None:
        html = '<div class="recharge-widget"></div>'
        text = "This product is subscription only. Ships every 30 days automatically."
        assert determine_subscription_status(html, text) == "subscription_required"

    def test_subscription_only_language_with_one_time_indicator_stays_optional(self) -> None:
        # Real-world pages sometimes mix confusing copy; presence of an
        # explicit one-time indicator should win over ambiguous "only" text.
        html = '<div class="recharge-widget"></div>'
        text = "Subscription only pricing shown, but one-time purchase is also available."
        assert determine_subscription_status(html, text) == "subscription_optional"


class TestExtractSubscriptionPrice:
    def test_daily_rate_converted_to_monthly_estimate(self) -> None:
        text = "About $1.63/day on subscription. Cancel anytime."
        assert extract_subscription_price(text) == round(1.63 * 30, 2)

    def test_daily_rate_with_per_day_wording(self) -> None:
        text = "Just $2.00 per day, billed monthly."
        assert extract_subscription_price(text) == 60.0

    def test_monthly_rate_used_directly_not_converted(self) -> None:
        text = "Subscribe for $23.96/month and save."
        assert extract_subscription_price(text) == 23.96

    def test_prefers_monthly_over_daily_when_both_present(self) -> None:
        text = "That's just $1.63/day, or $49.99/month billed directly."
        assert extract_subscription_price(text) == 49.99

    def test_no_rate_pattern_returns_none(self) -> None:
        text = "Save up to 58% on subscription (cancel anytime)."
        assert extract_subscription_price(text) is None

    def test_empty_text_returns_none(self) -> None:
        assert extract_subscription_price("") is None
