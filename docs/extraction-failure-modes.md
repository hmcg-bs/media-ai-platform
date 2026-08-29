# Landing-Page Extraction: Known Failure Modes

Catalog of confirmed price-extraction failure modes in the ZenRows/Tier-5-LLM
cascade (`ingestion/zenrows_scraper.py`, `ingestion/zone_pruner.py`,
`ingestion/llm_fallback.py`), found via live-sampling real currently-failing
pages rather than code-only review. Each entry: symptom, root cause, fix,
regression-test reference, and real example URLs. Update this file whenever a
new failure mode is root-caused — it's the durable record a future round of
diagnosis should check first, before re-discovering the same bug.

Source: [wayfinder map issue #1](https://github.com/hmcg-bs/media-ai-platform/issues/1).
["Round 1" (issue #28)](https://github.com/hmcg-bs/media-ai-platform/issues/28) —
100 random price-missing ads sampled, 26 had a real price the pipeline
missed, 4 bugs found (#1-4 below). ["Round 2" (issue #31)](https://github.com/hmcg-bs/media-ai-platform/issues/31) —
another 100 sampled from the *post-Round-1* residual pool, 24 had a real
price the pipeline missed, 3 more bugs found (#5-7 below) plus one attempted
fix (comparison-table disambiguation) that surfaced a distinct, deeper
data-completeness gap instead of resolving the cases it targeted.
["Round 3" (issue #32)](https://github.com/hmcg-bs/media-ai-platform/issues/32) —
100 more ads sampled from the post-Round-2 residual pool (82 unique URLs,
fresh seed): 5 already recovered a price via the current cascade (a
reprocessing gap, not a bug), 7 fetch failures (2 `chewy.com` URLs with
literal unresolved Facebook ad-template macros like `{{campaign.id}}` in the
URL, 2 non-landing-page click-through links (`facebook.com`,
`api.whatsapp.com`), 1 store-locator page, 1 oversized-payload 413, 1 bad
status — none in scope), 70 still price-missing after the full cascade
(including Tier 5 LLM). Of those 70, a raw-HTML currency-pattern scan
followed by manual review found roughly 9 plausible genuine misses (down
sharply from Round 2's 41.4%) — but they split across several small,
distinct suspects (2 PageFly builder-extraction failures, ~3 suspected
JS-render-timing races, a few one-off template misses), none individually
clearing the >5-sample bar. **No fix shipped this round** — see the "Not yet
root-caused" section for what was found. Also resolved the "check Amazon
prevalence" open question from Round 2/#31: only 19 ads / 12 unique URLs
(0.7% of the corpus) are Amazon — confirmed not high-leverage, a dedicated
extractor already exists (bug #12) and needs no further investment.
**Multi-field extraction-gap investigation** (2026-08-15) — broadened scope
beyond price-only: sampled all 154 ads (61 unique URLs) still missing *all
four* of price/product_name/brand_name/rating. 32/61 URLs diagnosed
successfully (29 permanent connection/dead-link failures, out of scope); of
those, 21 were genuine product pages a plain LLM call could trivially answer
despite the production pipeline extracting nothing — traced to 3 more bugs
(#8-10 below). A same-day follow-on round dug into the 4 residual failures
from that batch's live-verified lift check, then pulled a fresh 25-URL
(91-ad) sample from the broader "missing brand_name OR product_name"
population (452 unique URLs) to check whether the fixes generalized —
3 more bugs found (#11-13 below), all brand/name-focused, none overlapping
#8-10.

---

## 1. Cart-drawer false positive

**Symptom**: `product_price` is `None` even though ZenRows fetched the page
successfully and a real price is visible in the rendered HTML.

**Root cause**: `zone_pruner.py::_find_hero_zone`'s first check,
`form[action*="/cart"]`, matches Shopify's persistent theme cart drawer — a
slide-out panel present on *every* page of the store, usually empty, that
happens to have a form whose action also points at `/cart`. It matches just
as readily as the product's own add-to-cart form, and since it's checked
first and returned immediately, it wins even when a real product form exists
elsewhere on the page.

**Fix**: `zone_pruner.py::_find_hero_zone` now iterates *all*
`form[action*="/cart"]` matches (not just the first) and skips any whose
ancestor chain (up to 6 levels, via `_has_ancestor_matching`) has an
id/class matching `drawer` (case-insensitive).

**Why an LLM classification step can't fix this**: this happens *before* the
LLM ever runs — `zone_pruner.py` decides which HTML fragment becomes the
"hero zone" fed into the LLM prompt. By the time the LLM sees anything, the
wrong (or right) zone has already been chosen; the LLM never gets to compare
the drawer against the real form. The regex-based ancestor exclusion is the
whole fix — this is a zone-selection guardrail, not a model-judgment one.

**Regression test**: `ingestion/tests/test_zone_pruner.py::TestExtractZones::test_cart_drawer_form_is_skipped_for_real_product_form`

**Real examples**: `infinisnutrition.com`, `ancestralsupplements.com/pages/beef-thyroid-v1`,
`formula707.com/pages/support-pro-calming-deal`, `rhonutrition.com/pages/shop`,
`rituallabs.shop/products/happy-liver` (both variants), `wuffes.com/pages/pb-5-reasons-2026-v1`
(7/26 sampled failures)

---

## 2. Announcement-banner / rhetorical-price false positive

**Symptom**: `product_price` is `None` or wrong; the hero zone contains a
dollar amount that isn't the product's price at all.

**Root cause**: `zone_pruner.py::_find_hero_zone`'s broadened div/span/p
currency scan (added to catch prices rendered outside semantic headings)
originally took the *first* document-order dollar-bearing element as the
hero zone. Two distinct sub-patterns both hit this:
- **Shipping/promo banners**: "Free shipping on orders over $50" in an
  announcement bar, above-header banner, or top-bar section — these render
  early in the DOM, ahead of the real offer section.
- **Rhetorical/competitor-price mentions**: article-body copy contrasting
  the featured product against a competitor's price, e.g. "Spending $1,029
  on Ozempic. Every single month." — not in a banner-classed container, so
  an ancestor-class denylist alone doesn't catch it.

**Fix**: `zone_pruner.py::_find_hero_zone`'s currency-scan branch now scores
*every* candidate instead of returning the first: denylists banner-classed
ancestors (`announcement`, `topbar`, `above-header`, `promo-bar`,
`header-group`) and shipping-context phrasing (`free shipping`, `orders
over`, `spend $`), and rewards candidates containing purchase-context
keywords (`buy`, `bottle`, `add to cart`, `subscribe`, `one-time`, `save`,
`off`, `checkout`). Highest-scoring candidate wins.

**Regression tests**:
`ingestion/tests/test_zone_pruner.py::TestExtractZones::test_announcement_bar_shipping_banner_is_not_selected_as_hero`,
`::test_rhetorical_competitor_price_is_not_selected_as_hero`

**Real examples**: `mengotomars.com/pages/weight-loss-v1`, `track.tryrosabella.com`
(5 tracker URLs), `healthinsider.news` (both GLP-1 comparison pages),
`promixnutrition.com/pages/build-your-bundle`, `shop.pipitea.com/ppch/sp`,
`track.getamalahealth.com` (11/26 sampled failures, combining the original
banner cases with reclassified "other hero match" cases sharing this root
cause)

**Planned follow-up** (Phase 0.5h, in progress): shift more of this
disambiguation from regex denylist/keyword-scoring onto explicit LLM
classification (`price_context` field, see below) — regex denylists only
catch patterns anticipated in advance; an LLM can generalize to novel
banner/rhetorical phrasing regex hasn't seen yet.

---

## 3. Cart-subtotal-widget false positive

**Symptom**: `product_price` resolves to `$0.00` or another clearly-wrong
small value.

**Root cause**: A mini-cart subtotal widget (e.g. `<div
class="ajaxcart__subtotal">Subtotal</div><div>$0.00</div>` on an empty
cart) matches the currency-scan regex just as readily as a real price.

**Fix**: Folded into the same scoring pass as failure mode #2 — added
`subtotal`, `ajaxcart`, `minicart`, `cart__item` to the ancestor denylist.

**Regression test**: `ingestion/tests/test_zone_pruner.py::TestExtractZones::test_cart_subtotal_widget_is_not_selected_as_hero`

**Real examples**: `infinisnutrition.com` (1 direct sample this round; likely
undercounted since this pattern would also silently corrupt price on pages
where zone selection otherwise succeeded)

---

## 4. Guardrail rejecting legitimate derived bundle totals

**Symptom**: `product_price` is `None` even though the LLM correctly
extracted a bundle offer with a real, grounded per-unit price.

**Root cause**: Bundle/tiered offer pages ("Buy 2 + Get 2 FREE") almost
never show the multiplied total price verbatim — only the per-unit
discounted price (e.g. page shows "$14.73/ea", not "$58.92 total"). The LLM
correctly computes `total_price = quantity × price_per_unit` (4 × $14.73 =
$58.92), but `llm_fallback.py::validate_against_raw_text`'s hallucination
guardrail checked only `total_price` against the raw page text — "$58.92"
never appears verbatim, so the guardrail treated it as hallucinated and
nulled **both** `total_price` *and* `price_per_unit`, even though
`price_per_unit` ($14.73) was independently grounded in the page.

**Fix**: `validate_against_raw_text` now checks `price_per_unit` too; if
either `total_price` or `price_per_unit` is grounded in the raw text, both
fields are kept. Only nulls both when *neither* is grounded.

**Regression tests**:
`ingestion/tests/test_llm_fallback.py::TestValidateAgainstRawText::test_derived_total_price_trusted_when_price_per_unit_grounded`,
`::test_total_price_still_rejected_when_neither_total_nor_per_unit_grounded`

**Real examples**: `track.tryrosabella.com` (all 5 sampled tracker URLs),
`shop.pipitea.com/ppch/sp`, `track.getamalahealth.com` (≥7/26 sampled
failures)

**Planned follow-up** (Phase 0.5h, closed): explicit `price_context:
"bundle_price"` classification plus a prompt instruction to always reason
about "N units for $X → $X/N per unit" — this fix handles *validation*
(don't wrongly reject a correct derived total); the follow-up handles
*extraction quality* (make the model's bundle reasoning explicit and
consistent, not just implicitly correct often enough to pass the guardrail).

---

## 5. Hidden `display:none` product-form false positive

**Symptom**: `product_price` is `None` even though a real, visible price
exists elsewhere on the page.

**Root cause**: A visually-hidden product-form (`style="display:none"`,
progressive-enhancement markup Shopify themes commonly leave in the DOM —
e.g. a variant-specific form that only becomes visible after a JS
interaction) matches the `form[action*="/cart"]` hero-zone check just as
readily as the real, visible form. First seen as a one-off in Round 1
(`getmorningwould.shop`), confirmed as a real recurring pattern in Round 2
(4 more instances).

**Fix**: `zone_pruner.py::_find_hero_zone` now checks (`_is_hidden`) whether
a candidate cart-form — or any of its ancestors, up to 6 levels — carries an
inline `display:none` style, and skips it if so.

**Regression test**: `ingestion/tests/test_zone_pruner.py::TestExtractZones::test_hidden_product_form_is_skipped_for_visible_form`

**Real examples**: `getmorningwould.shop` (Round 1), `ancestralsupplements.com`
(both `beef-thyroid-v1` and `liver-changes-lives-v1`), `tryuvola.com/pages/liver-detox`,
`mortaine.co/pages/5-reasons-grow-spark-f` (Round 2) — 5 cumulative samples

---

## 6. Cart drawer reachable via the currency-scan branch (gap in bug #1's fix)

**Symptom**: Same as bug #1 (cart-drawer false positive) — `product_price`
resolves to `None` or a wrong small value like `$0.00`.

**Root cause**: Bug #1's drawer-ancestor exclusion (`_DRAWER_ANCESTOR_RE`)
was only ever applied inside the `form[action*="/cart"]` loop. The
currency-scan branch (added for bugs #2/#3) is a *separate* code path that
scans any div/span/p for a dollar amount — it had no drawer check at all, so
a cart drawer that also happens to contain a dollar figure (e.g. a subtotal
or promo line inside the drawer markup) could still be picked up through
this second path even after bug #1's fix shipped. Confirmed live: bug #1's
own fix was already deployed when these 4 pages were sampled in Round 2, and
they still failed via this exact gap.

**Fix**: `_find_hero_zone`'s currency-scan loop now also checks
`_has_ancestor_matching(el, _DRAWER_ANCESTOR_RE)`, mirroring the cart-form
branch's check.

**Regression test**: `ingestion/tests/test_zone_pruner.py::TestExtractZones::test_drawer_excluded_from_currency_scan_too`

**Real examples**: `nano-revive.com/pages/mucus`, `rhonutrition.com/pages/shop`,
`sandhus.com/pages/vitamin-d3-k2-...`, `weheartnutrition.com/pages/pro-life`
(4/24 sampled failures in Round 2 alone)

---

## 7. Full-body-fallback truncation cuts off a late-page offer section

**Symptom**: `product_price` is `None`; no hero zone was found at all, and
the real offer section sits beyond where the full-body-fallback markdown
gets truncated.

**Root cause**: `prune_to_markdown`'s full-body-fallback path (used when no
hero zone is found) used the same token budget as the zone-based path
(`max_tokens`, default 4000 tokens / ~16,000 chars). With no zone-based
pre-filtering, long-form advertorial pages (guarantee blurbs, reviews,
product description all rendered before the actual offer section) routinely
push the real price content past that budget. Confirmed live:
`shop.getamalahealth.com` and `shop.pipitea.com` both truncated within
~100 chars of the 16,000-char limit, with the real "Special Offer" price
sitting just beyond the cut.

**Fix**: `prune_to_markdown` now uses double the token budget specifically
for the full-body-fallback path (the zone-based path is unaffected — it
already bounds content via zone selection, not truncation).

**Regression test**: `ingestion/tests/test_zone_pruner.py::TestPruneToMarkdown::test_full_body_fallback_gets_doubled_budget`

**Real examples**: `shop.getamalahealth.com/pch/sp`, `shop.pipitea.com/hbt/sp`
(2/24 sampled failures — small count, but the fix is a one-line change with
no real downside, so shipped despite being under the usual 5-sample bar)

---

## Comparison-table price disambiguation — attempted, did not resolve the underlying cases

Phase 0.5h added prompt guidance instructing the model to identify the
featured/primary product on a comparison-table page and only extract *that*
product's price. Live-verified in Round 2 against real comparison-table
pages the guidance was specifically meant to fix
(`healthinsider.news/top-5-glp-1-booster-supplements-...`) — the zone
selection *did* correctly capture the featured product's own section
("Our Top Pick – Best Natural GLP-1 Support..."), but the only price signal
on the page is an indirect **"Less than $1 a day"** framing. That's a
data-completeness problem, not a classification problem: converting a daily
rate into a usable `total_price` requires knowing the subscription length,
which isn't stated anywhere on the page. The prompt fix is working as
designed; the comparison-table pages sampled so far just happen to also hit
the separate daily-cost-framing gap below. Still flagged as unresolved —
future rounds should distinguish "comparison table with a real dollar
price" (should now work) from "comparison table with only daily-cost
framing" (still won't).

---

## New capability: subscription-status and subscription-price detection

Not a bug fix — resolves the product decision that was blocking Round 3:
"is an indirect \$X/day rate an acceptable `price` value, does it need a
separate field, or stay null?" Investigation (2026-08-15, 13 real pages)
found the \$/day-framing gap was actually **two unrelated cases** conflated
together:

- **Genuine subscription pricing** (`nano-revive.com`): visible text reads
  *"About \$1.63/day on subscription. Cancel anytime... Save up to 58% on
  subscription"*, confirmed by a real `window.RechargeStorefrontConfig`
  object and Recharge app signatures in the HTML.
- **Rhetorical/marketing \$/day framing, unrelated to subscriptions**
  (`healthinsider.news`): *"Less than \$1 a day"* with zero subscription
  signals anywhere — a persuasive comparison for what's likely a one-time
  bundle price, not a recurring charge. This case is **not** resolved by
  the new fields below; it stays in the long tail (see
  "indirect \$X/day framing" further down).

**Resolution**: `ProductPage` gained two new fields —
`subscription_status: "one_time_only" | "subscription_optional" |
"subscription_required" | "unknown"` and `subscription_price: float | None`
(distinct from the existing `price`, since a page can have both a real
one-time price and a separate recurring price). `unknown` is the honest
default, not a negative claim — detection is signature/keyword-based, so
absence of a match doesn't prove a page has no subscription option.

**Implementation** (`ingestion/subscription_detector.py`, wired into
`zenrows_scraper.py::extract_product_data`, always attempted when HTML is
available, independent of whether a one-time price was already resolved):
deterministic app-signature detection (Recharge, Bold Subscriptions, Skio,
Awtomic, Loop Subscriptions, Appstle — Recharge confirmed present on 6/13
sampled real pages, 46%, the clear majority) plus regex extraction of a
directly-stated day/month rate from visible text, with a day→month ×30
estimate when only a daily rate is stated. **No LLM call** — Recharge's own
`window.RechargeStorefrontConfig` object is real but non-standard
JS-object-literal syntax focused on cart-drawer/cross-sell UI config, not a
clean source for the discount price itself; that price reliably renders as
plain visible text instead, which regex handles more cheaply than parsing
the app's internal config format.

**Guardrail** (found live, same class of bug as the main price extraction's
banner/rhetorical false positives): a day/month-rate regex match is only
trusted once `subscription_status` is no longer `"unknown"` — confirmed
`healthinsider.news` (zero subscription signal) still matched a "\$70 per
month" rate via regex alone, which turned out to be a rhetorical mention
about a *different, compared* product elsewhere on the page, not a real
subscription price for the featured one.

**Known limitations, not yet resolved**:
- Only 6 app signatures covered; a custom/native subscription
  implementation without any of these markers is invisible to detection —
  stays `"unknown"`, not misclassified as `"one_time_only"`.
- `subscription_status` detection is page-wide keyword matching (e.g.
  "Manage Subscription" nav link), which could over-trigger on a
  multi-product store where only *some* products are subscription-eligible
  — not yet verified against a case where this produces a wrong answer, but
  flagged as a real precision risk worth checking in a future round.
- No LLM-fallback path for subscription price yet (e.g. pages that only
  state a discount *percentage* — "Save up to 58%" — without ever stating
  the actual dollar rate anywhere nearby would need correlating that
  percentage against a base price, a genuine LLM-shaped task). Deliberately
  deferred rather than built speculatively — the regex path already covers
  the confirmed real case.

**Regression tests**: `ingestion/tests/test_subscription_detector.py` (15
tests), `ingestion/tests/test_zenrows_scraper.py::TestExtractProductDataSubscriptionDetection`
(5 tests) and `TestToProductPageUpdates` (2 new tests).

---

## 8. Tiny/junk hero zone silently blocks the full-body fallback

**Symptom**: `product_name`/`brand_name`/`price` all `None` on a genuine
product page, even though Tier 5 (LLM fallback) ran and the raw HTML clearly
has usable content.

**Root cause**: `zone_pruner.py::prune_to_markdown` only fell back to
full-body markdown when `extract_zones()` returned a completely empty dict.
If a "hero" zone was found at all — even a near-empty one (e.g. a stray
`<form>` with a submit button and nothing else, no price/name/brand text
anywhere in it) — that tiny fragment became the *entire* pruned markdown fed
to the LLM, with no fallback to the much larger full-body content that
actually had the real product information.

**Fix**: `prune_to_markdown` now discards a hero-zone candidate whose
combined markdown is under 100 characters (`_MIN_HERO_MARKDOWN_LENGTH`) and
falls through to the full-body-fallback path instead of trusting a
near-empty zone.

**Regression tests**: `ingestion/tests/test_zone_pruner.py::TestPruneToMarkdown::test_tiny_junk_hero_zone_falls_back_to_full_body`,
`::test_hero_zone_with_enough_content_is_not_discarded`, `::test_full_body_fallback_gets_doubled_budget`

**Real examples**: found via the multi-field extraction-gap investigation
(2026-08-15, 61-URL sample of ads missing price *and* name *and* brand *and*
rating) — live-verified fix recovers real content on 3/4 spot-checked
examples from that sample.

---

## 9. Marketplace (Amazon) checkout-flow widget masquerading as hero zone

**Symptom**: `product_name`/`brand_name`/`price` all `None` on an Amazon
product listing; the pruned markdown fed to the LLM is entirely
checkout/subscription-delivery boilerplate ("View Cart", "Proceed to
checkout", "Choose how often it's delivered", "Potential yearly savings")
with zero product content.

**Root cause**: same false-positive mechanism as bug #1 (cart-drawer), but
on a completely different site structure. Amazon's own cart/checkout form
action (`/gp/cart/desktop/go-to-checkout.html...`) contains the substring
`/cart`, so it matches `_find_hero_zone`'s `form[action*="/cart"]` selector
just as readily as a Shopify add-to-cart form — but bug #1's fix
(`_DRAWER_ANCESTOR_RE`) only covers Shopify's class-naming conventions
("drawer"), which Amazon's markup doesn't use at all.

**Fix**: `_find_hero_zone`'s cart-form loop now also checks the candidate's
own text against `_CHECKOUT_FLOW_TEXT_RE` ("proceed to checkout", "view
cart", "choose how often", "delivery frequency", "potential ... savings")
and skips it if matched — a content-based check rather than another
ancestor-class denylist, since marketplace sites don't share Shopify's
class-naming conventions to denylist against.

**Regression test**: `ingestion/tests/test_zone_pruner.py::TestExtractZones::test_marketplace_checkout_form_is_skipped_for_real_product_form`

**Real examples**: 8/10 Amazon URLs in the 61-URL multi-field sample were
genuine product pages hitting exactly this; live-verified against
`amazon.com/Purity-Products-Digestive-Probiotics-Prebiotics/dp/B0G3CFKWNQ`
— `product_name`/`brand_name` now extract cleanly (`price` still `None` on
this specific page, but that's the hallucination guardrail correctly
rejecting a fabricated multi-item total, a separate, pre-existing Amazon
price gap, not this bug).

---

## 10. `product_name`/`brand_name` were structurally unreachable outside Tier 4.5/5

**Symptom**: `product_name`/`brand_name` are `None` even on pages where
Tiers 1-4 (XHR/JSON-LD/window-objects/DOM) cleanly resolved
price/description/rating — and even where the LLM (Tier 5) ran but the
zone-pruned markdown it saw happened not to include the product's own name
or brand text (common on long-form "advertorial"/listicle pages, where the
featured product's name is mentioned deep in body copy rather than in the
hero/specs/social-proof zones).

**Root cause**: originally hypothesized as an LLM prompt bias toward price
over name/brand — live investigation disproved that. The real cause: Tiers
1-4's own extraction functions (`_extract_from_xhr`, `_extract_from_json_ld`,
`_extract_from_window_objects`, `_extract_from_dom`) never attempted to read
`product_name`/`brand_name` *at all* — those two fields were populated
exclusively by Tier 4.5 (builder fingerprint) or Tier 5 (LLM), which only
run when `_has_min_fields(merged)` is false or price is still missing. A
page whose JSON-LD cleanly provided price/description/rating never got a
chance at name/brand, even though schema.org's `Product.name` and
`Product.brand` are among the most universal fields on a Product block —
and even when Tier 5 *did* run, its zone-pruned view structurally excludes
name/brand on advertorial-style pages.

**Fix**: `_extract_from_json_ld` (Tier 2) now also reads `Product.name` →
`product_name` and `Product.brand` (string or `{name: ...}` object) →
`brand_name`, with two deterministic, no-LLM-cost fallbacks for brand when
a page has no `Product.brand`: a same-page `Organization` JSON-LD block's
`name`, then `<meta property="og:site_name">`. All setdefault-style —
never overwrites a later, more specific value. Free (no LLM call), so a
page never has to wait on Tier 4.5/5 just for these two fields when JSON-LD
already has them.

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestExtractFromJsonLd::test_extracts_product_name_and_string_brand`,
`::test_extracts_brand_as_nested_object`, `::test_organization_name_used_as_brand_fallback`,
`::test_product_brand_wins_over_organization_name`, `::test_og_site_name_used_as_brand_fallback_when_no_json_ld_brand`

**Real examples**: `goldsealsupplements.com/pages/magnesium` (brand recovered
via `og:site_name` = "Gold Seal Supplements"; product_name still `None` —
this is a "Top 5 Magnesium Supplements" roundup article, a genuinely
different, harder page-type problem, not this bug), `mengotomars.com/pages/10-reasons-aging`
(brand recovered via `Organization.name` = "Mars Men"). Live-verified: both
went from `name=None, brand=None` to `brand` correctly populated, zero
regressions in the 303-test suite.

---

## 11. `og:site_name`/`Organization`/`Product.brand` still missed the most universal brand signal: Shopify's own Pixel/Payment-Request data

**Symptom**: `brand_name` still `None` on a page with no `Product.brand`, no
`Organization` JSON-LD block, and no `<meta property="og:site_name">` — the
three sources bug #10 added.

**Root cause**: Shopify's own Pixel-loader `initData` (`"shop":{"name":
"..."}`) and the Apple-Pay/Shopify-Payments merchant-capabilities block
(`"merchantName":"..."`) both carry the storefront's own name in raw
inline-script JS — present on effectively every Shopify page independent of
whether that page has *any* JSON-LD or og:site_name meta tag at all, since
it's loaded for analytics/checkout regardless of page content. Confirmed
live: `getionix.com` has neither of bug #10's three sources, but both these
patterns cleanly resolve "IONIX LABS".

**Fix**: `_extract_from_window_objects` now also regex-matches
`"shop":{"name":"..."}` and `"merchantName":"..."` directly against the raw
HTML (not JSON-LD-scoped, since these live in ordinary `<script>` tags, not
`application/ld+json`), lowest-priority setdefault so a more specific
`Product.brand` still wins when present.

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestExtractFromWindowObjects::test_shopify_pixel_shop_name_used_as_brand`,
`::test_shopify_merchant_name_used_as_brand_fallback`, `::test_json_ld_brand_wins_over_shop_name`

**Real examples**: `getionix.com/pages/looseskin-adv-v3` (live-verified:
`brand_name` went from `None` to `'IONIX LABS'`).

---

## 12. Amazon marketplace pages needed a dedicated extractor, not generic hero-zone heuristics

**Symptom**: `product_name`/`brand_name`/`rating`/`rating_count` all `None`
on an Amazon listing even after bug #9's checkout-widget fix — the hero
zone `_find_hero_zone` picks (once the checkout widget is correctly
excluded) is often still just an unrelated fragment near an Add-to-Cart
button (e.g. a `<td>` containing only "Add to Cart"), not Amazon's actual
`#productTitle` element.

**Root cause**: Amazon isn't a page-builder app, so it never matched any
`BUILDER_SIGNATURES` entry, and `extract_via_builder_fingerprint` always
returned `None` for Amazon URLs — leaving Tier 5's zone-pruned LLM as the
only path, which inherits all of zone_pruner's generic (Shopify-DTC-shaped)
heuristics that don't map onto Amazon's very different page structure.
Amazon's own DOM, though, has extremely stable, well-known element ids
(`#productTitle`, `#bylineInfo`, `.a-icon-alt`, `#acrCustomerReviewText`)
that a generic hero-zone heuristic was never going to find reliably.
Confirmed prevalent, not a one-off: 8-10 Amazon URLs surfaced in a single
61-URL sample of ads missing name/brand/price/rating entirely.

**Fix**: `builder_fingerprint.py` gained a URL-domain-based (not
content-regex-based, unlike the other builders) Amazon detection path,
routed *before* the generic builder-signature check, with dedicated
selectors for title/brand/rating/review-count. Amazon's price wasn't
targeted here — `.a-price` was absent even on a live-verified page (likely
requires deeper JS interaction/isn't always server-rendered), and the
existing hallucination guardrail already correctly rejects the fabricated
multi-item totals Tier 5's LLM tends to produce on Amazon pages instead
(see the "Not yet root-caused" list below).

**Regression tests**: `ingestion/tests/test_builder_fingerprint.py::TestExtractViaAmazon` (4 tests)

**Real examples**: live-verified on `amazon.com/Vitamins-Wellness-Capsules-Elderberry-Echinacea/dp/B08WKV7LV6`
— went from all-`None` to `name`/`brand`/`rating=4.4`/`rating_count=118` all
correctly populated; a second, independent Amazon URL
(`amazon.com/Advanced-Multivitamin-.../dp/B0H353R1KR`) matched a live
ground-truth diagnostic exactly, confirming the fix generalizes.

---

## 13. `<title>` tag ignored as a `product_name` source — and one live case where the LLM filled it in *wrong*

**Symptom**: `product_name` is either `None` or (more concerning) populated
with the *brand* name instead of the product name, on pages where
`<h1>`/`<title>` clearly have the correct product name but no JSON-LD
`Product.name` exists. Confirmed live: `alevia.com/products/amla-192011`
returned `product_name='Alevia'` (the brand) instead of `'Amla Superfruit
Capsules'` (the real product, sitting in both `<h1>` and `<title>`) —
Tier 5's LLM, working from a zone-pruned view that didn't include the
product name, guessed wrong.

**Root cause**: the `<title>` tag was only ever read by the Tier 4.5
builder-fingerprint's generic fallback (`_extract_product_title`), gated
behind builder-signature or Amazon-domain detection — a plain Shopify
product page matching neither never got a chance at it. Shopify's default
theme renders `<title>` as `"{page_title} – {shop_name}"` (en-dash), which
is a near-universal, free, deterministic product-name signal completely
unused outside that narrow gate.

**Fix**: `_extract_from_json_ld` (Tier 2) now also derives `product_name`
from the `<title>` tag directly: split on the *last* en-dash/pipe separator
to isolate the page's own SEO title from the shop-name suffix, then split
that again on its *first* separator to strip any inner brand mention (e.g.
`"Berberine 800mg | Barton Nutrition – Barton Supplements"` →
`"Berberine 800mg"`). Deliberately **only** en-dash `–` and pipe `|` — never
a plain hyphen `-`, which advertorial/hook titles use as a generic
word-joiner and would otherwise cause a wrong split (e.g.
`"Advertorial - Personal Story - Comparison - ..."`). A 50-character length
bound on the candidate is the second guardrail: real product names in this
corpus have consistently come in well under 50 chars, while long
advertorial-hook headlines that *also* happen to end in `" – ShopName"`
(`mengotomars.com`'s 73-char headline, `jevawell.com`'s 59-char CMS
timestamp label) reliably exceed it. `Product.name` from JSON-LD always
wins when present (setdefault-only).

**Known residual risk, not yet observed as a real failure**: a short
(<50-char) advertorial hook title that also happens to end in a real brand
name (e.g. `"Warning: Don't Buy This – BrandName"`) would still be
misread as a product name. Flagged for a future round to check for, same
as the subscription-detection "Manage Subscription" nav-link risk already
in this doc — not yet confirmed as an actual occurrence.

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestExtractFromJsonLd::test_product_name_recovered_from_title_tag`,
`::test_product_name_from_title_strips_inner_brand_segment`, `::test_product_name_from_title_pipe_only`,
`::test_long_advertorial_title_not_used_as_product_name`, `::test_title_with_no_separator_not_used_as_product_name`,
`::test_json_ld_product_name_wins_over_title_tag`

**Real examples**: live-verified on `alevia.com` (`'Alevia'` → `'Amla
Superfruit Capsules'`), `bartonsupplements.com/products/berberine`
(`None` → `'Berberine 800mg with Milk Thistle'`, exact match to ground
truth), `rhonutrition.com/products/liposomal-collagen-peptides` (exact
match — and Tiers 1-4 alone were now sufficient, no Tier 4.5/5 call
needed at all), `tryorgatics.com/pages/receding-gums` (`None` →
`'RECEDING GUMS'`, a directionally-correct partial match against the
ground truth `'RECEDING GUMS RESET'`).

---

## Known limitation: comparison/listicle pages resolve to the *publisher's* brand, not the featured product's

Not a bug in the strict sense — bugs #10/#11's brand-fallback sources
(`og:site_name`, `Organization.name`, Shopify shop-name) are all
*site-wide* signals, so on a "Top 5 X Supplements" roundup/comparison page,
they correctly-but-unhelpfully resolve to the **publisher's own** brand
(e.g. `healthinsider.news` → `brand_name='Health Insider'`) rather than the
brand of whichever product the article is actually pushing (e.g. `'Bioma'`)
— confirmed on 3 live examples in the Round 3 sample, all `healthinsider.news`
comparison articles. This is the brand-field analog of the already-documented
"Comparison-table price disambiguation" gap above: same page type, same
underlying "which entity is this field actually about" ambiguity. Not fixed
here — resolving it would need distinguishing "this domain is a review/media
publisher, not the seller" from "this domain is a DTC brand's own store",
which none of the current deterministic signals do. Flagged for a future
round; low urgency since the value returned isn't fabricated, just
mis-scoped (true fact, wrong entity).

---

## 14. `enrich_corpus_advertorial_fallback` never attempted the free Shopify JSON Tier 1

**Symptom**: `price` (and `product_name`/`brand_name`/`variants_featured`)
`None` on a Shopify product page with a literal `/products/{handle}` URL —
even though Shopify's own `/products/{handle}.json` endpoint trivially
returns the real data, for free, with no ZenRows/LLM cost. Confirmed live
at real scale: `rituallabs.shop/products/happy-liver13` (220 ads) had
`extraction_method: "tier_5_llm+tier_5_llm+tier_5_llm+tier_5_llm"` —
reprocessed 4 separate times across Rounds 1/2/Phase-0.5h, always via the
expensive LLM path, **never once via Tier 1**.

**Root cause**: a tier-numbering collision across two separate modules.
`zenrows_scraper.py`'s own cascade calls its internal stages "Tiers 1-4"
(XHR capture → JSON-LD → window objects → DOM), which is what
`enrich_corpus_advertorial_fallback`'s docstring means by "Tiers 1-4
couldn't parse". `tiered_scraper.py`'s "Tier 1" is a *completely different*
concept — the Shopify `.json` API — and lives in a disjoint code path
(`scrape_and_extract`) that `enrich_corpus_advertorial_fallback` never
calls. Any URL that entered the "unresolved" bucket after Phase 0.5b/0.5d's
initial tiered-scraper passes, and got reprocessed via
`enrich_corpus_advertorial_fallback` in a later round (which every round
since Phase 0.5e has used), never got a shot at the free, reliable Shopify
JSON endpoint — even on URLs where it would have trivially resolved
everything needed.

**Fix**: `enrich_corpus_advertorial_fallback` gained a free pre-pass: for
every target URL with a literal `/products/{handle}` path
(`shopify_json.has_product_path`), attempt `fetch_shopify_json` via a plain
`httpx.Client` (no ZenRows credit spent) before the paid cascade runs, and
setdefault-merge the result into `existing_page`
(`_merge_product_pages(..., label="shopify_json_backfill")`) — so if Tier 1
alone resolves price, the existing "escalate to LLM whenever price is still
missing" check no longer needlessly escalates, saving the LLM call too.

**Regression tests**: `ingestion/tests/test_enrich_with_product_pages.py::TestEnrichCorpusAdvertorialFallbackShopifyJsonPrePass` (3 tests)

**Real examples**: live-verified — `rituallabs.shop/products/happy-liver13`
and `.../happy-liver` (237 ads combined) both resolve
`price=44.99, product_name='Happy Liver', brand_name='Ritual Labs B2'` via
the free JSON endpoint. Corpus-wide, 265 ads across 10 unique URLs were
found in this exact state (missing price, never Tier-1-attempted) —
`rituallabs.shop`'s 2 URLs account for 237 of them.

---

## 15. Hero zone can be substantively sized yet still lack all product identity — `<title>` never given to the LLM

**Symptom**: `product_name`/`brand_name` both `None` despite a real,
non-tiny hero zone (well above bug #8's 100-char junk-zone threshold) being
fed to Tier 5's LLM. Confirmed live: `track.tryrosabella.com`'s 204-char
hero zone was entirely offer-tier/review text — *"Buy 1 + Get 1 FREE
$19.97 ... 'Excellent' | 134 reviews"* — zero product name or brand
anywhere in it, on all 7 unique URLs (251 ads) on this domain.

**Root cause**: bug #8's length guard only protects against zones too
*small* to contain real content — it has no way to detect a zone that's
long enough but semantically incomplete (pure pricing/review boilerplate,
no identity text). Meanwhile the page's own `<title>` tag held the answer
cleanly (`"Rosabella Beetroot"`) but was never given to the LLM at
all — Tier 5 only ever saw the zone-pruned markdown, never the title.

**Fix, deliberately not a content-quality heuristic**: rather than try to
detect "does this zone contain identity text" (fragile, easy to both
over- and under-trigger), `prune_to_markdown` now unconditionally prepends
the page's `<title>` tag (capped at 200 chars) as a labeled `"Page title:
..."` line before whatever markdown — hero-zone or full-body — follows.
This is deliberately **not** wired as a new deterministic `product_name`
source (that's bug #13's separate, conservative title-tag heuristic, gated
on a `"ProductName – ShopName"` separator specifically to avoid misreading
advertorial hook headlines as product names) — titles with no separator at
all, like `"Rosabella Beetroot"`, are exactly the ambiguous case that
heuristic can't safely resolve on its own. Handing the raw title to the
LLM as auxiliary context lets its judgment — better at telling apart
`"Rosabella Beetroot"` (a real product name) from `"Top 5 Magnesium
Supplements"` (a listicle headline) than any regex — decide, rather than
forcing a brittle rule to decide for it.

**Regression tests**: `ingestion/tests/test_zone_pruner.py::TestPruneToMarkdown::test_title_tag_prepended_when_hero_zone_lacks_product_identity`,
`::test_no_title_tag_produces_no_prefix`

**Real examples**: live-verified — both sampled `track.tryrosabella.com`
URLs now resolve `product_name='Rosabella Beetroot'` and
`brand_name='Rosabella'` (previously both `None`), no code changes needed
beyond this one prepend. Fixes the same 251-ad/7-URL domain as the
Shopify JSON pre-pass above targets for its sibling `rituallabs.shop`
domain — between bugs #14 and #15, the two single highest-duplication
unresolved URLs in the whole corpus (488 ads combined, ~18% of the
2,736-ad corpus) are now resolved.

---

## Rating gap investigation (65% missing corpus-wide) — confirms existing limitation, no new fix found

Live-sampled 5 of the top rating-missing domains (`alevia.com`,
`trygoodgrove.com`, `primalviking.com`, `kittysupps.com`,
`tradesmannutrition.com`). 3/5 already resolve `rating` correctly under
current code (`4.8`, `4.7`/`1423`, `4.8`/`2`) — these are a **reprocessing
gap**, not a bug: the corpus just hasn't been re-run against already-shipped
fixes. The other 2/5 confirm the limitation already documented in
`_extract_review_widgets`'s own docstring, not a new bug:
- `tradesmannutrition.com`: an `okendo_widget` block **is** present in the
  DOM, but carries zero `data-oke-reviews-*` attributes — the actual rating
  is fetched by the widget's own client-side JS call, which hadn't resolved
  within the 2000ms render wait at capture time.
- `kittysupps.com`: no yotpo/loox/okendo/jdgm-classed elements anywhere in
  the DOM at all, despite the app's script tag being present — same
  async-timing story, further along (hasn't even mounted its container yet).

No selector gap, no quick code fix — closing this further would mean either
a longer ZenRows wait (cost/latency tradeoff) or `wait_for`-ing a specific
widget selector before capture, both real infrastructure changes with their
own ROI case, not something to build speculatively off a 5-URL sample.

**Follow-up — `wait_for` retry built, opt-in, but live test inconclusive**:
confirmed via ZenRows' own docs (Context7, 2026-08-16) that `wait_for`
(pause JS rendering until a CSS selector appears, up to 3 min) is real and
more reliable than a blind fixed delay — but critically, **the whole
request fails with a 422 if the selector never matches**, so it can never
be applied to every fetch (most pages have no review widget at all).
Implemented as a conditional retry in `fetch_product_zenrows`
(`enable_review_widget_retry`, opt-in, default `False` — zero behavior/cost
change for any existing caller): only attempted when the first fetch found
a review-app script signature (yotpo/loox/judge.me/jdgm/okendo) but no
rendered rating data, and the retry attempt itself is wrapped so any
failure (including a 422) falls back to the original result rather than
failing the fetch. 5 regression tests, 328/328 total pass, zero new lint
debt.

**Live-tested against `kittysupps.com` and `tradesmannutrition.com`
(the same 2 domains that motivated this) — inconclusive, not a confirmed
win**: both retries completed without error (200 status) but neither
recovered rating data, and each took 45-90+ seconds (confirming the
latency cost is real, not theoretical). This suggests these two specific
products may genuinely have zero rendered reviews yet, rather than the
"just needed more time" hypothesis this fix was built to test — or the
combined selector doesn't match this particular widget variant. The code
is shipped and safe (opt-in, no regression risk, tested), but its real
payoff is unproven — needs a broader live sample against pages with
stronger evidence of "rating exists but hasn't rendered" before deciding
whether it's worth enabling at corpus scale (each retry-eligible URL pays
up to ~2x the latency, and possibly 2x ZenRows credits, of a normal fetch).

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestReviewWidgetWaitForRetry` (5 tests)

---

## 16. Corpus reprocessing let an explicit LLM `0` silently overwrite a real prior value

**Symptom**: `rating_count` (and, latently, `price`/`rating`) wiped from a
real prior value to `0` after a reprocessing run — not a missing-data
problem, a **regression**: `eternapure.com`'s `rating_count` went from
`171` to `0`.

**Root cause**: `to_product_page_updates` (`zenrows_scraper.py`) is
deliberately freshness-oriented for `price`/`rating`/`rating_count` — a
later tier's value is allowed to replace an earlier one, unlike the sticky
`product_name`/`brand_name` fields — but used `is not None` as the gate,
which treats an LLM's explicit `0` (meaning "I found nothing in my
zone-pruned view this time") the same as a genuinely re-confirmed value.
Caught during this session's corpus reprocessing verification, before
merging — not live-tested in advance.

**Fix**: changed the gate to a plain truthy check (`if data.rating_count:`
etc.) for all three fields — a real product's price/rating/review-count
essentially never being genuinely `0` makes this a safe, low-risk
narrowing, and freshness is still preserved for any real (non-zero) value.

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestToProductPageUpdates::test_explicit_zero_rating_count_does_not_overwrite_real_value`,
`::test_real_nonzero_rating_count_still_updates`

**Real examples**: confirmed via a corpus-wide before/after diff during
this session's reprocessing verification — isolated to 1/2,736 ads
(`eternapure.com/apps/pagefly?id=86f4b49f-...`), patched directly in the
completed output before merging (reconstructible from the pre-reprocessing
backup, no need to re-run the batch).

---

## 17. Amazon served region-specific currency, causing wrong-magnitude price hallucinations — plus a dedicated price extractor

**Symptom**: `price` `None` on Amazon pages, and worse — before this fix,
Tier 5's LLM occasionally produced wildly-wrong-magnitude "prices" (e.g.
`$1714.42` for a supplement) that the hallucination guardrail correctly
rejected. Both are the same root cause, not two separate bugs.

**Root cause**: Amazon serves region-specific pricing/currency based on
the requesting IP's geolocation. No fetch anywhere in this pipeline set
`premium_proxy`/`proxy_country`, so every request was subject to whichever
country ZenRows' standard proxy pool happened to assign. Confirmed live:
the *exact same* Amazon URL returned `INR1,716.07` on one fetch and clean
USD on another — the LLM, working from a raw-number view with an
ambiguous or missing currency symbol, read the Rupee amount and mislabeled
it as a dollar price (`1716.07` → the `$1714.42` hallucination this
project had already seen and correctly rejected, but never root-caused
until now).

**Fix, three parts — redesigned mid-implementation from a blanket
`proxy_country=us` to a marketplace-aware one** after a user review caught
that Amazon operates a genuinely separate marketplace per country (`.com`,
`.co.uk`, `.de`, `.in`, ...), each with its own real price and currency —
forcing every Amazon URL to a US proxy would have been *wrong*, not just
imprecise, for a genuine non-`.com` URL (and could itself cause Amazon to
serve mismatched or blocked content):
1. New `builder_fingerprint.py::get_amazon_region(url) -> (proxy_country,
   currency) | None` — maps the URL's own domain TLD (`_AMAZON_TLD_TO_REGION`,
   20 marketplaces) to its real region and currency. Deliberately returns
   `None` for an unrecognized Amazon TLD rather than guessing — a caller
   should skip forcing a region entirely, not default to one.
2. `fetch_product_zenrows` uses `get_amazon_region(url)` to set
   `premium_proxy=true, proxy_country=<detected region>` — `amazon.com`
   still resolves to `us` (matching every URL actually observed in this
   corpus so far), but a genuine `amazon.co.uk` URL now correctly routes to
   a `gb` proxy instead of being forced to `us`. Scoped to Amazon only —
   premium/geo-targeted proxies cost more, and this is the only site this
   corpus has confirmed the currency-mismatch bug on.
3. The Tier 4.5 Amazon extractor (bug #12) pulls price deterministically
   from `#corePrice_feature_div`/`#apex_desktop`'s first
   `.a-price .a-offscreen` match — scoped to those specific buy-box
   containers rather than the generic `.a-price .a-offscreen` selector
   alone, which matches *every* price mention on the page (strikethrough
   MSRP, per-unit breakdowns) and risked picking a wrong value (confirmed
   live: one page's unscoped selector returned 5 matches including a
   stray `$0.30`) — and tags the extracted price with the *real*
   marketplace currency (`get_amazon_region`'s second element), not a
   hardcoded `"USD"`. `ZenRowsProductData`/`ProductPage` both gained
   `marketplace_region`/`price_currency` propagation (the latter field
   already existed but was never actually set by any tier in this
   pipeline — it silently sat at its Pydantic `"USD"` default forever).

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestFetchProductZenrows::test_amazon_com_url_forces_us_proxy_country`,
`::test_amazon_co_uk_url_forces_gb_proxy_country_not_us`, `::test_unrecognized_amazon_tld_does_not_force_any_proxy_country`,
`::test_non_amazon_url_does_not_set_proxy_country`, `TestToProductPageUpdates::test_marketplace_region_and_currency_propagate_to_product_page`,
`::test_no_region_leaves_product_page_currency_at_default`,
`ingestion/tests/test_builder_fingerprint.py::TestExtractViaAmazon::test_amazon_price_extracted_from_core_price_container`,
`::test_amazon_no_price_container_leaves_offer_matrix_empty`, `::test_amazon_price_uses_marketplace_currency_not_hardcoded_usd`,
`TestGetAmazonRegion` (6 tests)

**Real examples**: live-verified — all 3 previously price-missing Amazon
URLs sampled now resolve clean, plausible USD prices (`$17.95`, `$11.90`,
`$19.95`) alongside name/brand/rating/region/currency, confirmed via the
real production fetch path (`fetch_product_zenrows`), not a synthetic
test. Every Amazon `link_url` observed in this corpus so far is
`amazon.com` — the region-awareness is a correctness fix for URLs not yet
seen, not something this corpus's current data required.

---

## `collation_count` degeneracy — investigated, no code bug found

Separate, lower-priority open item from the plan's "Next steps" (not part
of the landing-page extraction pipeline this doc otherwise covers — this
is Meta Ad Library data via the `curious_coder/facebook-ads-library-scraper`
Apify actor, `ingestion/apify_client.py`). `collation_count` is a genuine,
documented output field of that actor (confirmed via its Apify Store
listing) — not a wrong/fabricated key, and `ingestion/normalize.py:105`'s
`raw.get("collation_count") or 0` passthrough is a correct, direct read of
what the actor returns. The actor's own input options
(`ingestion/apify_client.py::run_ad_scrape`'s `input_dict`) have no
apparent "enable full collation grouping" toggle, and the actor's docs
don't clarify whether its `collation_count` faithfully mirrors Meta's own
internal grouping or is itself an approximation specific to search-results-
based scraping (`scrapePageAds`) vs. full ad-detail mode. Verifying further
would require either live-comparing against Meta's own Ad Library UI for a
specific known ad, or the actor author's clarification — neither available
here. Most plausible explanation, unconfirmed: this corpus's low variance
(median 1.0, only 16% >1) reflects a genuine property of the niche —
smaller/mid supplement DTC brands generally don't run large collated
variant-testing campaigns the way mega-brand advertisers do — rather than
a scraper misconfiguration. **No code change made.**

---

## 18. `price` had no sanity bounds anywhere in the pipeline — found via Phase 1 data exploration, not live sampling

**Symptom**: corpus-wide `price` mean of $123,590.21 against a true median
of $44.99, with a max of $235,235,112.00 — first surfaced not by live
URL-sampling (this doc's usual methodology) but by running Phase 1's own
data-exploration statistics over the corpus and noticing the mean/stdev
were nonsensical.

**Root cause**: no price-setting path in the ZenRows cascade validates the
*plausibility* of the resulting number, only whether it's a well-formed
float. The clearest confirmed mechanism: `_extract_from_window_objects`'s
own "Shopify commonly stores price in cents" heuristic
(`parsed / 100 if parsed > 1000 else parsed`) — already flagged in its own
code comment as "not fully certain" — has no upper bound on the *result*,
so a large non-price integer (a variant/product id landing in a
generically-named `"price"` key inside a framework-agnostic
`window.__INITIAL_STATE__` blob, not Shopify-specific) sails through
undetected. On the low end, a related pattern: an indirect "$0.67/day"
subscription rate or a stray Amazon subscribe-and-save fragment
($0.20-$0.30) being stored as if it were the full price.

**Fix**: `to_product_page_updates` — the true universal bottleneck every
ZenRows-cascade tier (1 through 5) funnels `product_price` through before
it reaches `ProductPage` — now rejects any value outside `[$2, $1,500]`
rather than chasing the exact originating tier for every possible bad
value. Corpus cleaned retroactively: 97 ads (67 high-end, 30 low-end) had
`price` nulled back out after confirming the fix, zero regressions on any
other field.

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestToProductPageUpdates::test_implausibly_large_price_rejected`,
`::test_plausible_bundle_price_still_accepted`, `::test_implausible_price_does_not_overwrite_existing_valid_price`,
`::test_implausibly_small_price_rejected`

**Real examples**: `im8health.com/pages/menopause` ($235,235,112.00),
`nivara-shop.com/products/ferravital-ferritin-support` ($499,949.99, ×8
ads sharing the URL), plus 5 more confirmed cases in the $5,000-$10,000
range and 30 cases under $2. Post-cleanup corpus price distribution: mean
$47.14 (close to the true median $44.99), stdev $41.55, max $599 —
plausible for a real "build your own bundle" mega-order.

---

## 19. `rating` had no scale-bound check — editorial "9.8/10" scores misread as 5-star ratings

**Symptom**: corpus-wide `rating` mean of 5.04 on a field documented as a
0-5 star scale — mathematically impossible if every value were genuinely
≤5. Also first surfaced via Phase 1 exploration statistics, not live
sampling.

**Root cause**: review/comparison-listicle sites ("Top 5 X Supplements")
commonly print an editorial score out of 10 (e.g. "Editor's Score: 9.8/10")
alongside or instead of a customer 5-star rating. Neither
`llm_fallback.py`'s prompt nor its `validate_against_raw_text` guardrail
distinguished the two — the guardrail only checks whether a number is
hallucinated (does it appear verbatim on the page), and a genuine "9.8"
printed on the page passes that check easily despite being the wrong kind
of number for this field. The regex-based extractor
(`builder_fingerprint.py::_extract_social_proof`) had the same gap: its
pattern requires "stars"/"out of 5"/"/5" wording but never itself verifies
the matched number is actually ≤5 — a malformed "9.8 out of 5" would pass.
47 corpus ads had `rating_value > 5.0`, heavily clustered at exactly `9.8`
across many distinct "Top 5" review-site domains (`top5remedies.com`,
`herwellnessdaily.com`, `nutrihealthforum.com`, etc.) — plus one outlier at
`89.0` (`jjsmithonline.com`, a likely unrelated mis-parse).

**Fix, three parts**: (1) `llm_fallback.py`'s system prompt now explicitly
states `rating_value` must be a 0-5 customer star rating, not an
out-of-10 editorial score, and to leave it null rather than convert one.
(2) `validate_against_raw_text` gained an explicit `0 <= rating_value <= 5`
range check, applied *before* the existing hallucination check (a
genuinely-printed-but-wrong-scale number needs its own rejection reason,
distinct from "not on the page at all"). (3) `builder_fingerprint.py`'s
regex-based `_extract_social_proof` gained the same range check as a
second line of defense. `to_product_page_updates` also gained a final
`0 < rating <= 5` backstop (paired with the price fix above, same
universal-bottleneck reasoning) since JSON-LD/window-objects tiers route
`ratingValue` through `clean_price` with no range check of their own.

**Regression tests**: `ingestion/tests/test_llm_fallback.py::TestValidateAgainstRawText::test_editorial_out_of_10_score_rejected_as_rating`,
`::test_valid_star_rating_still_accepted`,
`ingestion/tests/test_builder_fingerprint.py::TestExtractViaAmazon::test_malformed_out_of_range_rating_match_rejected`,
`ingestion/tests/test_zenrows_scraper.py::TestToProductPageUpdates::test_out_of_range_rating_rejected`, `::test_valid_rating_still_accepted`

**Real examples**: corpus cleaned retroactively — 49 ads had `rating`
nulled after confirming the fix. Post-cleanup rating distribution: mean
4.72, max 5.0.

---

## 20. `variants_featured` structurally discarded by Tier 4.5/5's own merge function

**Symptom**: `variants_featured` sits at 28.7% corpus-wide (2,736 ads) — far
below `price` (70.0%), `product_name` (75.0%), `brand_name` (61.5%). Unlike
those three, this wasn't a diffuse long tail of per-page misses: every
`extraction_method` whose *last* tier was `tier_5_llm` or bare `zenrows`
(i.e. never touched by a Tier 1-4 structured-data source) showed **~100%**
missing `variants_featured`, while methods ending in `structured_data`/
`shopify_json` showed 0-36% missing (driven by genuinely-single-SKU
products). A near-total split by extraction path, not a scattered failure
rate, is the signature of a structural bug rather than a page-content gap.

**Root cause**: Both Tier 4.5 (`ingestion/builder_fingerprint.py`'s
`_extract_offer_matrix`) and Tier 5 (`ingestion/llm_fallback.py`'s LLM
prompt) already extract quantity/bundle tier labels ("1 Bottle", "3
Bottles", ...) into `DirectResponseProductData.offer_matrix` — real data,
already being computed on every extraction. But
`ingestion/zenrows_scraper.py::_merge_direct_response_into` only ever called
`best_offer(data.offer_matrix)` to pull out **one tier's price**, and never
read `tier_label` from any of them — every tier label was silently
discarded before it could reach `variants_featured`, regardless of how many
distinct offers the page had.

**Fix**: `_merge_direct_response_into` now also collects every
non-empty `tier_label` across `offer_matrix` and `setdefault`s them onto
`merged["variants"]` — same additive, never-overwrite convention as every
other field in the cascade. A quantity-bundle ladder ("1 bottle"/"3
bottles"/"6 bottles") is treated as a real form of `variants_featured` here,
matching the ML target variable `variants_featured_count`'s intent (variant
*complexity*, not just flavor/size SKUs).

**Bonus bug found by the regression test**: `builder_fingerprint.py`'s
`_QTY_RE` listed singular forms before plural ("bottle" before "bottles")
in its regex alternation — Python's `re` matches the first alternative that
succeeds, not the longest, so "3 Bottles" matched as "3 Bottle" (silently
truncated). Fixed by reordering every pair to plural-first. Purely
cosmetic-looking until you realize `tier_label` is now used verbatim as a
`variants_featured` entry.

**Regression tests**: `ingestion/tests/test_zenrows_scraper.py::TestExtendedCascadeTier45And5::test_builder_fingerprint_fills_when_tiers_1_4_find_nothing`,
`::test_llm_fallback_runs_when_opted_in_and_nothing_else_resolved`,
`::test_multi_tier_offer_matrix_maps_every_tier_label_to_variants`.

**Real examples (live-verified, not yet reprocessed into the corpus)**:
`unwd.com/shop/mindhack/` now recovers `variants: ['Mind Hack® Starter Kit',
'Mind Hack® Refill']` via `tier_5_llm` — previously discarded entirely
despite being extracted. Two unrelated price anomalies observed on the same
live check ($0.0 on `unwd.com`, $5,595 on `rhonutrition.com/pages/build-your-bundle`)
are pre-existing `best_offer()` price-selection behavior, not caused by this
fix, and are already filtered out downstream by bug #18's `[$2, $1,500]`
sanity bounds — not investigated further here, out of this ticket's scope.

**Not yet done**: corpus reprocessing. Unlike the price-context/guardrail
fixes, this recovers data that was never persisted anywhere (`offer_matrix`
only ever lived in-memory during extraction) — recovering it corpus-wide
needs a fresh paid ZenRows/LLM pass over every ad whose `extraction_method`
ended in `tier_4_5_builder`/`tier_5_llm`/`zenrows` (a large population, per
the near-100% miss rate above). Scope and cost are a decision for whoever
runs the next reprocessing pass, same pattern as every prior phase's
reprocessing checkpoint.

---

## Not yet root-caused (long tail, below the fix-worthiness bar so far)

Per the wayfinder map's ROI rule (skip a fix if it only rescues ≤5 diagnosed
samples), these were noted but not investigated to a fixable root cause —
flagged here so a future round doesn't have to rediscover them:

- **Indirect "$X/day" or "$X/month" cost framing on *non-subscription* pages**
  (**partially resolved** — see "New capability" above): where the rate
  corresponds to a real subscription (`nano-revive.com`), it's now handled
  by `subscription_price` with day→month conversion. What's still unresolved
  is the case where the same framing appears with **zero subscription
  signal** at all — `healthinsider.news` (x3), `news.tophealthinsider.com`,
  and `primus-health.com` (no app signature found, only ambiguous keyword
  text) are pure rhetorical/marketing framing for what's likely a one-time
  or bundle price, not a recurring charge. No stated way to convert
  "less than \$1 a day" into a real one-time price without more page context
  (e.g. finding the actual bundle total elsewhere) — still needs a fix
  candidate, not yet found.
- **Hidden-input JSON bundle config** (`rituallabs.shop`, both
  `happy-liver`/`happy-liver13`): price data is embedded as JSON in a hidden
  `<input>` value (a "KaChing Bundles" Shopify app config), not present as
  visible text at all. Would need a dedicated JSON-in-attribute parser — a
  new tier, not a quick fix. Confirmed recurring across both rounds (same 2
  URLs each time, same domain) — still just 1 domain.
- **Amazon marketplace listings — name/brand resolved (bug #9), price still
  a residual gap**: the checkout-flow-widget false positive blocking
  name/brand entirely is now fixed (see bug #9 above). Price is a separate,
  still-open problem: live-verified on `amazon.com/Purity-Products-...`, the
  LLM found multiple dollar amounts on the page but none were the real
  per-unit price — the guardrail correctly rejected fabricated multi-item
  totals ($1714.42, $3085.96) rather than trust them, but no correct price
  was recovered either. **Resolved (Round 3): not worth further investment.**
  Checked corpus-wide Amazon-domain prevalence directly: only 19 ads / 12
  unique URLs (0.7% of the 2,736-ad corpus), all plain `amazon.com` — too
  small a population to justify building past the dedicated extractor
  already shipped (bug #12).
- **"Best Sellers" / "Customers Also Bought" cross-sell price sections**
  (`betterwild.com/pages/lf`, `motherearthlabs.com`, `promixnutrition.com`
  x2 — recurred again in Round 3 on `promixnutrition.com/pages/build-your-bundle`,
  now 4 cumulative samples, still below the bar): a hero zone *was* found,
  but the real price sits in a cross-sell/recommendation grid or an
  ambiguous multi-tier bundle-builder ladder rather than one clear primary
  offer — not yet root-caused to a specific selector gap.
- **PageFly builder-extraction failures** (Round 3, 2 samples:
  `renoura.store/pages/make-your-horse-invisible-to-flies-with-plant-oils`,
  `usagain.store/pages/7-reasons`): `detect_builder()` correctly identifies
  PageFly via its container-class fingerprint, but `_extract_offer_matrix()`
  (`ingestion/builder_fingerprint.py`) then finds zero offer cards — its
  candidate selectors (`[class*='offer']`, `[class*='tier']`,
  `[class*='bundle']`, `[class*='pricing']`, `[class*='plan']`,
  `[class*='package']`) all key off semantic class-name substrings, which a
  page-builder's auto-generated markup (e.g. `pf-c-xxxx`) may simply never
  use even when a real quantity+price offer card is present. Both URLs
  confirmed still price-missing after the full cascade including Tier 5 LLM
  fallback. Below the ROI bar this round (2 samples) — worth revisiting if
  a future round adds 3+ more.
- **Suspected JS-render-timing/race price gap** (Round 3, 3 samples:
  `goldsealsupplements.com/pages/magnesium`,
  `goldsealsupplements.com/pages/magnesium-cement`,
  `tryuvola.com/pages/liver-detox`): raw HTML contains a real-looking price
  (e.g. "$28.80", "$30") alongside repeated `Usd0`/`$0.00` placeholder-style
  tokens, suggesting a client-side pricing widget that ZenRows' fixed `wait`
  captured mid-render (before a JS calculation replaced a $0 stub with the
  real price) — the same failure class the `rating` `wait_for` retry was
  built for, applied here to price instead of rating. Not verified past this
  raw-HTML pattern match (no live `wait_for` test run); below the ROI bar
  (3 samples).
- **Bundle-builder pages with a genuinely `$0.00` base SKU**
  (`carlsonlabs.com/products/bundle` — confirmed directly via its own
  Shopify `.json` endpoint: `"price":"0.00"` on the underlying variant
  itself): not an extraction bug at all — the real, current selling price
  only exists after a customer picks individual items client-side, so
  there's no single static "the price" for the pipeline to recover. Same
  category as the already-documented hidden-input JSON bundle-config case
  above; likely a structural, not fixable-by-extraction, gap for
  bundle-builder product pages generally.
- **Collection/listing and comparison-listicle pages misclassified as
  single-product pages**: several Round 3 "visible but missed" candidates
  (`kaged.com/collections/all-products`, `reviews.jaclynsavage.com`,
  `wellwellwellness.co`) turned out to be pages with *many* distinct prices
  for different products, not one product with a missed price — the
  existing "comparison-table" and multi-SKU-listing limitations already
  cover this; not a new gap, just recurring confirmation of it.
- **Noisy full-body-fallback content burying the real price**
  (`mortaine.co/pages/5-reasons-grow-spark-f`): even after the Fix 7
  truncation-budget increase, this page's fallback markdown (23K chars, well
  under the new cap) is dominated by cart-drawer/shipping-protection
  boilerplate text ("Your cart is empty... Shipping Protection $1.99...")
  that seems to crowd out whatever the real offer price is. Not yet
  root-caused past confirming truncation isn't the (remaining) cause.
- **`display:none` product-form** (`getmorningwould.shop`): hero zone
  matched a hidden, likely variant-specific form; the actual visible price
  probably lives elsewhere on the page. One-off in this sample.
- Several single-occurrence cases not individually investigated:
  `astaxanthin2.vitalydaily.org`, `healthinsider.news` (urogynecology
  page) — each ≤5 samples, below the ROI bar on their own.
