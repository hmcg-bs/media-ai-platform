from ingestion.zone_pruner import extract_zones, prune_to_markdown

_ADVERTORIAL_HTML = """
<html><body>
<nav>Home | Shop | About | Contact | Search</nav>
<div id="hero">
  <h1>Rosabella Beetroot $39.95</h1>
  <form action="/cart/add">
    <button>Add to Cart</button>
  </form>
</div>
<section id="reviews">
  <p>'Excellent' rated 4.8 stars, 134 reviews from happy customers.</p>
</section>
<div id="specs">
  <ul>
    <li>1300mg Beetroot per serving</li>
    <li>30 servings per bottle</li>
    <li>Non-GMO, vegan</li>
  </ul>
</div>
<footer>Copyright 2026. Privacy Policy. Terms of Service.</footer>
</body></html>
"""

_NO_ZONE_HTML = (
    "<html><body><p>Just some plain unstructured text with no clear sections.</p></body></html>"
)

_FILLER = "".join(f"<p>Filler paragraph number {i} with unrelated text.</p>" for i in range(10))


class TestExtractZones:
    def test_finds_hero_via_cart_form(self) -> None:
        zones = extract_zones(_ADVERTORIAL_HTML)
        assert "hero" in zones
        assert "cart" in zones["hero"]

    def test_finds_social_proof_zone(self) -> None:
        zones = extract_zones(_ADVERTORIAL_HTML)
        assert "social_proof" in zones
        assert "134 reviews" in zones["social_proof"]

    def test_finds_specs_zone(self) -> None:
        zones = extract_zones(_ADVERTORIAL_HTML)
        assert "specs" in zones
        assert "1300mg" in zones["specs"]

    def test_no_zones_found_returns_empty_dict(self) -> None:
        assert extract_zones(_NO_ZONE_HTML) == {}

    def test_offer_grid_class_found_as_hero(self) -> None:
        html = '<html><body><div class="offer-grid"><p>1 Bottle $39.95</p></div></body></html>'
        zones = extract_zones(html)
        assert "hero" in zones

    def test_finds_hero_via_non_heading_price_element(self) -> None:
        """Regression: direct-response funnel pages typically render a big
        styled price in a div/span/p, not a semantic h1-h3 — the previous
        heading-only currency check systematically missed this."""
        html = (
            "<html><body>"
            '<span class="price-tag">$49.95</span>'
            "<p>Some more unrelated filler text about shipping and returns policies.</p>"
            "<p>Another paragraph of filler text unrelated to pricing at all.</p>"
            "<p>Yet another filler paragraph just to pad out the tag count nicely.</p>"
            "</body></html>"
        )
        zones = extract_zones(html)
        assert "hero" in zones
        assert "49.95" in zones["hero"]

    def test_hero_price_found_behind_bulky_nav(self) -> None:
        """Regression: _top_fraction_elements previously counted nav/header
        boilerplate tags toward its 40% cutoff, so a bulky mega-nav (many
        <a> links before any real content) pushed the actual hero/price
        content past the cutoff even though it sits visually near the top
        of the page. Nav descendants are now excluded from the count."""
        nav_links = "".join(f"<a href='/nav{i}'>Link number {i}</a>" for i in range(100))
        filler_paragraphs = "".join(
            f"<p>Filler paragraph number {i} with unrelated text.</p>" for i in range(10)
        )
        html = (
            "<html><body>"
            f"<nav>{nav_links}</nav>"
            f'<main><div class="hero-price">Only $49.95 today</div>{filler_paragraphs}</main>'
            "</body></html>"
        )
        zones = extract_zones(html)
        assert "hero" in zones
        assert "49.95" in zones["hero"]

    def test_cart_drawer_form_is_skipped_for_real_product_form(self) -> None:
        """Regression: Shopify's persistent, theme-boilerplate cart drawer
        (present on every page, usually empty) matches form[action*="/cart"]
        just as readily as the product's own add-to-cart form. Confirmed
        live (extraction-gap Round 1): the single most common false-positive
        hero match, 7/26 sampled failures."""
        html = """
        <html><body>
        <div id="CartDrawer" class="drawer drawer--right is-empty">
          <form action="/cart" class="cart-drawer__form"><button>Update Cart</button></form>
        </div>
        <product-form class="product-form">
          <form action="/cart/add" class="form">
            <span>$59.95</span><button>Add to Cart</button>
          </form>
        </product-form>
        </body></html>
        """
        zones = extract_zones(html)
        assert "hero" in zones
        assert "59.95" in zones["hero"]
        assert "Update Cart" not in zones["hero"]

    def test_marketplace_checkout_form_is_skipped_for_real_product_form(self) -> None:
        """Regression: marketplace sites (Amazon) have their own cart/checkout
        forms whose action also contains "/cart" (e.g.
        "/gp/cart/desktop/go-to-checkout.html"), matching form[action*="/cart"]
        just as readily as a real add-to-cart form — but the surrounding
        content is a checkout/subscription-delivery widget ("View Cart",
        "Proceed to checkout", "Choose how often it's delivered"), not the
        product's own name/brand/description. Confirmed live (multi-field
        extraction-gap investigation): 8/21 real-product-page failures in one
        sample were Amazon listings hitting exactly this."""
        html = """
        <html><body>
        <div class="uss-c-sub-nav">
          <a href="/gp/cart/view.html">View Cart</a>
          <form action="/gp/cart/desktop/go-to-checkout.html">
            <button>Proceed to checkout</button>
            <p>Choose how often it's delivered. Potential yearly savings.</p>
          </form>
        </div>
        <product-form class="product-form">
          <form action="/cart/add" class="form">
            <span>$59.95</span><button>Add to Cart</button>
          </form>
        </product-form>
        </body></html>
        """
        zones = extract_zones(html)
        assert "hero" in zones
        assert "59.95" in zones["hero"]
        assert "Proceed to checkout" not in zones["hero"]

    def test_announcement_bar_shipping_banner_is_not_selected_as_hero(self) -> None:
        """Regression: broadened div/span/p currency scan previously took the
        first document-order dollar-bearing element, frequently grabbing a
        shipping-threshold/announcement-bar banner ("Free shipping on orders
        over $50") ahead of the real offer section. Confirmed live: 6/26
        sampled failures for this pattern alone."""
        html = (
            "<html><body>"
            '<div class="announcement-bar-section">Free shipping on orders over $50</div>'
            '<main><div class="product-price">Only $39.95 today, buy now and save</div>'
            f"{_FILLER}</main>"
            "</body></html>"
        )
        zones = extract_zones(html)
        assert "hero" in zones
        assert "39.95" in zones["hero"]
        assert "shipping" not in zones["hero"].lower()

    def test_rhetorical_competitor_price_is_not_selected_as_hero(self) -> None:
        """Regression: an article-body rhetorical price mention ("Spending
        $1,029 on Ozempic every month") isn't in a banner-classed ancestor,
        so the ancestor denylist alone doesn't catch it — scoring must
        prefer a candidate with purchase-context words over one without."""
        html = (
            "<html><body>"
            "<p>Spending $1,029 on Ozempic. Every single month.</p>"
            '<main><div class="offer">Buy now for just $59, one-time purchase</div>'
            f"{_FILLER}</main>"
            "</body></html>"
        )
        zones = extract_zones(html)
        assert "hero" in zones
        assert "59" in zones["hero"]
        assert "1,029" not in zones["hero"] and "1029" not in zones["hero"]

    def test_cart_subtotal_widget_is_not_selected_as_hero(self) -> None:
        """Regression: a mini-cart subtotal widget (e.g. "$0.00" on an empty
        cart) matches the currency regex just as readily as a real price.
        Confirmed live: infinisnutrition.com's hero matched
        <div class="ajaxcart__subtotal"> instead of the actual product
        price."""
        html = (
            "<html><body>"
            '<div class="cart__item-sub cart__item-row">'
            '<div class="ajaxcart__subtotal">Subtotal</div><div data-subtotal="">$0.00</div>'
            "</div>"
            '<main><div class="product-price">Buy now for $44.99, one-time purchase</div>'
            f"{_FILLER}</main>"
            "</body></html>"
        )
        zones = extract_zones(html)
        assert "hero" in zones
        assert "44.99" in zones["hero"]
        assert "0.00" not in zones["hero"]

    def test_hidden_product_form_is_skipped_for_visible_form(self) -> None:
        """Regression: a visually-hidden product-form (style="display:none",
        progressive-enhancement markup Shopify themes commonly leave in the
        DOM) matches the cart-form check just as readily as the real,
        visible form. Confirmed live (extraction-gap Round 2): 5 cumulative
        sampled failures across two rounds (getmorningwould.shop,
        ancestralsupplements.com x2, tryuvola.com, mortaine.co)."""
        html = """
        <html><body>
        <product-form style="display:none">
          <form action="/cart/add"><span>Hidden variant, ignore</span></form>
        </product-form>
        <product-form class="product-form">
          <form action="/cart/add"><span>$49.99</span><button>Add to Cart</button></form>
        </product-form>
        </body></html>
        """
        zones = extract_zones(html)
        assert "hero" in zones
        assert "49.99" in zones["hero"]
        assert "Hidden variant" not in zones["hero"]

    def test_drawer_excluded_from_currency_scan_too(self) -> None:
        """Regression: the drawer-ancestor exclusion was previously only
        applied to the cart_form branch, not the separate currency-scan
        branch — a cart drawer that also happens to contain a dollar amount
        (e.g. a subtotal or promo line inside the drawer markup) could still
        be picked up via that second path. Confirmed live (extraction-gap
        Round 2): 4/24 sampled failures this round alone
        (nano-revive.com, rhonutrition.com, sandhus.com, weheartnutrition.com)."""
        html = (
            "<html><body>"
            '<div id="CartDrawer" class="drawer drawer--right is-empty">'
            '<div class="drawer__subtotal">Subtotal <span>$0.00</span></div>'
            "</div>"
            '<main><div class="offer">Buy now for $34.99, one-time purchase</div>'
            f"{_FILLER}</main>"
            "</body></html>"
        )
        zones = extract_zones(html)
        assert "hero" in zones
        assert "34.99" in zones["hero"]
        assert "0.00" not in zones["hero"]


