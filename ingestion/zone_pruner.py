"""Module C: zone-based HTML pruning, preparing input for the Tier 5 LLM
fallback (ingestion/llm_fallback.py). Runs only when Tiers 1-4.5 fail.

Locates the structural zones an advertorial page's useful content actually
lives in (hero/offer, social proof, specs) rather than handing the whole
page to the LLM, then converts the pruned selection to Markdown under a
token budget — bounds both LLM cost and hallucination surface area.
"""

from __future__ import annotations

import re

import html2text
from bs4 import BeautifulSoup
from bs4.element import Tag

_CURRENCY_RE = re.compile(r"[$€£]\s?\d")
_SOCIAL_PROOF_RE = re.compile(r"reviews?|ratings?|\d+\s*stars", re.IGNORECASE)

# Rough chars-per-token approximation (no tokenizer dependency added — this
# is bounding LLM *cost*, not requiring exact token precision).
_CHARS_PER_TOKEN = 4


_BOILERPLATE_ANCESTORS = ("nav", "header", "footer", "aside")
_HERO_TAGS = ("h1", "h2", "h3", "h4", "div", "span", "p")

_DRAWER_ANCESTOR_RE = re.compile(r"drawer", re.IGNORECASE)
_BANNER_ANCESTOR_RE = re.compile(
    r"announcement|topbar|top-bar|above-header|promo-bar|header-group", re.IGNORECASE
)
# Mini-cart/subtotal widgets (e.g. a "$0.00" subtotal on an empty cart) match
# the currency regex just as readily as a real price — confirmed live
# (extraction-gap Round 1): infinisnutrition.com's hero matched
# `<div class="ajaxcart__subtotal">` instead of the actual product price.
_CART_WIDGET_ANCESTOR_RE = re.compile(r"subtotal|ajaxcart|minicart|cart__item", re.IGNORECASE)
_SHIPPING_CONTEXT_RE = re.compile(
    r"free\s+shipping|shipping\s+on|orders?\s+over|spend\s+\$", re.IGNORECASE
)
_PURCHASE_CONTEXT_RE = re.compile(
    r"buy|order|bottle|add\s+to\s+cart|subscribe|one[- ]time|per\s+bottle"
    r"|/\s*mo\b|save|\boff\b|checkout",
    re.IGNORECASE,
)
# A visually-hidden product-form (progressive-enhancement markup Shopify
# themes commonly leave in the DOM, e.g. a variant-specific form that only
# becomes visible after JS interaction) is just as likely to match the
# currency-scan/cart-form checks as the real, visible form — confirmed live
# (extraction-gap Round 2): 5 cumulative sampled failures across two rounds
# (getmorningwould.shop, ancestralsupplements.com x2, tryuvola.com, mortaine.co).
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
# Marketplace sites (Amazon, etc.) have their own cart/checkout forms whose
# action URL also contains "/cart" (e.g. "/gp/cart/desktop/go-to-checkout.html"),
# matching form[action*="/cart"] just as readily as a Shopify add-to-cart form
# — but the surrounding content is a checkout/subscription-delivery widget, not
# the product's own name/brand/description. Confirmed live (multi-field
# extraction-gap investigation): amazon.com listings hit exactly this.
_CHECKOUT_FLOW_TEXT_RE = re.compile(
    r"proceed to checkout|view cart\b|choose how often|delivery frequency"
    r"|potential.{0,20}savings",
    re.IGNORECASE,
)


def _has_ancestor_matching(el: Tag, pattern: re.Pattern[str]) -> bool:
    node: Tag | None = el
    depth = 0
    while isinstance(node, Tag) and depth < 6:
        attrs = " ".join([node.get("id") or "", " ".join(node.get("class") or [])])
        if pattern.search(attrs):
            return True
        node = node.parent if isinstance(node.parent, Tag) else None
        depth += 1
    return False


def _is_hidden(el: Tag) -> bool:
    node: Tag | None = el
    depth = 0
    while isinstance(node, Tag) and depth < 6:
        if _HIDDEN_STYLE_RE.search(node.get("style") or ""):
            return True
        node = node.parent if isinstance(node.parent, Tag) else None
        depth += 1
    return False


def _top_fraction_elements(body: Tag, fraction: float = 0.4) -> list[Tag]:
    """All descendant tags, document order, truncated to the first `fraction`
    of them — a cheap proxy for "top N% of the page" without a real layout
    engine (no rendering available at this stage; ZenRows already rendered
    JS server-side, we only have the resulting static HTML).

    Excludes nav/header/footer/aside descendants before counting — a bulky
    mega-nav (dozens of `<li>`/`<a>` tags before any real content) otherwise
    pushes the actual hero/price content past the tag-count cutoff even
    though it sits visually near the top of the page. Confirmed as a real
    gap via code audit, not just theoretical.
    """
    all_tags = [
        t for t in body.find_all(True) if not any(t.find_parent(n) for n in _BOILERPLATE_ANCESTORS)
    ]
    cutoff = max(1, int(len(all_tags) * fraction))
    return all_tags[:cutoff]


