# Architecture Review: `ingestion/`

**Scope**: `ingestion/*.py` (23 modules) plus its test suite (`ingestion/tests/`, 24 files)
and the 6 stray top-level scripts. Read-only review — no files under `ingestion/`,
`pipeline/`, or elsewhere were modified to produce this document. Companion to
`docs/blueprints/Step-1-Stage-4-Landing-Page-Analysis.md`, which documents what the
system *does*; this documents what's worth fixing in *how it's built*.

**Method**: full read of every module (not filename-based guessing), cross-referenced
against `docs/extraction-failure-modes.md`'s 20-bug history, plus direct verification of
several claims below by running `pytest --collect-only` (collection only — no tests
executed, no network calls made).

Findings are ranked by real impact: correctness/cost bugs first, then dead code, then
duplication, then style. Each entry names the file/lines, states what's wrong, why it
matters, and a concrete fix direction — not a full patch.

---

## What's working well (context for the rest of this review)

Worth naming before the critique: the `setdefault`-only merge convention is applied
consistently across almost every tier (`to_product_page_updates`,
`_merge_direct_response_into`, `_merge_semantic_extraction`, `_merge_product_pages`), the
free-tier-first cascade ordering (Shopify JSON → hardened scrape → ZenRows → builder
fingerprint → LLM) is real and consistently honored in the main paths, and
`docs/extraction-failure-modes.md`'s bug-catalog discipline (root cause → fix →
regression test → real URL) is unusually rigorous for a fast-moving scraping project.
The issues below are mostly *scar tissue from that same iterative history* — pieces that
were correct when written but never revisited as the system grew around them — not
signs of a poorly-designed system.

---

## 1. `ingestion/tests/`'s entire suite is excluded from the default test run (confirmed, not theoretical)

**File**: `pyproject.toml:64-66`

```toml
[tool.pytest.ini_options]
testpaths = ["pipeline/tests"]
python_files = "test_*.py"
```

**What's wrong**: `testpaths` restricts *any bare* `pytest`/`uv run pytest` invocation to
`pipeline/tests` only. Verified directly: `uv run pytest --collect-only -q` collects
**284 tests, zero of them from `ingestion/tests/`**. `ingestion/tests/` has its own 424
`test_` functions (a static grep count) — a suite *larger* than `pipeline/tests`' — that
never runs unless a developer explicitly types `uv run pytest ingestion/tests`. There is
no CI configuration in the repo (no `.github/` directory at all) to compensate by running
both paths separately.

**Why it matters**: CLAUDE.md's own documented command is `uv run pytest pipeline/tests`
— explicit about the path, so it doesn't even attempt `ingestion/tests`. Nothing in the
documented workflow ever exercises the landing-page-enrichment test suite. A regression
in `zenrows_scraper.py`, `zone_pruner.py`, or `llm_fallback.py` — the most bug-prone,
most iterated-on modules in the codebase per `docs/extraction-failure-modes.md` — could
ship silently as long as `pipeline/tests` stays green.

**Fix direction**: either broaden `testpaths` to `["pipeline/tests", "ingestion/tests"]`,
or drop `testpaths` entirely and let pytest's default rootdir discovery find both (see
Finding 4 below for why "drop it entirely" needs a companion fix first).

---

## 2. CLI flag combinations in `enrich_with_product_pages.py` silently resurrect a previously-proven-broken scraping path

**File**: `ingestion/enrich_with_product_pages.py:944-983` (`main()`)

**What's wrong**: two separate, unvalidated footguns in the same dispatch block:

```python
if args.workers > 1 and args.tiered:
    return enrich_corpus_parallel_tiered(...)   # rate-limited, deduped, recommended
if args.workers > 1:
    return enrich_corpus_parallel(...)          # naive concurrent scraping, NO rate limit
return enrich_corpus(...)                        # fully serial, NO rate limit
```

- Passing `--tiered` *without* also passing `--workers` greater than 1 silently no-ops
  the flag — it falls through to `enrich_corpus()`, fully serial, with no rate limiting
  and no URL dedup. No warning is printed.
- Passing `--workers 8` *without* `--tiered` routes to `enrich_corpus_parallel()` —
  which, per `enrich_corpus_parallel_tiered`'s own docstring two functions below it in
  the same file, is *exactly* the naive per-request concurrent-scraping pattern that
  "tripped Shopify's shared platform-level rate limit at scale (2,736 requests, 15
  workers -> 66% failure)". That historically-proven-broken code path is still live,
  still the default for any `--workers > 1` invocation that forgets `--tiered`.

