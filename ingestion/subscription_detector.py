"""Subscription-commerce detection: is this product one-time-purchase only,
subscription-optional, or subscription-required — and if a recurring price
is stated directly on the page (not just implied), what is it.

Deterministic-first, same philosophy as ingestion/builder_fingerprint.py:
known subscription-app signatures (script/class markers) plus a light
keyword/regex layer for price patterns that don't need an LLM call. Confirmed
live (subscription-pricing investigation, 2026-08-15): Recharge alone
appeared on 6/13 sampled real pages (46%) — a dominant, worth-detecting app,
found via `window.RechargeStorefrontConfig` and script/class signatures. Its
config object is real but non-standard JS-object-literal syntax focused on
cart-drawer/cross-sell UI, not a clean source for the discount price itself
— that price reliably renders as plain visible text instead ("Save up to
58% on subscription", "$1.63/day on subscription"), which is why price
extraction here is regex-over-text, not JSON parsing of the app config.

Detection is inherently incomplete: absence of a known signature does not
prove a page has no subscription option (a custom/native implementation
without any of these markers would be missed) — see SubscriptionStatus's
"unknown" default in ingestion/product_page.py.
"""

from __future__ import annotations

import re

SUBSCRIPTION_APP_SIGNATURES: dict[str, list[str]] = {
    "recharge": [r"recharge", r"RechargeStorefrontConfig"],
    "bold_subscriptions": [r"bold[-_]?subscription", r"BOLD\."],
    "skio": [r"skio", r"Skio\."],
    "awtomic": [r"awtomic", r"Awtomic\."],
    "loop_subscriptions": [r"loop[-_]?subscription"],
    "appstle": [r"appstle"],
}

_APP_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns]
    for name, patterns in SUBSCRIPTION_APP_SIGNATURES.items()
}

_ONE_TIME_INDICATOR_RE = re.compile(
    r"one[- ]time purchase|single purchase|buy once|no subscription", re.IGNORECASE
)
_SUBSCRIPTION_REQUIRED_RE = re.compile(
    r"subscription only|members?[- ]only|auto-renew(?:ing)? only|subscription required",
    re.IGNORECASE,
)
_SUBSCRIPTION_TEXT_RE = re.compile(
    r"subscribe\s*&?\s*save|subscription|deliver every|manage subscription|cancel anytime",
    re.IGNORECASE,
)

# "$1.63/day", "$1.63 per day", "$1.63/mo", "$29.95/month" — captures the
# number and the unit so a daily rate can be converted to a monthly estimate.
_RATE_PRICE_RE = re.compile(
    r"\$\s?(\d[\d,]*\.?\d{0,2})\s*(?:/|per\s+)\s*(day|month|mo\b)", re.IGNORECASE
)
_DAYS_PER_MONTH = 30


def detect_subscription_app(html: str) -> str | None:
    """Returns the subscription app name on the first signature match, else
    None. Checked in SUBSCRIPTION_APP_SIGNATURES' dict order."""
    for name, patterns in _APP_PATTERNS.items():
        if any(p.search(html) for p in patterns):
            return name
    return None


def determine_subscription_status(html: str, visible_text: str) -> str:
    """"unknown" is the honest default — a real coverage ceiling, not a
    negative claim, since detection is signature/keyword-based."""
    app = detect_subscription_app(html)
    has_subscription_signal = app is not None or bool(_SUBSCRIPTION_TEXT_RE.search(visible_text))

    if not has_subscription_signal:
        return "unknown"

    if _SUBSCRIPTION_REQUIRED_RE.search(visible_text) and not _ONE_TIME_INDICATOR_RE.search(
        visible_text
    ):
        return "subscription_required"

    return "subscription_optional"


def extract_subscription_price(visible_text: str) -> float | None:
    """Regex-only extraction of a directly-stated recurring price. A daily
    rate ("$1.63/day") is converted to a 30-day monthly estimate — an
    approximation, not the page's own stated monthly figure, since most
    pages state the rate this way specifically to obscure the real
    recurring total. Prefers a month/mo-denominated match over a day-rate
    if both are present (less approximation involved)."""
    matches = _RATE_PRICE_RE.findall(visible_text)
    if not matches:
        return None

    monthly_matches = [
        float(amount.replace(",", "")) for amount, unit in matches if unit.lower() != "day"
    ]
    if monthly_matches:
        return monthly_matches[0]

    daily_matches = [
        float(amount.replace(",", "")) for amount, unit in matches if unit.lower() == "day"
    ]
    if daily_matches:
        return round(daily_matches[0] * _DAYS_PER_MONTH, 2)

    return None