def _find_hero_zone(body: Tag) -> Tag | None:
    # Skip Shopify's persistent, theme-boilerplate cart drawer (present on
    # every page, usually empty/hidden) — it matches form[action*="/cart"]
    # just as readily as the product's own add-to-cart form. Confirmed live
    # (extraction-gap Round 1 diagnosis): this was the single most common
    # false-positive hero match, 7/26 sampled failures.
    for cart_form in body.select('form[action*="/cart"]'):
        if _has_ancestor_matching(cart_form, _DRAWER_ANCESTOR_RE):
            continue
        if _is_hidden(cart_form):
            continue
        # The form itself is usually just a submit button — its parent
        # section holds the surrounding price/headline context.
        candidate = cart_form.parent if isinstance(cart_form.parent, Tag) else cart_form
        # Non-Shopify sites (marketplaces like Amazon) have their own
        # checkout/cart forms whose action also happens to contain "/cart"
        # (e.g. "/gp/cart/desktop/go-to-checkout.html"), matching this
        # selector just as readily — but the surrounding content is a
        # checkout/subscription-delivery widget ("View Cart", "Proceed to
        # checkout", "Choose how often it's delivered"), not the product's
        # own name/brand/description. Confirmed live (multi-field
        # extraction-gap investigation, 2026-08-15): 8/21 real-product-page
        # failures in one sample were Amazon listings hitting exactly this.
        widget_text = candidate.get_text(separator=" ", strip=True)[:500]
        if _CHECKOUT_FLOW_TEXT_RE.search(widget_text):
            continue
        return candidate

    offer_grid = body.select_one(".offer-grid, [class*='offer-grid']")
    if offer_grid is not None:
        return offer_grid

    # Broadened beyond heading-only tags: direct-response/funnel pages
    # typically render a big styled price in a div/span/p (CSS-driven size),
    # not a semantic h1-h3 — the heading-only check systematically missed
    # this, confirmed via code audit against the Phase 0.5e live-test notes.
    # Bounded to short text (<300 chars) so a broadly-matching container
    # (e.g. a whole page wrapper) doesn't get selected just because a price
    # appears somewhere deep inside it.
    #
    # Scores every currency-bearing candidate instead of taking the first
    # document-order match — confirmed live (Round 1) that "first match"
    # frequently grabbed a shipping-threshold/announcement-bar banner
    # ("Free shipping on orders over $50") or a rhetorical competitor-price
    # mention ("Spending $1,029 on Ozempic") ahead of the real offer
    # section, 6/26 sampled failures for the banner case alone.
    best: tuple[int, Tag] | None = None
    for el in _top_fraction_elements(body):
        if el.name not in _HERO_TAGS:
            continue
        text = el.get_text(separator=" ", strip=True)
        if not (0 < len(text) < 300 and _CURRENCY_RE.search(text)):
            continue
        if _has_ancestor_matching(el, _BANNER_ANCESTOR_RE):
            continue
        if _has_ancestor_matching(el, _CART_WIDGET_ANCESTOR_RE):
            continue
        # The drawer-ancestor exclusion was previously only applied to the
        # cart_form branch above — this branch (a separate scan over any
        # currency-bearing div/span/p) had no such check, so a cart drawer
        # that happened to also contain a dollar amount (e.g. a subtotal or
        # promo line inside the drawer markup) could still be picked up via
        # this path even after the cart_form fix. Confirmed live
        # (extraction-gap Round 2): 4/24 sampled failures this round alone.
        if _has_ancestor_matching(el, _DRAWER_ANCESTOR_RE):
            continue
        if _is_hidden(el):
            continue
        if _SHIPPING_CONTEXT_RE.search(text):
            continue

        score = 2 if _PURCHASE_CONTEXT_RE.search(text) else 1
        parent = el.parent
        candidate = el
        if (
            isinstance(parent, Tag)
            and parent.name not in ("body", "html")
            and len(parent.get_text(separator=" ", strip=True)) < 2000
        ):
            # Widen to the parent for surrounding context — but never to
            # body/html itself, which would silently re-include sibling
            # content (e.g. a denylisted banner) the scoring just excluded.
            candidate = parent

        if best is None or score > best[0]:
            best = (score, candidate)

    return best[1] if best else None


def _find_social_proof_zone(body: Tag) -> Tag | None:
    for el in body.find_all(["section", "div"]):
        text = el.get_text(separator=" ", strip=True)
        if 0 < len(text) < 2000 and _SOCIAL_PROOF_RE.search(text):
            return el
    return None