**Why it matters**: the argparse help text for `--tiered` says "Only meaningful with
`--workers > 1`" — a reader could reasonably assume the flag is simply *ineffective* when
misused, not that omitting the companion flag reproduces a documented production
incident (429/403 storm across hundreds of unrelated domains). This is the single
highest-risk finding in this review: it's not a bug that produces wrong data, it's a
footgun that can get scraping IPs/API keys rate-limited or blocked at corpus scale, and
the two flags needed to avoid it aren't validated together anywhere.

**Fix direction**: validate the combination in `main()` — either make `--tiered` imply
`--workers` defaulting higher (e.g. warn-and-bump to a sane minimum), or `parser.error()`
when `--workers > 1` is passed without `--tiered` for a corpus above some size threshold,
or (simplest) fold rate limiting into `enrich_corpus_parallel` itself so there's no
un-rate-limited concurrent path left to accidentally select.

---

## 3. `enrich_corpus_advertorial_fallback`'s LLM-escalation check ignores data this same function already recovered for free

**File**: `ingestion/enrich_with_product_pages.py:819-832`

```python
elif fetch_result.success and fetch_result.data and fetch_result.data.product_price:
    merged_page = to_product_page_updates(fetch_result.data, existing_page, url)
else:
    # Escalate to the LLM-enabled re-run whenever price is still missing...
    data = extract_product_data(fetch_result.html, url=url, enable_llm_fallback=True)
    merged_page = to_product_page_updates(data, existing_page, url)
```

**What's wrong**: the escalation-to-paid-LLM decision checks only
`fetch_result.data.product_price` — the *freshly re-fetched* Tiers-1-4 cascade's own
result — never `existing_page.price`. But `existing_page` was already updated, a few
dozen lines earlier in this exact function (the "free Shopify-JSON pre-pass," added to
fix bug #14 in `docs/extraction-failure-modes.md`), for any URL with a literal
`/products/{handle}` path. If that pre-pass already recovered a price and the fresh
ZenRows Tiers-1-4 refetch doesn't *independently* rediscover it (the common case — these
are URLs a prior round already flagged as Tiers-1-4 failures), this code pays for a real
Tier-5 LLM call it doesn't need. `to_product_page_updates` would have kept the existing
price either way (it's a `setdefault`-safe merge), so the LLM call's own price output is
very likely thrown away — a pure cost regression, not a correctness one, but a direct
repeat of the same class of bug #14 fixed one call-site up.

**Why it matters**: this function's whole design premise (per its own docstring and per
`docs/blueprints/Step-1-Stage-4-Landing-Page-Analysis.md`'s "free-tier-first,
cost-conscious cascade ordering" principle) is to spend the paid LLM tier only when
cheaper tiers have genuinely exhausted their options. This one check silently violates
that on every URL the free pre-pass a few lines above it already solved.

**Fix direction**: change the condition to check `existing_page.price` (or a merged view
of `existing_page` + `fetch_result.data`) rather than `fetch_result.data.product_price`
alone before deciding to escalate.

---

## 4. Stray top-level `ingestion/test_*.py` scripts perform live, unmocked network calls and *are* collected under a natural pytest invocation

**Files**: `ingestion/test_creatine.py`, `test_creatine_v2.py`, `test_furniture.py`,
`test_furniture_with_ad_context.py`, `test_furniture_with_llm.py`, `test_real_pages.py`

**What's wrong**: these are early-development manual-verification scripts, not part of
the `ingestion/tests/` package — they live directly in `ingestion/` and call
`ingestion.apify_client.run_ad_scrape(search_query=..., count=...)` with no `run_fn`
injected, i.e. a real, live Apify API call, plus (for the `*_with_llm`/`*_with_ad_context`
variants) real LLM calls via `extract_product_page(..., use_llm_enrichment=True)`.
Verified directly: `uv run pytest ingestion/ --collect-only -q` collects all 6 of them
alongside the real `ingestion/tests/` suite (430 total vs. 424 in `ingestion/tests/`
alone). `uv run pytest ingestion/` — a completely natural command for "run the ingestion
tests" — would *execute* them, not just collect them.

**Why it matters**: this contradicts the project's own offline-test convention (verified
elsewhere as `uv run pytest pipeline/tests` needing no GCP/network credentials). Anyone
with `APIFY_API_TOKEN`/`REPLICATE_API_TOKEN` set locally who runs `pytest ingestion/`
gets real API spend and non-deterministic network-dependent failures masquerading as
test failures.

