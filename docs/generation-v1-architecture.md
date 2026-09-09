# Generation v1/v2: Architecture Reference

**Scope**: `pipeline/generation/*.py` (15 modules) and the two clients it depends on
(`pipeline/clients/genai_client.py`, `pipeline/clients/replicate_client.py`). Companion
to [`docs/meta-ad-image-model-stack.md`](./meta-ad-image-model-stack.md) (the model-choice
research) and [`docs/generation-failure-modes.md`](./generation-failure-modes.md) (the
bug catalog) — this document describes what the system *does* end to end, as it exists
today. Tracked on [wayfinder map issue #36](https://github.com/hmcg-bs/media-ai-platform/issues/36).

**Method**: grounded in a full read of every module listed above, current as of Round 9
/ Generation v2 (2026-08-30) — not a design description of what was originally planned.
The filename is kept as `generation-v1-architecture.md` for link stability (wayfinder map,
CONTEXT.md); the content below is the current (v2) architecture.

---

## What this is

Generation is the **cold-start** path of the "Ideal Ad" product (see `CONTEXT.md`'s
**Generation** entry): given a client's raw product photo and a stated intention, it
synthesizes a new ad image from scratch, grounded in two independent sources of "what
works" — Critique's statistical models (Cox survival, SHAP) and real top-performing
Corpus ads (retrieval). It does **not** re-render an existing draft ad (that's a
separate, not-yet-built re-render path — see wayfinder issue #41).

**Generation v2 (Round 9, 2026-08-30)** restructured the v1 flow around two live-confirmed
findings (`docs/generation-failure-modes.md` bugs #6 and #12):
1. The product's bounding box is knowable immediately after background-removal — Flux
   Fill only ever repaints the masked-out region, so the product never moves after that
   point (see `masking.py`). Layout can therefore be **computed once, deterministically,
   before generation ever runs**, from the guide's own statistical layout directives —
   instead of asking a vision model to search an already-generated frame for empty space,
   once per regeneration attempt.
2. Duplicate-product hallucination (Flux Fill occasionally painting a second, garbled
   copy of the product into the open background) is real and probabilistic. Rather than
   hoping the general review agents catch it, it's now **checked directly**
   (deterministic-first, escalating to a vision call only when ambiguous) and
   **surgically repaired** — a targeted re-fill of just the hallucinated region, not a
   full from-scratch regeneration.

The entry points are `pipeline.generation.pipeline.generate_cold_start_ad()` (one ad) and
`generate_cold_start_ad_variants()` (N candidate ads for human selection), invoked via
the local CLI (`pipeline/generation/cli.py`):

```bash
uv run python -m pipeline.generation.cli \
    --product-photo path/to/product.jpg \
    --intention "Energizing pre-workout for young, active adults" \
    --product-name "Surge Pre-Workout" \
    --out out/generated_ad.png
```

---

## The step chain

`generate_cold_start_ad()` runs these steps in order. Each is a plain function call
(there is no shared `PipelineContext`/`BaseStage` object the way Step 2's extraction
pipeline has one — see "How this differs from Step 2" below), threading explicit
parameters between calls.

```
1. extract_generation_guide()          -- pipeline/generation/guide.py
2. get_top_reference_ads(guide)        -- pipeline/generation/reference_ads.py
3. derive_style_brief(guide)           -- pipeline/generation/style_reference.py
4. draft_copy()                        -- pipeline/generation/copywriter.py
5. compute_product_bbox()              -- pipeline/generation/masking.py       (no API call)
   plan_layout_from_guide()            -- pipeline/generation/layout_planner.py (no API call, ONCE)
6. generate_background_and_product()   -- pipeline/generation/background.py + masking.py
   ┌─── regeneration loop (capped at MAX_REGENERATION_PASSES = 2) ────────────────────┐
   │   ┌─── surgical-repair sub-loop (capped at MAX_SURGICAL_REPAIR_ATTEMPTS = 2) ───┐ │
   │   │ 7. detect_duplicate_product()  -- pipeline/generation/duplicate_detection.py│ │
   │   │    if duplicate_detected: repair_duplicate_region(), re-check              │ │
   │   └────────────────────────────────────────────────────────────────────────────┘ │
   │ 8. validate_layout()              -- pipeline/generation/layout.py               │
   │ 9. compose_ad()                   -- pipeline/generation/compositor.py           │
   │ 10. review_blend()                -- pipeline/generation/blend.py                │
   │ 11. review_ad()                   -- pipeline/generation/reviewer.py             │
   │ 12. review_feature_fidelity()     -- pipeline/generation/feature_fidelity.py     │
   │    if not passed and not exhausted: re-run step 6 with feedback folded          │
   │    into `intention`, then loop back to step 7 -- the layout_plan from step 5    │
   │    is NOT recomputed; it stays fixed for the whole run                          │
   └──────────────────────────────────────────────────────────────────────────────────┘
```

Note step 2 now depends on step 1's output (`guide`) — Round 7 added directive-alignment
scoring to reference-ad selection, so it needs the guide's directives to check candidate
ads against before step 3 ever runs. Step 5 depends on step 4's output (`ad_copy`) only
for one boolean (`has_price_offer = ad_copy.price_offer_text is not None`), used to decide
whether a price-offer zone is reserved.

### 1. `guide.py` — `extract_generation_guide()`

Turns the *already-trained* Critique models (Cox survival for `days_active`, XGBoost for
`collation_count`/`variants_featured_count`, and the composite success-score SHAP model
— all built on wayfinder map #1, not this map) into a structured, interpretable
`GenerationGuide`: lists of `DirectionalSignal` (dimension, value, `higher_is_better` /
`lower_is_better` / `unknown`, magnitude, source), bucketed into `visual_directives`,
`copy_style_directives`, `positioning_context`, plus `non_directional_signals` (matters,
but no reliable direction) and `excluded_notes` (dropped, with why).

Two real filters, both load-bearing:
- **`_unknown`/`_None` category levels are dropped entirely** — these mean "this ad has
  no Step 2 `creative_features`" (a coverage gap), not "an unknown value causes success."
- **SHAP direction reliability gate** (`MIN_DIRECTION_RELIABILITY = 0.10`): a feature's
  `abs(mean_signed_shap) / mean_abs_shap` must clear this bar before its direction is
  asserted; below it, the feature is kept only as a non-directional note. This is what
  keeps an unstable feature like `rating` (confirmed to flip sign across ablations
  elsewhere in this project) from being asserted a direction it doesn't reliably have.

Reads from `data/model_training_report_fresh.json` and `data/success_score_report_fresh.json`
by default — both pre-computed artifacts from wayfinder map #1's training runs, not
recomputed here.

### 2. `reference_ads.py` — `get_top_reference_ads(guide, n=3)`

**Rescoped in Round 7** (2026-08-29): reference ads no longer feed the generation
prompt at all (see section 3) — they're now ground-truth exemplars for the
post-generation fidelity check (section 12). That changes what "top" has to mean: a
reference ad is only useful for checking replication if it's a genuine embodiment of
the guide's own directives, not merely a high scorer for unrelated reasons (price,
brand recognition, small-sample luck). `_directive_alignment_score(row, guide)` scores
each candidate ad: **+1** for every categorical guide directive whose `higher_is_better`
value the ad's own extracted feature actually has, **-1** for every directive whose
*discouraged* (`lower_is_better`) value it has. Candidates are ranked by
`(alignment_score desc, composite_success_score desc)` and filtered to
`alignment_score >= min_alignment` (default 1 — "can't just be a random ad"), relaxed
back to 0 automatically (logged) if too few real ads clear that bar, since a genuine
corpus-coverage gap must degrade gracefully rather than returning nothing to compare
against.

Each successfully-fetched ad becomes a `ReferenceAd` (id, composite score,
**alignment score**, image bytes, dominant color, background style, hook framework).
`_is_url_expired()` decodes each URL's own signed expiry (a Facebook CDN `oe` query
param) and skips a known-dead candidate before ever attempting a fetch — free, no
network call. A per-ad fetch failure (any other cause) is logged and skipped, never
raised — callers must handle receiving **fewer than `n`, including zero**: confirmed
live, repeatedly (most recently in Round 9's own live smoke test — all 244 checked
candidates were expired, `n=0` returned), that the corpus's Facebook CDN URLs are
essentially always expired by the time this runs — see
[failure mode #10](./generation-failure-modes.md#10-reference-ad-image-urls-are-cryptographically-expired-not-blocked-or-rate-limited),
with the durable fix (re-scraping fresh URLs) currently blocked on an external Apify
account usage limit, not on anything in this codebase.

### 3. `style_reference.py` — `derive_style_brief(guide, variant_hint=None)`

**Rescoped in Round 7.** This agent used to feed reference-ad *images* into a vision
call and ask it to independently read off "background treatment, color choices" —
dimensions the guide already has a rigorous, larger-sample SHAP/Cox answer for
(`dominant_color`, `background_style`, `contrast_ratio_type`, ...). Letting a vision
model re-derive an already-measured dimension from a handful of images risked a small,
uncontrolled qualitative sample overriding a real statistical one — exactly the
ungrounded-LLM-judgment failure mode this whole project is built to avoid wherever a
deterministic/statistical answer already exists (CLAUDE.md's own stated principle).

The agent is text-only and does two things only:
1. **Translates** each measured, direction-reliable guide directive into concrete,
   renderable creative language, anchored to the directive as ground truth — the
   prompt states explicitly that the directives are "the authoritative source for any
   dimension they name," not something to independently re-derive.
2. **Fills `font_personality`** — the one dimension Step 2's extraction genuinely never
   measures (no font-family feature exists anywhere in the schema) — as its own
   qualitative judgment, kept visibly separate from the real statistical directives.

**`variant_hint` (Round 9, Generation v2)**: an optional string folded into the prompt,
asking for different incidental creative wording than a multi-variant batch's other
calls — explicitly scoped to never override a directive-covered dimension (the prompt
says so directly: "vary wording only, never the directives"). `generate_cold_start_ad_variants()`
also overrides `font_personality` directly, by rotation, after this call returns —
not relying on incidental LLM stochasticity to guarantee real visual diversity across
variants.

Reference-ad images aren't gone from the pipeline; they moved to the fidelity check
(section 12), which uses them to verify the output *after* generation instead of
(mis)informing what to generate beforehand.

### 4. `copywriter.py` — `draft_copy(variant_hint=None)`

One text-only `GenAIClient.extract_structured_text()` call producing `AdCopy` (headline,
secondary copy, CTA text, optional price/offer text), informed by the guide's
copy-style/hook-framework directives (framed explicitly as correlational signals, not
commands, so the model doesn't over-fit to one historical pattern). Rendering this text
into pixels is deterministic (`compositor.py`) — this agent decides *what it says*, never
how it's drawn. `variant_hint` (Round 9) works the same way as `style_reference.py`'s —
asks for a different hook/phrasing angle, never permission to ignore a directive.

### 5. `masking.py` + `layout_planner.py` — pre-generation layout, no API call

**New in Round 9 (Generation v2).** Two pure-geometry steps, run once, before any image
is generated:

```
cutout = BackgroundRemoverClient.remove_background(product_photo_bytes)  -- same call step 6 needs anyway
product_bbox = masking.compute_product_bbox(cutout)                     -- numpy over the alpha channel
layout_plan = layout_planner.plan_layout_from_guide(product_bbox, guide, has_price_offer=...)
```

`compute_product_bbox()`: the product's bounding box read straight off the alpha cutout
that step 6 needs anyway for the inpaint mask — no extra API call, and the resulting
`cutout` bytes are passed into step 6 directly so it isn't fetched twice.

`plan_layout_from_guide()`: computes the empty canvas region opposite the product (a
horizontal band above/below it if the product spans nearly the full width, otherwise a
vertical strip on whichever side the product ISN'T on), then stacks
headline/secondary-copy/CTA zones within it (a documented 40/40/20 height split), with
the guide's own `headline_zone` directive deciding whether the headline anchors first or
last in that stack. A `price_offer_zone` is reserved only when a genuinely separate
corner of empty space remains beyond the main stack. Degrades to a fixed bottom-band
layout if the computed empty region is too small to be usable (rare — mask feathering
already keeps a real margin around the product). Returns the same `LayoutPlan` model
`layout.py` has always used — this is a new *producer* of that model, not a new schema.

This `layout_plan` is threaded through the whole run unchanged: into step 6's prompt (as
`keep_clear_zones`), into `_layout_from_plan()`'s `ElementSpec` conversion at step 9, and
is **never recomputed** on a regeneration retry, unlike Generation v1's per-attempt
vision search.

### 6. `background.py` + `masking.py` — `generate_background_and_product()`

Produces the single background+product image every later step composites onto.

```
product_photo_bytes, cutout (from step 5, reused)
    │
    ▼
masking.build_inpaint_mask()                  -- local PIL, no API call
    │  (binarize -> dilate by a small safety margin -> invert -> feather the edge)
    │  black = preserve product pixels verbatim, white = Flux Fill may regenerate
    ▼
FluxFillClient.inpaint()                      -- Replicate: black-forest-labs/flux-fill-pro
    │  (masked inpaint: only white-mask pixels are touched)
    ▼
background_and_product_image (bytes)
```

Scene description comes from `_scene_description()`: prefers the `StyleBrief` outright
over the guide-only description when one is available, rather than merging two
potentially-conflicting scene texts into one prompt. The fill prompt carries the
numbered no-text/no-duplicate rules (see
[failure mode #6](./generation-failure-modes.md#6-anti-duplication-prompt-instruction-is-probabilistic-not-guaranteed)
and [#11](./generation-failure-modes.md#11-flux-fill-rendered-a-full-ghost-ad-copy-paragraph-echoing-the-intention-text-verbatim))
followed by, when `keep_clear_zones` is passed (Round 9), an explicit instruction to keep
those specific regions — bucketed into human-readable labels like "upper-right area" plus
percentage bounds, since a text-conditioned diffusion model follows verbal region
descriptions more reliably than raw coordinates — visually simple, since text will be
placed there.

**`repair_duplicate_region()` (new in Round 9)**: the surgical-repair counterpart, called
from the regeneration loop (step 7) when a duplicate is detected. Builds a mask via
`masking.build_targeted_erase_mask()` (black everywhere, a feathered white patch over just
the flagged region) and re-runs `FluxFillClient.inpaint()` against the **current** (flawed)
image with a short "erase and blend seamlessly" prompt — a smaller, targeted edit instead
of a full from-scratch regeneration. See
[failure mode #13](./generation-failure-modes.md#13-surgical-repair-can-reintroduce-a-new-duplicate-inside-the-erased-region)
for the live-confirmed limits of this mechanism.

**Why masked inpainting over Flux Kontext Pro** (the prior mechanism, client still
present in `replicate_client.py` as `FluxKontextClient`, unused on this path): Flux
Kontext has no mask input at all — every call re-renders the *entire* frame, product
included, which was silently garbling the product's own label text. See
[failure mode #5](./generation-failure-modes.md#5-flux-kontexts-whole-image-re-render-silently-garbled-product-label-text)
for the full root-cause chain.

### 7. `duplicate_detection.py` — `detect_duplicate_product()`

**New in Round 9.** Checks the *finished* background+product image for a hallucinated
second product instance, deterministic-first:

1. Re-runs `BackgroundRemoverClient.remove_background()` on the finished image (a general
   foreground segmenter, so it tends to segment both the real product and any
   hallucinated duplicate as separate blobs) and finds connected components
   (`scipy.ndimage.label`) on the binarized alpha.
2. One blob should land almost exactly on the already-known `product_bbox` from step 5
   (masking structurally guarantees those pixels are untouched) — excluded via a
   near-exact position match, not a fuzzy one.
3. **No other significant blob** → `detection_method="none"`, no vision call, no
   duplicate.
4. **Exactly one other blob, closely matching the product's own size/aspect ratio** →
   `detection_method="deterministic"`, flagged directly — the clear cases shouldn't cost
   a vision call.
5. **Anything messier** (an odd shape, or multiple candidates) → escalates to one
   `GenAIClient.extract_structured_multi_image()` call (original product photo + the
   generated image), asking directly whether a duplicate is present and where.

Returns a `DuplicateDetectionResult` (`duplicate_detected`, `duplicate_bbox`,
`detection_method`, `notes`). Called from the regeneration loop in a capped sub-loop
(`MAX_SURGICAL_REPAIR_ATTEMPTS = 2`): on a detection, `repair_duplicate_region()` runs and
the check re-runs against the repaired image; on `MAX_SURGICAL_REPAIR_ATTEMPTS` reached
with the duplicate still present, control falls through to the outer regeneration loop's
existing full-regen fallback (which, per
[failure mode #13](./generation-failure-modes.md#13-surgical-repair-can-reintroduce-a-new-duplicate-inside-the-erased-region),
is a real, live-confirmed necessity, not a theoretical one).

### 8. `layout.py` — `validate_layout()`

**Repurposed in Round 9.** Generation v1's `plan_layout()` asked a vision model to look
at an already-generated frame and *find* empty zones from scratch — replaced by step 5's
deterministic planning. What remains is a cheaper check: does this specific generated
frame still respect the *already-decided* plan (no part of the product, or any
hallucinated object, extends into a reserved zone)? Returns a `LayoutValidation`
(`zones_respected: bool`, `notes: str`).

**Important, live-confirmed nuance**: `zones_respected` is recorded in
`GenerationResult.layout_validation_history` for every attempt but is **not** currently
one of the three gates deciding whether to regenerate (`review.overall_pass`,
`blend_review.blends_well`, `fidelity_review.overall_fidelity_pass` — unchanged from v1).
A Round 9 live run returned `zones_respected=False` ("the bottle pouring liquid, the
glass, and the small treats all extend into the defined zones") on a pass that still
otherwise passed and stopped the loop — the scene bleeding into the reserved zone didn't
actually harm legibility, because the compositor's background-band mechanism (see
`compositor.py` below) already renders text on a solid/semi-transparent surface
regardless of what's behind it. This was a deliberate scope decision in the approved
plan (keep validation as an observability signal, not a fourth gate) but is worth
knowing: a `zones_respected=False` result alone will not currently trigger a repair or
regeneration.

### 9. `compositor.py` — `compose_ad()`

Deterministic PIL assembly — no generative model call, per ADR-006's deterministic-first
principle. Draws each `ElementSpec` (headline, secondary copy, CTA, price offer) onto the
background+product image, using the zones from step 5's `layout_plan` (via
`_layout_from_plan()`):

- **Shrink-to-fit text**: wraps and shrinks font size until the whole block fits *both*
  box dimensions.
- **Real bundled fonts**: loads TrueType files from `pipeline/generation/assets/fonts/`
  (DejaVu family) by absolute path, keyed by `FontPersonality`, raising loudly if a file
  is missing rather than silently falling back — see
  [failure mode #1](./generation-failure-modes.md#1-font-resolution-silently-fell-back-to-pils-tiny-bitmap-font).
- **WCAG auto-contrast**: `_ensure_legible_color()` samples the real background pixels
  under a text/button box and swaps to black/white if the requested color fails WCAG AA
  contrast — applied to both text color and CTA fill color (see
  [failure mode #3](./generation-failure-modes.md#3-cta-fill-color-was-never-contrast-checked)).
- **Background bands**: an optional solid/semi-transparent rectangle behind text — a
  robust legibility fallback confirmed live in Round 9 to absorb scene elements that
  bleed into a reserved zone despite the pre-generation keep-clear prompting (see step 8
  above), not just a stylistic choice.
- **Feathered edges**: `_composite_feathered_shape` blurs the band/CTA shape's own alpha
  channel before compositing, so it reads as part of the image rather than a pasted-on
  sticker — see
  [failure mode #12](./generation-failure-modes.md#12-compositors-text-band--cta-had-hard-geometric-edges-read-as-a-pasted-on-sticker).
- **Drop shadows**: every text/CTA element composites a blurred shadow layer underneath
  itself first — a cheap depth cue.

### 10. `blend.py` — `review_blend()`

A narrowly-scoped agent, deliberately separate from `reviewer.py`: judges *only* whether
the composited text/CTA layer looks visually unified with the AI-generated background
(lighting, edge integration, shadow consistency, seams/halos) — never content, never
directive-adherence.

### 11. `reviewer.py` — `review_ad()`

Rates the fully assembled ad against the `GenerationGuide`'s directives — for each one,
does the image visually follow it (e.g. `dominant_color=green: lower_is_better` → does
the ad avoid a green-dominant palette)? Also flags general visual-quality problems
(garbled text, a duplicated product, awkward cropping) and recommends regeneration only
for a real defect, not stylistic disagreement with a low-magnitude directive.

### 12. `feature_fidelity.py` — `review_feature_fidelity()`

Does the *final*, fully-composited ad actually replicate the feature pattern the guide's
statistics found, checked against the real, directive-aligned exemplars
`reference_ads.py` selected — rather than assumed from having generated "in the
direction of" the directives. One `GenAIClient.extract_structured_multi_image()` call:
the reference-ad images plus the final ad image (last in the list), asked to judge,
per checkable directive, whether the generated ad visually replicates the same trait
the reference ads share. Returns a `FeatureFidelityReview` (`replicated_directives`,
`missed_directives`, `overall_fidelity_pass`, plus a `checked` flag).

Degrades gracefully when `reference_ads` is empty (corpus image staleness, same
failure mode section 2 describes): returns `checked=False, overall_fidelity_pass=True`
without calling the model at all — there's nothing to compare against, and that must
never block generation or fabricate a verdict. Confirmed live in every Round 9 smoke
test: with `n=0` reference ads (corpus fully expired), fidelity was correctly reported
`checked=False, overall_fidelity_pass=True` rather than fabricating a fail.

### The regeneration loop

`generate_cold_start_ad()` runs steps 7-12 up to `MAX_REGENERATION_PASSES + 1` times.
`passed = review.overall_pass and blend_review.blends_well and
fidelity_review.overall_fidelity_pass` — all three gates must clear independently; any
one failing does not get short-circuited by the other two passing. On a
failed-but-not-exhausted pass, all three agents' feedback (whichever flagged a problem)
is folded into the `intention` string passed to a **fresh**
`generate_background_and_product()` call (a targeted re-edit, not a full restart from
the original product photo). Unlike Generation v1, **the loop does not re-plan layout on
retry** — `layout_plan` from step 5 is reused unchanged, since it was derived from
geometry (the product's bbox never changes) and the guide (also unchanged), not from
whatever a specific re-edited frame happens to look like. This removes a real source of
instability Generation v1 had: each retry's fresh vision-based layout search could
produce wildly different zones for reasons unrelated to the actual defect being fixed.

### Multi-variant generation — `generate_cold_start_ad_variants()`

**New in Round 9.** Generates `n_variants` (default 3) full candidate ads for human
selection — the user's own confirmed decision: return all variants, never auto-pick one.
`guide`, `reference_ads`, and the geometry-driven `product_bbox`/`layout_plan` (steps 1,
2, 5) are computed **once** and shared across every variant, since these are the guide's
real statistical findings, not "options" to vary. Only the qualitative,
statistically-unmeasured dimensions vary per variant: each gets its own `style_brief`
(via `variant_hint`, plus an explicit `font_personality` rotation across the full
`FontPersonality` set — `clean_modern`, `bold_condensed`, `elegant_serif`,
`playful_dynamic` — so diversity doesn't depend on incidental LLM stochasticity) and its
own `ad_copy` (via the same `variant_hint` mechanism). Each variant still runs its own
full `generate_cold_start_ad()` call (background generation, duplicate detection,
compositing, and review loop) — those steps can't be shared, since the visual content
genuinely differs per variant.

**Cost note, confirmed live**: this multiplies every expensive step (Flux Fill
inpainting, every vision call in the regeneration loop) by `n_variants` — only the guide/
geometry steps above are shared. A live 3-variant run confirmed `BackgroundRemoverClient.remove_background()`
was called exactly once against the *original* product photo across the whole batch
(the identical `layout_plan` — not just equal, the same computed values — appeared in
all three results), while it was still separately re-invoked per variant per
regeneration attempt for duplicate-detection's own re-check of each variant's distinct
generated image, as designed.

---

## Client layer: which provider, which call

| Client | Provider | Used by | Notes |
|---|---|---|---|
| `GenAIClient` | Vertex Gemini | guide→copy, style brief, duplicate-vision escalation, layout validation, blend, review, fidelity (steps 3-4, 7-8, 10-12) | `extract_structured` (image+schema), `extract_structured_text` (text+schema), `extract_structured_multi_image` (N images+schema) |
| `BackgroundRemoverClient` | Replicate (`851-labs/background-remover`, pinned version — a community model, 404s by bare name) | step 5 (product bbox), step 6 (mask), step 7 (duplicate re-check, per attempt) | Produces the RGBA alpha matte `masking.py` consumes; called more than once per run in Generation v2 |
| `FluxFillClient` | Replicate (`black-forest-labs/flux-fill-pro`, official, bare name) | step 6 (background fill), step 7 (surgical repair) | Masked inpaint; `guidance` param (default 85) controls prompt-adherence strength |
| `FluxKontextClient` | Replicate (`black-forest-labs/flux-kontext-pro`) | *unused* on this path since Round 6 | Kept as a fallback candidate, not deleted |

`GenAIClient.generate_image()` (Gemini-native image output, e.g. `gemini-2.5-flash-image`)
exists and is tested but is **not called anywhere in this pipeline**. `QwenLayersClient`,
`QwenVLClient`, `EmbeddingClient`, and `ReplicateVisionClient` (also in
`replicate_client.py`) are used elsewhere in this codebase — not by Generation.

---

## How this differs from Step 2's extraction pipeline

Worth naming explicitly, since it's the central open question on wayfinder map
[#42](https://github.com/hmcg-bs/media-ai-platform/issues/42) (the modular-harness
effort): Step 2's extraction pipeline (`pipeline/orchestrator.py`,
`pipeline/stages/base_stage.py`) threads one shared `PipelineContext` object through a
list of `BaseStage` instances, each wrapped in a uniform `try/except StageError` for
per-stage fallback. Generation has **no equivalent** — each step above is a plain
function call with its own explicit parameter list, and the only existing
resilience/control mechanisms are the capped regeneration loop, the capped surgical-
repair sub-loop (both described above), and generic `tenacity`-based retry-on-transient-
failure inside each client's `_execute`/API call (confirmed live in Round 9: a
`ModelError` from Flux Fill's own upstream infra, distinct from an HTTP 429/5xx, is
correctly *not* retried by this layer and propagates — by design, since a model-level
error usually isn't transient the way a rate limit is). There is currently no per-call
cost/latency instrumentation, no step-level fallback (e.g. routing around a failed
`FluxFillClient` call to `FluxKontextClient`), and no golden-set quality evaluation for
any of this pipeline's own agent judgment calls — all three are the explicit scope of
map #42, not yet built.

---

## Files at a glance

```
pipeline/generation/
├── guide.py               Cox/SHAP models -> GenerationGuide (directional signals)
├── reference_ads.py        guide -> directive-aligned top-N real ads, images included (Round 7 rescope)
├── style_reference.py      guide -> StyleBrief (text-only; variant_hint for multi-variant diversity)
├── copywriter.py            Intention + guide -> AdCopy (variant_hint for multi-variant diversity)
├── masking.py               RGBA alpha matte -> inpaint mask (Round 6); product bbox + targeted erase mask (Round 9)
├── layout_planner.py        product bbox + guide -> LayoutPlan, deterministic, no API call (Round 9)
├── background.py            StyleBrief + product photo -> background+product image; surgical repair (Round 9)
├── duplicate_detection.py   background+product image -> is a duplicate product present, and where? (Round 9)
├── layout.py                LayoutPlan + generated image -> LayoutValidation, "does the plan still hold?" (Round 9 repurpose)
├── elements.py              ElementSpec/AdSpec/FontPersonality data models
├── compositor.py            AdSpec -> final PNG bytes (deterministic PIL drawing)
├── blend.py                 Visual-cohesion-only review of the composited result
├── reviewer.py              Guide-adherence + general quality review of the composited result
├── feature_fidelity.py      Final ad + reference ads -> does the output replicate the guide's pattern?
├── pipeline.py              generate_cold_start_ad() + generate_cold_start_ad_variants() (Round 9)
├── cli.py                   Local CLI entry point
└── assets/fonts/            Bundled DejaVu TrueType files (Round 5)
```
