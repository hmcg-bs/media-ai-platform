# Step 1 Ingestion & Step 1.5 Landing-Page Analysis

**Status**: ✅ Built (Step 1 core + Step 1.5 tiered cascade)
**Version**: 3.0
**Date**: 2026-08-26 (supersedes the 2026-08-20 v2 doc — v2 documented the landing-page
tiered cascade in isolation; v3 additionally covers Step 1's raw-ingestion path, the
`utm_features.py` campaign-taxonomy layer, the full schema graph, and the Step 2 merge
bridge, none of which had a settled doc before this rewrite)

## Overview

`ingestion/` implements two adjacent pieces of the pipeline:

1. **Step 1 — raw ingestion**: scrape competitor ads from the Meta Ad Library (via an
   Apify actor), normalize them into a `CompetitorAd` corpus, and best-effort download
   creative images.
2. **Step 1.5 — landing-page enrichment** (informally "Stage 4"): for every ad's
   `link_url`, scrape the linked landing page and extract structured product data
   (name, brand, price, rating, variants, subscription pricing) into a `ProductPage`
   attached to that ad. This validates that an Ad-Library keyword match ("creatine")
   actually landed on a relevant product, and gives Step 3 (Pattern Discovery) real
   product features to correlate against performance.

Both are **local CLI batch jobs** run over `data/*.json` corpus files — there is no
serverless deployment, no BigQuery, and no scheduling yet (see ADR-005's v1 scope
deviations). Step 1.5 is optional post-hoc enrichment: ads flow through Steps 1–3 with
`product_page=None` if it's skipped.

**What changed since v2**: v2 described the landing-page tiered cascade only. v1 (before
that) described a flat 4-stage pipeline (HTML scrape → structured data → LLM enrichment
→ integration) that hit a 66% failure rate at corpus scale — Shopify's shared
platform-level anti-abuse system blocking bursty concurrent requests — and had no answer
for pages with zero Schema.org/OpenGraph markup (a large fraction of real "advertorial"
direct-response funnel pages). v2's tiered cascade fixed both. This v3 doc widens the
lens to the whole `ingestion/` package: Step 1's own scrape/normalize/download path, the
UTM/campaign-taxonomy feature layer, the full schema graph (`CompetitorAd` /
`ProductPage` / `ZenRowsProductData` / `DirectResponseProductData`), and the bridge into
Step 2's creative-analysis output — none of which had a settled architecture doc before.

---

## Full Data Flow

```
Meta Ad Library (via curious_coder/facebook-ads-library-scraper Apify actor)
        │
        ▼
ingestion/apify_client.py::run_ad_scrape()        — raw dataset items (list[dict])
        │
        ▼
ingestion/normalize.py::normalize_ad()            — defensive dict.get()-based mapping
        │                                            into ingestion/models.py::CompetitorAd
        ▼
ingestion/download.py::download_creatives()       — best-effort primary-image download
        │                                            (never drops an ad on failure)
        ▼
ads.json  (CompetitorAd[], product_page=None)
        │
        ▼  ── Step 1.5, optional, one of several CLI passes ──────────────────
ingestion/enrich_with_product_pages.py            — corpus-level orchestration
        │
        ├─ Tier 1   ingestion/shopify_json.py          (free, deterministic)
        ├─ Tier 2   ingestion/landing_page_scraper.py  (free, rate-limited HTML scrape)
        ├─ Tier 3/4 ingestion/zenrows_scraper.py        (paid, JS-rendered, managed)
        ├─ Tier 4.5 ingestion/builder_fingerprint.py    (free — reuses Tier 3/4's fetch)
        └─ Tier 5   ingestion/zone_pruner.py +
                    ingestion/llm_fallback.py            (paid, real LLM call)
        │
        │   (also, independent of tier: ingestion/subscription_detector.py,
        │    always attempted when HTML is available; ingestion/utm_features.py,
        │    pure URL parsing, no fetch needed)
        ▼
ads_enriched.json  (CompetitorAd[], product_page populated where resolved)
        │
        ▼  ── Step 2 bridge, separate corpus, merged back in ───────────────
ingestion/run_step2_pipeline.py                    — runs pipeline/'s creative-analysis
        │                                            stage chain over each ad's image
        ▼
out/step2/<ad_id>.json  (pipeline/models/output_schema.py::ExtractionResult)
        │
        ▼
ingestion/merge_step2_features.py::merge_step2_into_corpus()
        │                                            — attaches creative_features
        ▼
final corpus (data/supplements_enriched_with_creative.json)
        │
        ▼
Step 3 Pattern Discovery (product_page.* + creative_features.* + days_active/collation_count)
```

`ingestion/dedupe.py` (URL canonicalization + content-hash + MinHash near-duplicate
detection) and `ingestion/rate_limiter.py` (token-bucket pacing) are cross-cutting
utilities the Step 1.5 tiers above call into, not stages of their own.

---

## Step 1: Raw Ingestion

### `ingestion/apify_client.py`