class TestPruneToMarkdown:
    def test_excludes_nav_and_footer_boilerplate(self) -> None:
        markdown = prune_to_markdown(_ADVERTORIAL_HTML)
        assert "Privacy Policy" not in markdown
        assert "Search" not in markdown

    def test_full_body_fallback_gets_doubled_budget(self) -> None:
        """Regression: with no hero zone, a long advertorial page's real
        offer section frequently sits past a single-width truncation
        budget. Confirmed live (extraction-gap Round 2):
        shop.getamalahealth.com and shop.pipitea.com both truncated within
        ~100 chars of the standard 4000-token limit, with the real offer
        price sitting just beyond it. The full-body fallback path should
        get double the budget the zone-based path gets for the same
        max_tokens argument."""
        # A page with a real hero zone: markdown is bounded by max_tokens.
        hero_html = _ADVERTORIAL_HTML
        hero_markdown = prune_to_markdown(hero_html, max_tokens=10)
        assert len(hero_markdown) <= 10 * 4

        # A page with no hero zone at all: full-body fallback, double budget.
        no_hero_html = "<html><body><p>" + ("padding text here. " * 50) + "</p></body></html>"
        no_hero_markdown = prune_to_markdown(no_hero_html, max_tokens=10)
        assert len(no_hero_markdown) <= 10 * 4 * 2
        assert len(no_hero_markdown) > 10 * 4  # actually used the wider budget

    def test_includes_zone_content(self) -> None:
        markdown = prune_to_markdown(_ADVERTORIAL_HTML)
        assert "39.95" in markdown
        assert "134 reviews" in markdown
        assert "1300mg" in markdown

    def test_tiny_junk_hero_zone_falls_back_to_full_body(self) -> None:
        """Regression: a "hero" zone can be technically found (matches the
        cart-form/currency-scan heuristics) but still be a CTA button, an
        empty-cart-total widget, or bare nav breadcrumbs with no real
        product content. Confirmed live (multi-field extraction-gap
        investigation, 2026-08-15): zenther.co's hero zone was literally
        "Order Now / And Save Upto 60% / Shop Now" (45 chars) while the
        real product name and brand sat elsewhere on the page the pruner
        never captured, so the LLM extracted nothing at all."""
        html = """
        <html><body>
        <div class="sticky-cta">
        <form action="/cart/add"><button>Order Now — Save Upto 60% — Shop Now</button></form>
        </div>
        <main>
        <h1>BeetWise Nitric Oxide Booster</h1>
        <p>Made by Zenther, this daily beet-root formula supports healthy blood flow
        and energy. Third-party tested, non-GMO, and backed by a 90-day guarantee.</p>
        </main>
        </body></html>
        """
        zones = extract_zones(html)
        assert "hero" in zones  # the tiny CTA form still matches the heuristic
        assert len(zones["hero"]) < 200

        markdown = prune_to_markdown(html)
        assert "BeetWise" in markdown
        assert "Zenther" in markdown

    def test_hero_zone_with_enough_content_is_not_discarded(self) -> None:
        """A hero zone at or above the minimum length is trusted as-is —
        the fallback only triggers for genuinely tiny/junk zones."""
        html = (
            "<html><body>"
            '<div class="offer-grid">'
            "<p>Full Sun Beetroot Extract — clinically dosed nitric oxide support, "
            "$34.95 for a 30-day supply, third-party tested and non-GMO.</p>"
            "</div>"
            "</body></html>"
        )
        markdown = prune_to_markdown(html)
        assert "34.95" in markdown
        assert "Full Sun Beetroot" in markdown

    def test_title_tag_prepended_when_hero_zone_lacks_product_identity(self) -> None:
        """Regression: a hero zone can be substantively sized (well above
        the tiny-junk-zone threshold) yet still be pure offer-tier/review
        text with zero product name or brand anywhere in it. Confirmed live
        (multi-field extraction-gap investigation): track.tryrosabella.com's
        204-char hero zone was entirely "Buy 1 + Get 1 FREE $19.97 ...
        'Excellent' | 134 reviews" — no product identity, while <title>
        held the clean answer. The title tag is now always prepended as
        auxiliary LLM context, regardless of which markdown path is used."""
        html = """
        <html><head><title>Rosabella Beetroot</title></head><body>
        <div class="offer-grid">
        <p>Buy 1 + Get 1 FREE $19.97 Save 10% $17.97/ea Most Popular</p>
        <p>Buy 2 + Get 2 FREE $19.97 Save 26% $14.73/ea Best Deal</p>
        <p>Buy 3 + Get 3 FREE $19.97 Save 40% $11.99/ea</p>
        <p>'Excellent' | 134 reviews</p>
        </div>
        </body></html>
        """
        markdown = prune_to_markdown(html)
        assert markdown.startswith("Page title: Rosabella Beetroot")

    def test_no_title_tag_produces_no_prefix(self) -> None:
        markdown = prune_to_markdown(_ADVERTORIAL_HTML)
        assert not markdown.startswith("Page title:")

    def test_falls_back_to_pruned_body_when_no_zones(self) -> None:
        markdown = prune_to_markdown(_NO_ZONE_HTML)
        assert "plain unstructured text" in markdown

    def test_falls_back_to_full_body_when_hero_missing_but_other_zones_found(self) -> None:
        """Regression: prune_to_markdown previously only fell back to the
        full body when extract_zones() returned an entirely empty dict. A
        page where social_proof/specs matched but hero specifically didn't
        still built the LLM's input from only those fragments, silently
        dropping content outside any matched zone (e.g. the intro/hero copy
        itself) even though the full-body fallback path existed."""
        html = """
        <html><body>
        <p id="intro">Welcome to our product page with a lengthy hero pitch and no
        price mentioned in this exact sentence at all.</p>
        <section id="reviews"><p>Rated 4.8 stars, 200 reviews from happy customers.</p></section>
        <div id="specs"><ul><li>Ingredient A</li><li>Ingredient B</li></ul></div>
        </body></html>
        """
        zones = extract_zones(html)
        assert "hero" not in zones  # no cart-form/offer-grid/price element anywhere
        assert "social_proof" in zones and "specs" in zones  # the other two did match

        markdown = prune_to_markdown(html)
        assert "Welcome to our product page" in markdown  # would be dropped before this fix
        assert "200 reviews" in markdown
        assert "Ingredient A" in markdown

    def test_respects_token_budget(self) -> None:
        long_html = "<html><body><ul>" + "".join(
            f"<li>Paragraph number {i} with some filler content to pad length out.</li>\n\n"
            for i in range(500)
        ) + "</ul></body></html>"
        markdown = prune_to_markdown(long_html, max_tokens=100)
        # No hero zone in this fixture -> full-body fallback, which uses a
        # doubled budget (see prune_to_markdown) since it has no zone-based
        # pre-filtering and long-form pages routinely push real content past
        # a single-width budget before truncation reaches it.
        assert len(markdown) <= 100 * 4 * 2

    def test_truncates_at_paragraph_break_not_mid_sentence(self) -> None:
        html = (
            "<html><body><ul>"
            + "<li>" + ("Sentence one is reasonably long padding text here. " * 3) + "</li>\n\n"
            + "<li>" + ("Sentence two is reasonably long padding text here. " * 3) + "</li>\n\n"
            + "<li>" + ("Sentence three is reasonably long padding text here. " * 3) + "</li>"
            + "</ul></body></html>"
        )
        markdown = prune_to_markdown(html, max_tokens=40)
        # Truncated text should not end mid-word (a lone trailing partial token).
        assert not markdown.endswith(("Sent", "Sente", "pad"))