**Fix direction**: move these out of the package entirely (a `scripts/manual/` or
`ingestion/_manual_scripts/` directory pytest never scans), or delete them outright since
`ingestion/tests/test_landing_page_scraper.py` and `test_product_page_analyzer.py` now
cover the same surface with proper mocks. At minimum, rename them off the `test_*`
pattern so pytest's `python_files = "test_*.py"` config stops matching them.

---

## 5. `is_amazon_url()`'s regex is looser than `get_amazon_region()`'s explicit map — the two can disagree on the same URL

**File**: `ingestion/builder_fingerprint.py:196-257`

```python
_AMAZON_DOMAIN_RE = re.compile(r"(?:^|\.)amazon\.[a-z.]+$", re.IGNORECASE)

def is_amazon_url(url: str) -> bool:
    return bool(_AMAZON_DOMAIN_RE.search(urlsplit(url).netloc))
```

**What's wrong**: `[a-z.]+$` after `amazon\.` accepts *any* run of lowercase letters and
dots to the end of the string, not just a real TLD. A hostname like
`reviews.amazon.somecomparisonsite.net` — "amazon" appearing as one dot-delimited label
followed only by lowercase/dot characters to the end of the string — matches this regex,
even though it isn't Amazon at all and would correctly return `None` from
`get_amazon_region()` (which checks against an explicit 20-entry TLD map via exact/suffix
match). The two functions are meant to answer the same underlying question
("is this an Amazon marketplace URL") but use different logic and can disagree.

**Why it matters**: `is_amazon_url()` gates `extract_via_builder_fingerprint`'s dispatch
to the Amazon-specific extractor (`_extract_via_amazon`) and `fetch_product_zenrows`'s
decision to set ZenRows' `premium_proxy`/`proxy_country` params. A false positive here
routes a real, non-Amazon advertorial page through Amazon-only selectors
(`#productTitle`, `#bylineInfo`, etc. — which would simply find nothing and fall through,
low direct harm) but also means the "is this Amazon" check that matters for routing isn't
single-sourced — a maintainer fixing one won't know to check the other.

**Fix direction**: derive `is_amazon_url()` from the same `_AMAZON_TLD_TO_REGION` keys
`get_amazon_region()` already uses (e.g. `return get_amazon_region(url) is not None`, or
a shared suffix-set check both call), rather than a second, independently-drifting
regex.

---

## 6. `_MERGE_FILLABLE_FIELDS` wasn't updated when `ProductPage` grew three new fields — the near-duplicate-dedup merge path silently drops them

**File**: `ingestion/enrich_with_product_pages.py:574-588`

```python
_MERGE_FILLABLE_FIELDS = (
    "product_name", "product_category", "product_subcategory", "brand_name",
    "price", "price_currency", "price_range", "rating", "rating_count",
    "marketing_copy", "usp", "cultural_branding", "variants_featured",
)
```