def _find_specs_zone(body: Tag) -> Tag | None:
    for ul in body.find_all("ul"):
        items = ul.find_all("li", recursive=False)
        if len(items) >= 2:
            return ul
    return None


def extract_zones(html: str) -> dict[str, str]:
    """Returns {"hero": html, "social_proof": html, "specs": html} for
    whichever zones were found — missing zones are simply absent keys."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup

    zones: dict[str, str] = {}
    hero = _find_hero_zone(body)
    if hero is not None:
        zones["hero"] = str(hero)

    social_proof = _find_social_proof_zone(body)
    if social_proof is not None:
        zones["social_proof"] = str(social_proof)

    specs = _find_specs_zone(body)
    if specs is not None:
        zones["specs"] = str(specs)

    return zones


def _html_to_markdown(html_fragment: str) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.body_width = 0
    return converter.handle(html_fragment).strip()


def _truncate_at_paragraph(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_break = truncated.rfind("\n\n")
    if last_break > 0:
        return truncated[:last_break].strip()
    return truncated.strip()


_MIN_HERO_MARKDOWN_LENGTH = 100


def prune_to_markdown(html: str, max_tokens: int = 4000) -> str:
    """Converts the located zones (falling back to the full pruned <body> if
    no zones were found, or if the hero zone found is too small to contain
    real content) to Markdown, truncated at a paragraph break under the
    token budget."""
    zones = extract_zones(html)

    hero_markdown = None
    if "hero" in zones:
        markdown_parts = [_html_to_markdown(zone_html) for zone_html in zones.values()]
        candidate = "\n\n".join(part for part in markdown_parts if part)
        # A "hero" zone can be technically found (matches the cart-form/
        # currency-scan heuristics) but still be a CTA button, an
        # empty-cart-total widget, or bare nav breadcrumbs -- 20-95 chars of
        # boilerplate with no actual product name/brand/description text.
        # Confirmed live (multi-field extraction-gap investigation,
        # 2026-08-15): zenther.co's hero zone was literally "Order Now / And
        # Save Upto 60% / Shop Now" (45 chars) while the real product name
        # and brand sat elsewhere on the page the pruner never captured.
        # Below this threshold, treat it the same as "no hero found".
        if len(candidate) >= _MIN_HERO_MARKDOWN_LENGTH:
            hero_markdown = candidate

    if hero_markdown is not None:
        markdown = hero_markdown
        budget_tokens = max_tokens
    else:
        # Falls back to the full pruned body whenever a real hero specifically
        # wasn't found — not just when zones is entirely empty, and not just
        # when "hero" is absent from the dict (a present-but-too-small hero
        # hits this path too, see hero_markdown above). Previously, a page
        # where social_proof/specs matched but hero didn't still built the
        # LLM's input from only those fragments, silently dropping hero/price
        # content the LLM never had a chance to see.
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "svg", "noscript", "iframe"]):
            tag.decompose()
        markdown = _html_to_markdown(str(soup.body or soup))
        # Doubled budget for the full-body path only: with no zone-based
        # pre-filtering, long-form advertorial copy (guarantee/reviews/
        # description blurbs) routinely pushes the real offer section past
        # the standard budget before truncation ever reaches it. Confirmed
        # live (extraction-gap Round 2): shop.getamalahealth.com and
        # shop.pipitea.com both truncated within ~100 chars of the 4000-
        # token limit, with the real offer price sitting just beyond it.
        budget_tokens = max_tokens * 2

    # Prepend the <title> tag as a labeled anchor line, regardless of which
    # path above was taken. A "hero" zone can pass the length guard above
    # while still being pure offer-tier/review text with zero product
    # identity — confirmed live (multi-field extraction-gap investigation):
    # track.tryrosabella.com's 204-char hero zone was entirely "Buy 1 + Get
    # 1 FREE $19.97 ... 'Excellent' | 134 reviews", no product name/brand
    # anywhere, while <title> held the clean answer ("Rosabella Beetroot").
    # Deliberately NOT a deterministic product_name source here (that's
    # zenrows_scraper.py's separate, conservative title-tag heuristic,
    # gated on a "ProductName – ShopName" separator to avoid misreading
    # advertorial hook headlines) — titles with no separator, like this one,
    # are exactly the ambiguous case that heuristic can't safely resolve.
    # Handing the raw title to the LLM as auxiliary context lets its
    # judgment (not a regex) decide whether it's a real product name.
    title_soup = BeautifulSoup(html, "html.parser")
    title_prefix = ""
    if title_soup.title and title_soup.title.string:
        title_text = title_soup.title.string.strip()[:200]
        if title_text:
            title_prefix = f"Page title: {title_text}\n\n"

    return title_prefix + _truncate_at_paragraph(markdown, budget_tokens * _CHARS_PER_TOKEN)