Thin, mockable wrapper over the `apify-client` SDK for the
`curious_coder/facebook-ads-library-scraper` Apify actor. `run_ad_scrape(search_query,
count, country)` constructs a Meta Ad Library search URL, calls the actor
(`scrapeAdDetails=True`), and returns the raw dataset items. Retries transient failures
(429/5xx/timeout) via `tenacity`. Exposes a module-level `run_fn` injection seam so every
downstream script (`ingest.py`, `refresh_image_urls.py`, `fresh_corpus_scrape.py`) can be
tested offline without a real Apify call.

### `ingestion/normalize.py`

`normalize_ad(raw: dict) -> CompetitorAd` — every field access is `dict.get()` with a
safe default, because the Ad Library snapshot's field names vary and change. Notable
defensive/corrective logic (each backed by a live-confirmed bug, not speculation):

- `_video_urls` / `_video_preview_image_urls` also read from a `"cards"` group, not just
  a top-level `"videos"` key — a video-carousel ad's video lives under `cards` and was
  previously silently dropped.
- `snapshot_url` is built from the ad's own `ad_archive_id` (`facebook.com/ads/library/
  ?id={id}`) rather than trusting the raw item's `url` field, which is confirmed to just
  echo back the actor's own generic search-input URL (identical for every ad in a run,
  useless as a per-ad link).
- `impressions_index`'s `-1` sentinel (Meta's "not disclosed" marker) is normalized to
  `None`.
- `reach_estimate`/`spend` are coerced to `str | None` via a `field_validator` on
  `CompetitorAd` — their real shape has never been observed populated in live testing, so
  the model degrades gracefully instead of raising the first time a real value appears.

### `ingestion/download.py`

`download_creatives(ads, media_dir)` — best-effort download of each ad's first
`image_urls` entry to `media/<ad_archive_id>.<ext>`. Never drops an ad on a failed
download; only sets `local_image_path` on success.

### `ingestion/ingest.py`

The Step 1 CLI entry point: scrape → normalize → download → write `ads.json` +
`query.json` (provenance, with the Apify token redacted). `--count` has a hard minimum of
10 (an Apify actor requirement).

```bash
uv run python -m ingestion.ingest --query "creatine" --count 100 --out data/creatine_run
```

### `ingestion/fresh_corpus_scrape.py`

A multi-query variant of `ingest.py` for building a fuller, less keyword-biased corpus:
runs one Apify call per query across 12 supplement-subcategory search terms (a single
"supplements" query over-represents whichever advertisers rank highest for that one
term), dedupes by `ad_archive_id` across all queries, and drops any ad with no
`image_urls` up front (a hard requirement for Step 2). Writes to a new file rather than
overwriting the existing corpus, so the new corpus can be verified before promotion.

### `ingestion/refresh_image_urls.py`

Facebook's CDN `image_urls` are signed and time-limited — every URL captured at original
ingestion time has since expired, corpus-wide (confirmed by decoding a sampled URL's own
embedded expiry). There is no supported per-ad re-fetch (confirmed via an isolated A/B
test: the actor returns zero items for a direct `ads/library/?id=` URL even against a
live-reconfirmed ad). This instead re-runs the same keyword search, matches fresh results
back to the corpus by `ad_archive_id`, and overwrites `image_urls` wherever a fresh match
resurfaces. Ads not resurfaced keep their stale (unusable) URLs and are skipped later at
the Step 2 fetch stage, same as any other fetch failure — never dropped from the corpus.

---

## Step 1.5: The Landing-Page Tiered Cascade

Each unique landing-page URL passes through tiers in order; a tier only runs if the ones
before it left fields empty. At most 1–2 HTTP requests per URL for the fast path (Tier
1/2); ZenRows (Tier 3+) is a separate, paid, opt-in pass over URLs the fast path didn't
fully resolve.

```
                 ┌─────────────────────────────────────────────┐
                 │ Tier 1 — Shopify .json API                   │  free, 1 request
                 │ ingestion/shopify_json.py                    │  87% success on
                 │ {url}/products/{handle}.json → full product  │  Shopify URLs
                 └─────────────────────┬─────────────────────────┘
                                        │ 404 / not Shopify
                 ┌─────────────────────▼─────────────────────────┐
                 │ Tier 2 — Hardened HTML scrape                  │  free, rate-limited
                 │ ingestion/landing_page_scraper.py              │  JSON-LD + OpenGraph
                 │ + ingestion/rate_limiter.py                    │  (the original v1
                 │ + ingestion/tiered_scraper.py (composition)     │  4a/4b, hardened)
                 └─────────────────────┬─────────────────────────┘
                                        │ still missing fields (esp. price)
                 ┌─────────────────────▼─────────────────────────┐
                 │ Tier 3/4 — ZenRows managed scraping             │  paid, JS-rendered
                 │ ingestion/zenrows_scraper.py                    │  4-tier cascade:
                 │ (XHR → JSON-LD → window objects → DOM/widgets)  │  XHR/JSON-LD/window
                 └─────────────────────┬─────────────────────────┘  objects/DOM fallback
                                        │ zero Schema.org markup at all
                 ┌─────────────────────▼─────────────────────────┐
                 │ Tier 4.5 — Builder fingerprint                  │  paid (already fetched),
                 │ ingestion/builder_fingerprint.py                │  free extraction
                 │ PageFly / Zipify / GemPages / ReConvert /        │
                 │ Amazon (own dedicated path)                      │
                 └─────────────────────┬─────────────────────────┘
                                        │ still nothing
                 ┌─────────────────────▼─────────────────────────┐
                 │ Tier 5 — Zone-pruned LLM fallback                │  paid, real LLM call
                 │ ingestion/zone_pruner.py + ingestion/llm_fallback.py │
                 │ hero/social-proof/specs zones → Markdown →       │
                 │ Gemini (via Replicate) → hallucination guardrail │
                 └─────────────────────────────────────────────────┘