**What's wrong**: `ProductPage` (`ingestion/product_page.py`) has three fields this tuple
never picked up: `subscription_status`, `subscription_price`, `marketplace_region`. Every
other merge path in the codebase (`to_product_page_updates` in `zenrows_scraper.py`)
handles these correctly (setdefault-style, matching the fields' own "unknown"/`None`
defaults). But `_merge_product_pages` — used by `enrich_corpus_advertorial_fallback`'s
near-duplicate-dedup hit path (`_merge_product_pages(existing_page, dup_match)`) and its
free Shopify-JSON pre-pass — silently ignores these three fields even when `dup_match`
(a near-duplicate page's already-resolved data) has them and `existing_page` doesn't.

**Why it matters**: a page resolved via the near-dup-dedup shortcut ends up with strictly
*less* subscription/marketplace data than the exact same page would have gotten via a
direct `to_product_page_updates` call — a silent, hard-to-notice coverage gap introduced
purely because this allowlist wasn't kept in sync as the schema grew. Exactly the kind of
gap that's invisible until someone specifically audits `subscription_status` coverage
among near-dup-resolved ads.

**Fix direction**: either add the three missing field names to the tuple, or replace the
static tuple with `ProductPage.model_fields.keys()` minus the handful of fields that
need special handling (`extraction_method`, `confidence`, `url`, `fallback_used`,
`shows_all_variants`) so a future field addition can't silently fall through this gap
again.

---

## 7. HTML-tag-stripping / text-cleaning logic is implemented independently at least four times

**Files**: `ingestion/product_page_analyzer.py:25-45` (`_extract_visible_text`, regex-based),
`ingestion/product_page_analyzer.py:152-154` (a second, inline 2-line regex strip inside
`extract_semantic_fields_from_shopify_json`), `ingestion/shopify_json.py:140-141` (a
third, near-identical inline regex strip inside `parse_shopify_product`), and
`ingestion/dedupe.py:122-131` (`get_content_hash`, BeautifulSoup-based — a genuinely
different library, same conceptual job).

**What's wrong**: `zenrows_scraper.py::clean_description` does the right thing — it
imports and reuses `product_page_analyzer._extract_visible_text` for its plain-HTML
case. But `product_page_analyzer.py` itself doesn't reuse its own helper one function
below where it's defined: `extract_semantic_fields_from_shopify_json` re-derives the same
"strip tags, collapse whitespace, truncate" logic inline instead of calling
`_extract_visible_text`. `shopify_json.py::parse_shopify_product` does the same thing a
third time, independently.

**Why it matters**: three of the four implementations are doing the *exact same
two-line transformation* (`re.sub(r"<[^>]+>", " ", html)` then whitespace collapse) with
slightly different truncation lengths (1000/3000/15000 chars) and no shared name — a
future change to how tags should be stripped (e.g. to also handle the Portable-Text-JSON
case `clean_description` already special-cases) has three other call sites that won't
get it.

**Fix direction**: promote `_extract_visible_text` (or a small `strip_html(text,
max_chars)` utility) to a shared location both `shopify_json.py` and
`product_page_analyzer.py` can import without creating a circular dependency, and replace
the two inline duplicates with calls to it.

---

## 8. The "hand-written JSON template must match the Pydantic schema" bug has been fixed twice, never fixed at the root

**Files**: `ingestion/product_page_analyzer.py:83-88` (`_SEMANTIC_JSON_INSTRUCTIONS`),
`ingestion/llm_fallback.py:131-141` (`_DIRECT_RESPONSE_JSON_INSTRUCTIONS`)

**What's wrong**: both modules embed a hand-written, literal JSON-shape string in their
LLM prompt ("Respond only with JSON matching this schema... {"product_category": "...",
...}") that must stay byte-for-byte in sync with a separate Pydantic model's actual field
names. Both modules' own docstrings/comments explicitly reference the *same* historical
bug as the reason this pattern exists: a Title-Case-vs-snake_case mismatch that Pydantic
silently swallowed (every field had a default, so validation "succeeded" against an
all-empty object). The fix applied both times was "write the JSON template as a
module-level constant right next to the schema, and share it verbatim with the prompt" —
a discipline that depends entirely on every future contributor doing the same manual
sync a third time.

**Why it matters**: this is the one place in the review where the same root-cause bug
class has already shipped once, has a known fix, and the fix was applied ad hoc twice
rather than systematized once. A third schema added the same way (there's already a
third LLM-schema pattern brewing — `SemanticExtraction` and `_DirectResponseLLMExtraction`
are both good candidates for this) is one copy-paste slip away from reintroducing the
exact bug both existing comments warn against.

**Fix direction**: a small helper that renders a Pydantic model's fields into the
"key": type-hint or-null instruction string automatically (via `model_fields` or
`model_json_schema()`), so the prompt template can never drift from the schema by
construction rather than by discipline.

---

## 9. `0 <= rating <= 5` range validation is duplicated three times, each citing the others as justification for not sharing it

**Files**: `ingestion/llm_fallback.py:287-306` (`validate_against_raw_text`),
`ingestion/builder_fingerprint.py:161-167` (`_extract_social_proof`),
`ingestion/zenrows_scraper.py:1081-1084` (`to_product_page_updates`)

**What's wrong**: all three independently reimplement the same one-line check (mapped to
bug #19's fix — editorial "9.8/10" scores misread as 5-star ratings). Each site's own
comment explicitly says "same defensive range check as [the other file]" — the
duplication is acknowledged in-line, three times, rather than factored out once.

**Why it matters**: low risk on its own (a one-line check is unlikely to drift), but it's
a clean, low-effort consolidation the codebase has already flagged for itself three
separate times without acting on it — a good first PR for whoever picks up this review.

**Fix direction**: a `ingestion/validation.py` (or similar) module with
`is_valid_star_rating(value: float | None) -> bool` that all three call.

---

## 10. Amazon's checkout-widget false positive is handled by two independent, non-cross-referenced mechanisms

**Files**: `ingestion/zone_pruner.py:53-63,122-134` (`_CHECKOUT_FLOW_TEXT_RE`, a generic
content-keyword denylist applied to *every* page's hero-zone candidate) vs.
`ingestion/builder_fingerprint.py:196-374` (`is_amazon_url`-gated dedicated
`_extract_via_amazon` path)

**What's wrong**: both exist specifically to stop Amazon's checkout/subscription-delivery
widget ("Proceed to checkout", "Choose how often it's delivered") from masquerading as
the product's own content — zone_pruner's own comment cites the exact same live
investigation (`multi-field extraction-gap investigation, 2026-08-15`) that motivated
`builder_fingerprint.py`'s dedicated Amazon path. But `zone_pruner.py`'s fix is a
*generic*, URL-agnostic keyword denylist applied to every page regardless of domain,
while `builder_fingerprint.py`'s fix is Amazon-domain-gated and reaches for
Amazon-specific DOM ids instead. They were built in the same investigation, solve
overlapping instances of the same underlying problem, and neither references the other.

**Why it matters**: this is the "accumulated bug-fix history left a scar" pattern the
review was asked to look for directly. In practice `builder_fingerprint.py`'s dedicated
Amazon path runs *first* (Tier 4.5, before Tier 5's `zone_pruner` is ever reached) and
usually succeeds, so `zone_pruner`'s generic keyword denylist is a `zone_pruner.py`-only
safety net for the case where the Amazon-specific extractor already returned `None`. That's
a reasonable defense-in-depth design in hindsight, but it isn't documented as
intentional layering anywhere — it reads, and was very likely built, as two independent
fixes for the same bug in two different rounds.

**Fix direction**: no code change needed necessarily, but the two should cross-reference
each other in comments (or `zone_pruner.py`'s denylist should explicitly note it's the
Tier-5 backstop for when Tier 4.5's dedicated Amazon path already failed), so a future
maintainer doesn't spend time re-diagnosing a "new" Amazon issue that's actually this
same layering.

---

## 11. `variants_featured` string formatting is inconsistent across tiers (acknowledged, not fixed)

**Files**: `ingestion/shopify_json.py:152` (prefixes `"Variant: {title}"`) vs.
`ingestion/zenrows_scraper.py:681` (`_merge_direct_response_into`, stores bare
`tier_label` strings like `"3 Bottles"`, no prefix)

**What's wrong**: the fix for bug #20 (variants_featured structurally discarded) added a
second source of `variants_featured` entries with a different string shape than the
pre-existing Tier 1 source — the code's own comment calls this out explicitly as "a
pre-existing inconsistency between the two modules, not introduced here" rather than
fixing it.

**Why it matters**: low severity, but real — any downstream consumer (Step 3 feature
engineering) treating `variants_featured` as uniform free text (e.g. counting distinct
values, or pattern-matching on `"Variant: "`) will see two incompatible conventions
depending on which tier populated the field, with no field-level marker distinguishing
them.

**Fix direction**: pick one convention (prefixing is more self-describing) and apply it
at the single point both paths already funnel through (`to_product_page_updates`) rather
than at each producer.

---

## 12. `enrich_with_product_pages.py` has accumulated five orchestration entry points; two are effectively superseded but not marked as such

**File**: `ingestion/enrich_with_product_pages.py` (whole file, 988 lines)

**What's wrong**: `enrich_corpus` (serial), `enrich_corpus_parallel` (naive concurrent),
`enrich_corpus_parallel_tiered` (rate-limited + deduped — the recommended path per its
own docstring), `enrich_corpus_zenrows`, and `enrich_corpus_advertorial_fallback`
(targeted Tier 4.5/5 re-run) all live in one 988-line file. The first two are
superseded by the third for any real corpus (see Finding 2) but remain fully
implemented, tested, and CLI-reachable with no deprecation marker, docstring warning
pointing forward, or comment explaining when (if ever) they're still the right choice.

**Why it matters**: this is the file the task brief specifically called out as having
"accumulated multiple entry points over many iterative rounds" — confirmed. It's not
wrong to keep superseded-but-working code around during active iteration, but with
nothing marking `enrich_corpus`/`enrich_corpus_parallel` as legacy, a new contributor
has no signal that `enrich_corpus_parallel_tiered` is the one to extend.

**Fix direction**: either delete the two superseded functions now that
`enrich_corpus_parallel_tiered` covers their use case with rate limiting for free, or add
an explicit `# Superseded by enrich_corpus_parallel_tiered — kept only for X reason`
docstring note to both, matching the honesty standard the rest of this codebase already
applies to its own limitations.

---

## 13. `_extract_from_xhr` (ZenRows internal Tier 1) is live code whose own docstring says it's never been confirmed to do anything

**File**: `ingestion/zenrows_scraper.py:228-283`

**What's wrong**: the function's docstring is unusually candid: "Search ZenRows'
captured background XHR/JSON responses (if the `json_response` param actually returns
them — **NOT confirmed** against ZenRows' PyPI package description, only
js_render/wait/wait_for/autoparse/css_extractor were documented there)... **Verify the
real response shape during the live smoke test**; designed to degrade gracefully (return
`{}`) if the assumed shape isn't present." The v2 blueprint doc itself calls this tier
"rarely useful in practice."

**Why it matters**: low severity (it degrades gracefully to a no-op, per its own design),
but it's ~55 lines of speculative parsing logic, with its own test coverage, for a data
shape nobody has confirmed ZenRows actually returns. It's the kind of code that's easy to
forget is unverified once it's been sitting in the codebase for a while.

**Fix direction**: either confirm the real shape against a live ZenRows response once
(cheap, one-time) and update the docstring either way, or remove the tier and its tests
if it's confirmed to never fire in practice — current state is an indefinitely-deferred
"verify this" note in production code.

---

## 14. Retry-status-code convention is deliberately inconsistent across modules (self-documented, but still a trap for the next module)

**Files**: `ingestion/landing_page_scraper.py:25-30` (429 and 403 both retryable) vs.
`pipeline/clients/replicate_client.py:39-41` (403 explicitly *not* retryable)

**What's wrong**: `landing_page_scraper.py`'s comment is explicit that this is
intentional: "confirmed (empirically) to be part of the same burst-triggered block...
not a permanent auth failure — deliberately different from
`pipeline/clients/replicate_client.py`'s `_is_retryable`... Do not 'fix' this back to
match that convention." `shopify_json.py` follows the same 429+403-retryable convention.
This is good self-documentation, not a bug — flagged here only because a fourth module
adding retry logic (there's no shared retry-policy abstraction) has a 50/50 chance of
picking the wrong convention for its own domain without reading this specific comment
first.

**Why it matters**: minor. Listed for completeness since the review was asked to look
for "inconsistent patterns between older and newer modules" — this is one, but it's an
intentional, well-justified one, not a defect.

**Fix direction**: none needed; consider a short note in `docs/blueprints/` or a
top-of-module comment convention that makes "this module's retry policy differs from
X on purpose" more discoverable than a single docstring.

---

## Summary table

| # | Finding | Kind | Severity |
|---|---|---|---|
| 1 | `ingestion/tests/` excluded from default `pytest` run | Test-infra gap | High — verified, no CI backstop |
| 2 | `--tiered`/`--workers` combinations silently resurrect a known-broken scrape path | Latent operational bug | High — real incident already happened once |
| 3 | Advertorial-fallback LLM escalation ignores its own free pre-pass's result | Cost bug | Medium-High |
| 4 | Stray top-level test scripts make live network calls, are pytest-collected | Hygiene + safety | Medium-High |
| 5 | `is_amazon_url` regex disagrees with `get_amazon_region`'s map | Latent correctness bug | Medium |
| 6 | `_MERGE_FILLABLE_FIELDS` missing 3 fields added after it was written | Latent data-loss bug | Medium |
| 7 | HTML-tag-stripping duplicated 4x | Duplication | Medium |
| 8 | LLM JSON-template/schema sync duplicated, root cause not fixed | Duplication (recurring bug class) | Medium |
| 9 | Rating range-check duplicated 3x (self-acknowledged) | Duplication | Low |
| 10 | Amazon checkout-widget handling duplicated across 2 files | Duplication / undocumented layering | Low-Medium |
| 11 | `variants_featured` formatting inconsistent across tiers | Data-consistency style issue | Low |
| 12 | 5 orchestration entry points, 2 superseded but unmarked | Dead/legacy code | Medium |
| 13 | `_extract_from_xhr` unverified since inception | Speculative dead-ish code | Low |
| 14 | Retry-status-code convention differs by module (intentional) | Style / discoverability | Low |
