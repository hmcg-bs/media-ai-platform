"""Campaign-taxonomy signals parsed out of an ad's `link_url` -- pure
URL-parsing over data already in the corpus (`CompetitorAd.link_url`), no
new scraping needed. Confirmed live: UTM parameters are present on 203/2,736
ads (7.4%) in this corpus, real but sparse.

X-axis features: these describe how the campaign is built/run (taxonomy,
naming sophistication, likely test-vs-scale role) -- not how well the ad
performed. See the plan's own X vs Y axis framing: UTM taxonomy is an input
that might help *explain* differences in the real Y-axis outcomes
(days_active, collation_count, variants_featured_count), not an outcome
itself.

`campaign_role_signal` is an explicit, best-effort heuristic, not ground
truth -- same honesty standard already applied to `subscription_status`
(ingestion/subscription_detector.py) and `price_context`
(ingestion/llm_fallback.py) classification elsewhere in this codebase.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

# "Dedicated Attribution" (paid-social specific) vs "Legacy Attribution"
# (generic/easily-misattributed channel) -- matches the maturity
# distinction from the user's own reference table (utm_medium=paid-social
# vs utm_medium=cpc/social).
_DEDICATED_MEDIUM_RE = re.compile(r"paid[-_ ]?social|paidsocial", re.IGNORECASE)
_LEGACY_MEDIUM_RE = re.compile(r"^cpc$|^social$|^ppc$", re.IGNORECASE)

# Unresolved Facebook dynamic-variable templating, e.g. {{campaign.id}},
# {{ad.name}} -- confirmed live present in this corpus's own link_urls
# (also separately flagged as a URL-malformation bug in the price-
# extraction work; here it's read as signal, not noise).
_DYNAMIC_TEMPLATE_RE = re.compile(r"\{\{[a-z_.]+\}\}", re.IGNORECASE)

# Distinct creative *dimensions* an advertiser is explicitly labeling in
# utm_content -- a proxy for how many testing axes are being isolated
# (confirmed live: e.g. "...staticcarousel" contains "static").
_GRANULARITY_KEYWORDS = (
    "hook", "cta", "ugc", "static", "video", "testimonial",
    "flavor", "size", "angle", "format",
)

# Explicit test/version markers -- distinct from the creative-dimension
# keywords above (a campaign can name its creative dimensions without
# being an explicit test, and vice versa).
_TEST_SIGNAL_KEYWORDS = ("test", "v1", "v2", "v3", "variant", "alt", "exp", "experiment")

UTM_MEDIUM_CATEGORIES = ("dedicated_paid_social", "legacy_generic", "unknown")
CAMPAIGN_ROLE_SIGNALS = ("likely_test", "likely_scale", "unknown")


def extract_utm_params(link_url: str | None) -> dict[str, str]:
    """Raw utm_* query params from link_url, lowercased keys. Empty dict
    when link_url is empty, unparseable, or has no utm_* params at all --
    the honest "no UTM data" case, never fabricated."""
    if not link_url:
        return {}
    try:
        parsed = urlparse(link_url)
    except ValueError:
        return {}
    params = parse_qs(parsed.query)
    return {k.lower(): v[0] for k, v in params.items() if k.lower().startswith("utm_") and v}


def categorize_utm_medium(utm_medium: str | None) -> str:
    if not utm_medium:
        return "unknown"
    if _DEDICATED_MEDIUM_RE.search(utm_medium):
        return "dedicated_paid_social"
    if _LEGACY_MEDIUM_RE.match(utm_medium.strip()):
        return "legacy_generic"
    return "unknown"


def has_dynamic_naming(utm_params: dict[str, str]) -> bool:
    combined = " ".join(utm_params.get(k, "") for k in ("utm_campaign", "utm_content", "utm_term"))
    return bool(_DYNAMIC_TEMPLATE_RE.search(combined))


def content_granularity_score(utm_content: str | None) -> int:
    """Count of distinct creative-dimension keywords present in
    utm_content -- how many testing axes this advertiser is explicitly
    labeling (hook variant, CTA variant, format, etc.)."""
    if not utm_content:
        return 0
    lowered = utm_content.lower()
    return sum(1 for kw in _GRANULARITY_KEYWORDS if kw in lowered)


def campaign_role_signal(
    utm_params: dict[str, str], granularity_score: int, dynamic_naming: bool
) -> str:
    """Best-effort inference of whether this looks like a deliberate,
    disposable creative *test* (many variants, templated/dynamic naming,
    granular hook/CTA labeling) or a confident, scaled *winner* (a clean,
    singular, hand-named campaign) -- not ground truth."""
    if not utm_params:
        return "unknown"
    campaign = utm_params.get("utm_campaign", "").lower()
    content = utm_params.get("utm_content", "").lower()
    test_signal = any(kw in campaign or kw in content for kw in _TEST_SIGNAL_KEYWORDS)
    if dynamic_naming or granularity_score >= 2 or test_signal:
        return "likely_test"
    if utm_params.get("utm_campaign"):
        return "likely_scale"
    return "unknown"


def extract_campaign_features(link_url: str | None) -> dict[str, Any]:
    """The full UTM/campaign-taxonomy feature set for one ad's link_url."""
    utm_params = extract_utm_params(link_url)
    dynamic_naming = has_dynamic_naming(utm_params)
    granularity = content_granularity_score(utm_params.get("utm_content"))
    return {
        "has_utm_tracking": bool(utm_params),
        "utm_medium_category": categorize_utm_medium(utm_params.get("utm_medium")),
        "utm_dynamic_naming": dynamic_naming,
        "utm_content_granularity_score": granularity,
        "campaign_role_signal": campaign_role_signal(utm_params, granularity, dynamic_naming),
    }