```

Semantic enrichment (`product_category`, `subcategory`, USP, cultural branding —
`product_page_analyzer.py`, Stage 4c's original scope), subscription-commerce detection
(`subscription_detector.py`), and UTM/campaign-taxonomy features (`utm_features.py`)
layer on top of whichever tier resolved the base fields; see their own sections below.

Every tier merges into the same `ProductPage`, **never replacing data an earlier tier
already found** (`setdefault`-style merging throughout — several real bugs this project
shipped and fixed were exactly *blind-overwrite* merges destroying already-correct data;
see `docs/extraction-failure-modes.md`).

### Tier 1: Shopify JSON API — `ingestion/shopify_json.py`

Appending `.json` to a Shopify product URL (`store.com/products/{handle}` →
`store.com/products/{handle}.json`) returns the full structured product payload — no HTML
scraping or LLM call needed. Confirmed empirically: 13/15 (87%) success on real corpus
URLs. No WooCommerce found in this corpus's top 60 domains by ad volume — Shopify
dominates the supplements landing-page landscape, making this the highest-leverage tier.

`product_type` is flagged lower-trust (some stores stuff A/B-test bucket names in it,
e.g. "Intelligems Testing v2"); `tags` is the better category signal. `parse_shopify_product`
also derives `price_range` from the variant price spread and prefixes non-default variant
titles as `"Variant: {title}"` for `variants_featured`.

### Tier 2: Hardened HTML scrape — `ingestion/landing_page_scraper.py` + `ingestion/tiered_scraper.py` + `ingestion/rate_limiter.py`

The original v1 4a/4b logic (JSON-LD schema.org `Product`, OpenGraph fallback), hardened
after a real production incident: a 2,736-request, 15-concurrent-worker run tripped
Shopify's shared platform-level anti-abuse system — 1,669 HTTP 429s + 127 403s, spread
across *unrelated* domains failing together in the same burst window (confirmed: plain
`curl` against the same URLs succeeded minutes later with zero special handling — a
volume signature, not per-site blocking). Fixed with `ingestion/rate_limiter.py`: a
process-wide global token bucket (2 req/s, 4-token burst, anchored to Shopify's
documented Admin API rate) plus a lighter per-domain bucket (0.5 req/s, 2-token burst)
layered on top. A domain is exempted from the global bucket only after a *confirmed*
non-Shopify response (no `x-shopid`/`x-sorting-hat-podid`/`x-shardid` header and no
`cdn.shopify.com`/`Shopify.theme` marker in the body) — a failed/blocked response carries
no evidence either way and never demotes a domain.

`ingestion/tiered_scraper.py::scrape_and_extract()` composes Tier 1 + Tier 2 as a single
entry point, capped at 2 requests per URL total (reusing already-fetched HTML between
tiers rather than re-fetching): if the URL already has a literal `/products/{handle}`
path, it tries the `.json` endpoint directly; otherwise it follows redirects once (which
both resolves the final path and gives HTML to reuse if Tier 1 doesn't apply), then tries
`.json` on the resolved path if one exists.

### Tier 3/4: ZenRows managed scraping — `ingestion/zenrows_scraper.py`

Even with rate limiting fixed, 45.5% of unique URLs still failed 429/403 — broadly,
immediately, across unrelated domains. That pattern pointed at TLS/browser
fingerprinting, not volume. Rather than self-host TLS impersonation (a `curl_cffi`-based
design was scoped but never built — superseded before implementation), the project
adopted **ZenRows**, a managed scraping API that handles JS rendering, proxy rotation,
and anti-bot evasion server-side.

`extract_product_data()` runs a 4-tier parsing cascade over one fetched page — each tier
only fills what the previous left empty:

1. **XHR/background-request capture** (`_extract_from_xhr`) — rarely useful in practice;
   whether ZenRows' `json_response` capture parameter reliably surfaces anything was
   never confirmed against ZenRows' own docs, only inferred.
2. **JSON-LD** (`_extract_from_json_ld`) — `Product`/`AggregateRating` schema.org data;
   also the primary source for `product_name`/`brand_name` (via `Product.brand`, falling
   back to a same-page `Organization` block, `<meta property="og:site_name">`, or a
   parsed `<title>` tag split on en-dash/pipe separators — see bug #10/#13 below).
3. **Platform window objects** (`_extract_from_window_objects`) — regex-extracted
   `window.ShopifyAnalytics.meta.product`, `window.__INITIAL_STATE__`, Shopify
   Pixel-loader `"shop":{"name":...}"`, and Apple-Pay/Shopify-Payments
   `"merchantName":...` blocks (the latter two are the only source for brand on pages
   with no JSON-LD or og:site_name at all — bug #11).
4. **DOM/review-widget fallback** (`_extract_from_dom`) — CSS selectors tuned for
   Dawn/OS2.0 Shopify themes and WooCommerce, plus explicit Loox/Yotpo/Judge.me/Okendo
   review-widget detection (the *only* source for `rating`/`rating_count` at scale —
   Shopify's core product schema has no review data).

A dedicated Amazon path (in `builder_fingerprint.py`, invoked from both the fetch layer
and the Tier 4.5 dispatcher — see below) supplements this for Amazon-hosted landing
pages: marketplace-region-aware (a 20-TLD map to `(proxy_country, currency)`, so
`.com`/`.co.uk`/`.de`/etc. each resolve their own real price+currency rather than one
being forced onto all), and pins ZenRows' `premium_proxy`/`proxy_country` to the URL's
own marketplace region (see bug #17).

### Tier 4.5: Builder fingerprint — `ingestion/builder_fingerprint.py`

Custom "advertorial" direct-response funnel pages (built on PageFly, Zipify, GemPages,
ReConvert) emit **zero** Schema.org/OpenGraph markup — Tiers 1–4 find nothing on them.
Detection is builder-specific (`BUILDER_SIGNATURES`: each app stamps distinctive
container classes/CDN hosts into the DOM); the offer-grid and review-widget markup
*within* those containers follows similar-enough generic patterns across builders (a
quantity phrase + price, a review-count phrase) that one shared extractor
(`_extract_offer_matrix`/`_extract_social_proof`) serves all of them — no LLM call
needed.

**Amazon gets its own dedicated path** (`_extract_via_amazon`), checked *before* the
generic builder-signature loop, using Amazon's own stable element ids
(`#productTitle`, `#bylineInfo`, `.a-icon-alt`, `#acrCustomerReviewText`,
`#corePrice_feature_div .a-price .a-offscreen`) rather than the generic offer-card/hero
heuristics, which don't map onto Amazon's page structure at all (see bug #12).

### Tier 5: Zone-pruned LLM fallback — `ingestion/zone_pruner.py` + `ingestion/llm_fallback.py`

Last resort for pages still unresolved. Two steps:

1. **Zone pruning** (`zone_pruner.py::prune_to_markdown`) — locates the structural zones
   a page's useful content actually lives in (hero/offer via `_find_hero_zone`, social
   proof, specs) rather than handing the LLM the whole page, converts the pruned
   selection to Markdown under a token budget (default 4000 tokens, doubled to 8000 for
   the full-body-fallback path — see bug #7). Falls back to full-body Markdown if zone
   detection comes up empty *or* the hero zone found is too small to contain real content
   (under 100 chars — bug #8). The page's `<title>` tag is always prepended as a labeled
   "Page title: ..." line regardless of which path is taken, since a substantively-sized
   hero zone can still be pure offer/review boilerplate with zero product-identity text
   (bug #15).
2. **LLM extraction** (`llm_fallback.py::extract_via_llm`) —
   `ReplicateVisionClient.extract_structured_text()` (Gemini, accessed via Replicate per
   ADR-008's temporary, scoped exception to CLAUDE.md's Vertex-only rule — the same
   client `product_page_analyzer.py`'s Stage 4c semantic enrichment uses) against the
   pruned Markdown, with a literal snake_case JSON template embedded in the prompt
   (Pydantic can't catch a field-name-casing drift since every field has a default — this
   exact bug shipped once already in Stage 4c, fixed there and prompted here from the
   start). The LLM-facing schema (`_DirectResponseLLMExtraction`) makes every field
   `Optional`, including array fields — the real model emits explicit JSON `null` for
   fields it has no data for even when the prompt implies an array type, and a stricter
   schema failed validation on the majority of real responses in an initial smoke test.

**Hallucination guardrail**: `validate_against_raw_text()` nulls out numeric claims
(`review_count`, `rating_value`, and offer `total_price`) that don't appear verbatim in
the page's own visible text, rather than trusting the LLM outright. It also:
- classifies *why* a price-like number was found (`price_context`: `real_offer` /
  `bundle_price` / `shipping_or_promo_banner` / `rhetorical_or_competitor_price` /
  `cart_subtotal_widget`) so a "free shipping over $50" banner or a rhetorical "spending
  $1,029 on Ozempic" mention doesn't get mistaken for the product's actual price;
- trusts a bundle's `total_price` whenever its `price_per_unit` is independently grounded
  in the raw text, since bundle pages almost never state the multiplied total verbatim
  (bug #4);
- range-checks `rating_value` to `0 <= x <= 5` *before* the hallucination check, since a
  genuinely-printed "9.8/10" editorial score on a review-listicle site would otherwise
  pass the not-hallucinated check while being the wrong kind of number entirely (bug
  #19).

Confirmed via a 26-page hand-labeled golden set at 92.3% recoverable-price accuracy
(`pipeline/validation/price_context_validator.py`).

---

## Schema Graph

Four Pydantic/dataclass models are in play, each with a distinct job. Understanding how
they relate matters more than any one field list:

```
CompetitorAd  (ingestion/models.py)
  — one per scraped ad. `product_page: ProductPage | None`, one-to-one.
       │
       ▼
ProductPage  (ingestion/product_page.py)
  — the STABLE, FLAT output schema. What every tier ultimately writes into,
    and the only one of these four persisted inside CompetitorAd / the corpus
    JSON. Every other schema below is an intermediate, in-memory result that
    gets bridged into a ProductPage and then discarded.
       ▲                              ▲                         ▲
       │ to_product_page_updates()    │ _merge_semantic_          │ (direct field
       │ (zenrows_scraper.py)         │  extraction()             │  construction)
       │                              │ (product_page_analyzer.py)│
ZenRowsProductData              SemanticExtraction          (Tier 1/2 build a
(zenrows_scraper.py, @dataclass) (product_page_analyzer.py,   ProductPage directly:
  — Tiers 1-4's raw 5-field        Pydantic BaseModel)         shopify_json.py /
    extraction result, plus         — Stage 4c's LLM-only       landing_page_scraper.py)
    Tier 4.5/5 provenance and       output (category, USP,
    subscription/marketplace        cultural_branding,
    fields bolted on.               variants, price_range).
       ▲
       │ _merge_direct_response_into()
       │ (zenrows_scraper.py)
DirectResponseProductData  (ingestion/direct_response_schema.py, Pydantic BaseModel)
  — Tier 4.5 (builder_fingerprint.py) and Tier 5 (llm_fallback.py)'s shared,
    richer intermediate shape: offer_matrix (multi-tier bundle pricing),
    social_proof, page_metadata (builder detected, content hash, page type).
    Exists because a direct-response funnel page's real structure (a
    quantity/price ladder) doesn't fit ProductPage's flat price/rating
    fields — best_offer() picks one tier's price out of offer_matrix for
    the flat `price` field; every tier_label becomes a `variants_featured`
    entry (see bug #20).
```

Key relationship rules, all setdefault/never-overwrite unless noted:

- **`ProductPage.product_name`/`brand_name`** are sticky — once a prior tier has set
  them, no later tier's `ZenRowsProductData`/`DirectResponseProductData` value overwrites
  them (`to_product_page_updates`'s `if data.product_name and not base.product_name`
  check).
- **`ProductPage.price`/`rating`/`rating_count`** are deliberately *freshness-oriented*
  instead — a later tier's non-empty value is allowed to replace an earlier one — but the
  gate is a truthy check (`if data.rating_count:`), not `is not None`, so an LLM's
  explicit "found nothing" `0` can never silently overwrite a real prior value (bug #16).
- **`price`/`rating`** are sanity-bounded (`[$2, $1500]` / `(0, 5]`) at this same
  bottleneck function, independent of which tier produced the value — the single point
  every tier's output funnels through (bugs #18/#19).
- **`ProductPage.marketplace_region`/`price_currency`** are safe to overwrite outright
  (not setdefault) when `get_amazon_region()` resolves a value — they're deterministic
  (URL TLD), never an earlier tier's guess, so there's no "earlier, more trustworthy
  value" to protect.

### `confidence` is not a quality score

Confirmed during an internal audit: `ProductPage.confidence` only ever takes 3 values
(0.0 / 0.75 / 0.85 / 0.9, depending on the extraction path), each mapping 1:1 to *which
tier ran*, not whether that tier's extraction actually succeeded. Do not filter on it as
a trust signal — filter on whether specific fields are populated instead.

---

## Dedup layer — `ingestion/dedupe.py`

Failed URLs average ~4 ads each — heavily-promoted advertorial funnels get many
ad-variant creatives pointing at the same landing page, sometimes via distinct tracker
URLs with no shared query params. Three tiers, cheapest first:

1. `canonicalize_url()` — strips ad-tracking/funnel query params (`utm_*`, `fbclid`,
   `gclid`, `ad_id`, etc.), lowercases the host, sorts remaining params. Pre-fetch, free.
2. `get_content_hash()` — SHA-256 of normalized visible text (drops
   script/style/svg/noscript/iframe tags, strips long hex/base64-looking tokens and ISO
   timestamps before hashing). Post-fetch, catches byte-identical duplicates.
3. `is_near_duplicate()` — an in-house MinHash implementation (not the `datasketch`
   package — its eager `__init__.py` import pulled in `scipy`, which reproduced a
   multi-minute macOS-Gatekeeper-triggered hang for a dependency needed only for this one
   class) over Jaccard similarity (default threshold 0.95), catching near-duplicates
   (minor copy edits, dynamic tracker-path segments) that canonicalization or an exact
   hash miss.

Used most heavily inside `enrich_corpus_advertorial_fallback` (see below): a URL whose
fetched HTML is a near-duplicate of an already-processed page's HTML *within the same
run* gets that page's result copied instead of paying for another LLM call.

---

## Subscription detection — `ingestion/subscription_detector.py`

A page can have both a one-time price and a distinct recurring price (e.g. "$29.95
one-time" vs "$23.96/mo on subscription") — a separate axis from `price`/`price_range`.
Deterministic-first, same philosophy as builder fingerprinting: known subscription-app
signatures (`SUBSCRIPTION_APP_SIGNATURES`: Recharge, Bold Subscriptions, Skio, Awtomic,
Loop Subscriptions, Appstle — Recharge confirmed on 46% of sampled pages via
`window.RechargeStorefrontConfig` and script/class markers) plus a keyword/regex layer
for day/month-rate text patterns (`extract_subscription_price`, with a day→month ×30
estimate when only a daily rate is stated). `determine_subscription_status()` returns
`subscription_status` (`one_time_only` / `subscription_optional` / `subscription_required`
/ `unknown`) — `unknown` is a real coverage ceiling (signature/keyword-based detection,
not proof of absence), not a failure. A day/month-rate regex match is only trusted as a
real subscription price once `subscription_status != "unknown"` — a bare rate match with
no independent subscription signal turned out to be a rhetorical mention about a
*different*, compared product on at least one confirmed live page.

Always attempted when HTML is available (`extract_product_data` in
`zenrows_scraper.py`), independent of whether a one-time price was already resolved by
an earlier tier — a page can genuinely have both. No LLM call.

---

## UTM / campaign-taxonomy features — `ingestion/utm_features.py`

Pure URL-parsing over `CompetitorAd.link_url` — no new scraping, no dependency on which
landing-page tier resolved (or failed to resolve) the product data. Confirmed live: UTM
parameters are present on 203/2,736 ads (7.4%) in this corpus — real, but sparse.
`extract_campaign_features(link_url)` returns:

- `has_utm_tracking` — whether any `utm_*` param is present at all.
- `utm_medium_category` — `dedicated_paid_social` (a `paid-social`-style `utm_medium`) vs
  `legacy_generic` (`cpc`/`social`/`ppc`) vs `unknown`.
- `utm_dynamic_naming` — whether `utm_campaign`/`utm_content`/`utm_term` contains
  unresolved Facebook dynamic-variable templating (e.g. literal `{{campaign.id}}` —
  confirmed present in this corpus's own `link_url`s, and separately flagged as a
  URL-malformation issue for price extraction; here it's read as signal, not noise).
- `utm_content_granularity_score` — count of distinct creative-dimension keywords
  (`hook`, `cta`, `ugc`, `static`, `video`, `testimonial`, `flavor`, `size`, `angle`,
  `format`) present in `utm_content` — a proxy for how many testing axes an advertiser is
  explicitly labeling.
- `campaign_role_signal` — a best-effort, explicitly-not-ground-truth heuristic
  (`likely_test` / `likely_scale` / `unknown`) for whether a campaign looks like a
  deliberate, disposable creative test vs. a confident, scaled winner.

These are **X-axis** (campaign-construction/taxonomy) features, not Y-axis performance
outcomes — inputs that might help *explain* differences in the real Y-axis signals
(`days_active`, `collation_count`, `variants_featured_count`), same framing as
`subscription_status`/`price_context` elsewhere in this codebase: an honest,
best-effort classification, not a claim of ground truth. Not yet wired into any
corpus-level enrichment CLI — `extract_campaign_features` is a standalone function a
Step 3 caller would invoke directly over the existing corpus's `link_url` field.

---

## Integration Points

### CLI (`ingestion/enrich_with_product_pages.py`)

```bash
# Serial (workers=1, default): scrapes without rate limiting or dedup.
python -m ingestion.enrich_with_product_pages \
  --ads data/supplements.json --out data/supplements_enriched.json

# Tiered fast path: Shopify JSON + hardened HTML scrape, rate-limited, parallel,
# URL-deduped (one fetch per unique link_url, fanned out to every ad sharing it).
python -m ingestion.enrich_with_product_pages \
  --ads data/supplements.json --out data/supplements_enriched.json \
  --tiered --workers 8 --resume

# ZenRows pass: backfill price/description/variants/rating for every unique URL.
python -m ingestion.enrich_with_product_pages \
  --ads data/supplements_enriched.json --out data/supplements_enriched.json \
  --zenrows --diagnostics-csv data/zenrows_diagnostics.csv

# Advertorial fallback: Tier 4.5 + Tier 5, targeted at the ZenRows diagnostics'
# success=False (or price-still-missing) rows.
python -m ingestion.enrich_with_product_pages \
  --ads data/supplements_enriched.json --out data/supplements_enriched.json \
  --advertorial-fallback --diagnostics-csv data/zenrows_diagnostics.csv
```

`--tiered`/`--zenrows`/`--advertorial-fallback` are independent passes, always additive
(merge into whatever `product_page` an ad already has — never a blind overwrite) and safe
to run in any order or repeatedly. `--resume` matches by `ad_archive_id` (or, for
`--zenrows`/`--advertorial-fallback`, by which *URLs* a prior checkpoint already touched)
against `--out` if it already exists. The module has accumulated five separate
orchestration entry points (`enrich_corpus`, `enrich_corpus_parallel`,
`enrich_corpus_parallel_tiered`, `enrich_corpus_zenrows`,
`enrich_corpus_advertorial_fallback`) across its iterative build-out — see
`docs/architecture-review-ingestion.md` for which of these are still the recommended path
versus superseded-but-not-removed.

### Programmatic use

```python
import httpx
from ingestion.rate_limiter import get_rate_limiter_registry
from ingestion.tiered_scraper import scrape_and_extract
from ingestion.zenrows_scraper import extract_product_data

# Fast path: Tier 1 (Shopify JSON) + Tier 2 (hardened scrape), one entry
# point, at most 2 HTTP requests total for the URL.
product_page = scrape_and_extract(
    "https://example.com/products/creatine",
    client=httpx.Client(),
    rate_limiter=get_rate_limiter_registry(),
)  # -> ProductPage | None

# ZenRows cascade: operates on HTML you've already fetched (e.g. via
# ZenRows' own API) — enable_llm_fallback=True opts into the real-cost
# Tier 5 LLM call when Tiers 1-4.5 find nothing.
data = extract_product_data(html, url="https://example.com/...", enable_llm_fallback=True)
# -> ZenRowsProductData
```

### Merge with Step 2 — `ingestion/run_step2_pipeline.py` + `ingestion/merge_step2_features.py`

Step 2 (the separate creative-analysis pipeline in `pipeline/`) operates on ad *images*,
independent of anything Step 1.5 does with landing pages. `run_step2_pipeline.py` is the
bridge: for each ad in the corpus, fetches the first `image_urls` entry into memory (no
image ever touches disk — every Step 2 stage operates on `context.image_bytes`
directly), runs it through `pipeline.orchestrator.build_default_stages()`'s stage chain,
and writes the resulting `ExtractionResult` to `out/step2/<ad_id>.json`. Resumable by
output-file existence (an ad whose JSON already exists is skipped). Concurrency is a
`ThreadPoolExecutor` over ads (default 4) — each ad's cost is dominated by network I/O
(image fetch + two Gemini calls inside the cognitive stage), not CPU, so this is the main
speed lever. A per-ad catch-all exception boundary exists specifically because an
unhandled `http.client.IncompleteRead` (Facebook's CDN closing a connection mid-read) was
confirmed to have crashed a multi-hour batch run in production before this was added.

`merge_step2_features.py::merge_step2_into_corpus()` then reads every
`out/step2/<ad_id>.json`, flattens each `ExtractionResult` via `flatten_features()` (plus
`ColorProfile`'s fields, pulled in separately since `flatten_features()` is scoped to
copywriting + placement only), and attaches the result as a new `creative_features`
sub-key on the matching ad — additive, written to a new output file rather than
overwriting the canonical corpus, so the merge can be verified before promotion.

---

## Testing

`ingestion/tests/` — roughly one `test_*.py` per module, 424 `test_` functions across the
suite by a static count (the real pytest-collected total may differ slightly for
parametrized cases), all offline (no network/GCP/Replicate/ZenRows calls; hand-rolled
fakes injected via `run_fn`/`fetch_fn`/`client` parameters throughout, matching the
project's established DI convention). Six loose top-level scripts
(`test_creatine.py`, `test_creatine_v2.py`, `test_furniture.py`,
`test_furniture_with_ad_context.py`, `test_furniture_with_llm.py`, `test_real_pages.py`)
sit directly in `ingestion/` rather than `ingestion/tests/` — these are ad-hoc, live
Apify/network manual-verification scripts from early development, not part of the pytest
suite; see `docs/architecture-review-ingestion.md` for disposition.

- `pipeline/validation/price_context_validator.py` — golden-set eval (26 hand-labeled
  pages), 92.3% recoverable-price accuracy.
- `docs/extraction-failure-modes.md` — a living catalog of 20+ confirmed, root-caused
  extraction bugs (symptom → root cause → fix → regression test), the project's record of
  *why* each guardrail/heuristic exists. Cited by number throughout this doc.

---

## Known Limitations

Pulled directly from `docs/extraction-failure-modes.md`, which is the authoritative,
continuously-updated source — this section summarizes, it doesn't replace it.

- **`product_category`/`product_subcategory`** — Stage 4c's original LLM
  semantic-enrichment scope — only run on Shopify-tier successes; not attempted for
  ZenRows/Tier-5-resolved pages. Coverage stays flat (~36%) across every reprocessing
  round for this reason, not a bug.
- **Async-rendered review widgets** (65% of `rating` still missing corpus-wide) — some
  pages' rating data hasn't finished its own client-side fetch within ZenRows' JS-render
  wait window. An opt-in `wait_for`-based retry exists
  (`fetch_product_zenrows(..., enable_review_widget_retry=True)`) but is unproven at
  scale (live-tested against 2 domains: technically works, recovered nothing, cost
  45–90s/page) — not enabled by default.
- **Non-subscription "$X/day" rhetorical framing** (e.g. "$70/month" describing a
  *compared* product, not the page's own) — a genuine, unresolved data-completeness gap
  distinct from the (solved) genuine-subscription-pricing case.
- **Comparison/listicle pages** — `brand_name`/`price` sometimes resolve to the
  *publisher's* brand/a competitor's price rather than the featured product's — a
  true-but-mis-scoped value, not a fabrication.
- **`variants_featured` corpus reprocessing not yet done** (bug #20) — the fix that maps
  every `offer_matrix` tier label into `variants_featured` is shipped and live-verified,
  but recovering the data corpus-wide needs a fresh paid ZenRows/LLM pass over every ad
  whose `extraction_method` ended in `tier_4_5_builder`/`tier_5_llm`/`zenrows` — scope and
  cost are a decision for whoever runs the next reprocessing pass.
- **Amazon/marketplace sites this pipeline doesn't target** (Chewy, etc.) — permanent,
  expected zero-coverage, not a defect. Amazon itself is low-prevalence in this corpus
  (0.7%, 19/2,736 ads) — the dedicated extractor (bug #12) is judged sufficient
  investment; Amazon price extraction specifically remains an open, low-priority gap.
- **`collation_count`** (a `CompetitorAd`-level field, not `ProductPage`) — investigated,
  found to be a genuine passthrough from Apify's own scrape, not something this pipeline
  derives; its low variance (median 1) is unexplained and may just reflect this niche's
  advertiser behavior.
- A long tail of below-ROI-bar, not-yet-root-caused gaps (PageFly offer-card selector
  misses, suspected JS-render-timing races, cross-sell/bundle-builder pricing ambiguity,
  hidden-input JSON bundle configs) is tracked in `docs/extraction-failure-modes.md`'s
  "Not yet root-caused" section rather than repeated here.

---

## Performance & Cost (at corpus scale — 2,736 ads / 985 unique URLs)

| Tier | Cost model | Notes |
|---|---|---|
| 1 (Shopify JSON) | Free | 1 request, no rate limit concern (native API) |
| 2 (hardened scrape) | Free | Rate-limited to 2 req/s global + 0.5 req/s/domain |
| 3/4 (ZenRows) | Paid, per-request | JS rendering + proxy costs more per request; run once per unique URL |
| 4.5 (builder fingerprint) | Free | Reuses the page already fetched for Tier 3/4 |
| 5 (LLM fallback) | Paid, per-LLM-call | Only runs on pages 1–4.5 fully failed; zone-pruning bounds token cost |

### Final corpus coverage (2,736 ads, live-measured)

| Field | Coverage |
|---|---|
| `product_page` present | 97.3% |
| `price` | 70.0% |
| `product_name` | 75.0% |
| `brand_name` | 61.5% |
| `rating` | 32.9% |
| `rating_count` | 43.1% |
| `variants_featured` | 28.7% (pre-bug-#20-reprocessing; the fix is shipped, corpus not yet re-run) |
| `subscription_status` known (≠ `unknown`) | 33.2% |

Residual gaps are believed to reflect genuine data unavailability (dead links,
non-Shopify marketplace listings this pipeline was never targeting, JS-only widgets,
comparison/listicle scoping ambiguity) rather than unfixed pipeline defects — see Known
Limitations above.

---

## Deferred (still not built)

- **Vision API screenshots** — headless-browser screenshot + Cloud Vision object/label
  detection for visual product features. Never built; ZenRows' JS rendering + the Tier 5
  LLM fallback covers most of what this would have added.
- **Visual verification** (`is_visually_verified_match`) — still `null` on every ad;
  Stage 4's own v1-deferred scope, unchanged (see CLAUDE.md's Current-State section).
- **Self-hosted TLS impersonation** (`curl_cffi`) — designed, never built; superseded by
  ZenRows before implementation began. Kept as a fallback design if ZenRows ever proves
  insufficient.
- **`utm_features.py`'s campaign-taxonomy features are not wired into any corpus-level
  enrichment CLI yet** — the module is complete and tested, but nothing in
  `enrich_with_product_pages.py` calls it; a Step 3 caller would need to invoke
  `extract_campaign_features` directly against the corpus's own `link_url` field.

---

## See Also

- **`docs/extraction-failure-modes.md`** — the detailed bug catalog referenced throughout
  this doc.
- **`docs/architecture-review-ingestion.md`** — code-quality/architecture review of
  `ingestion/` (dead entry points, duplicated logic, latent bugs) — a companion to this
  doc, not a duplicate of it.
- **ADR-005** — Supersede Media AI Platform; defer ROAS/XGBoost until own-ad data exists.
- **ADR-006** — Step 2 Extraction architecture; Stage 4 deferred there (this doc is the
  Stage-4-*is*-built counterpart).
- **ADR-008** — Layered color/VLM/imagery/embeddings; the ReplicateVisionClient/Gemini-
  via-Replicate choice this doc's Tier 5, Stage 4c, and the price-context validator all
  rely on (a temporary, scoped exception to CLAUDE.md's Vertex-only rule).
- **CONTEXT.md** — Domain glossary (Longevity/`days_active`, Variant/`collation_count`).
