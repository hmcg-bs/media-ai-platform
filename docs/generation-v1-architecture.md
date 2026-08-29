# Generation v1: Architecture Reference

**Scope**: `pipeline/generation/*.py` (13 modules) and the two clients it depends on
(`pipeline/clients/genai_client.py`, `pipeline/clients/replicate_client.py`). Companion
to [`docs/meta-ad-image-model-stack.md`](./meta-ad-image-model-stack.md) (the model-choice
research) and [`docs/generation-failure-modes.md`](./generation-failure-modes.md) (the
bug catalog) — this document describes what the system *does* end to end, as it exists
today. Tracked on [wayfinder map issue #36](https://github.com/hmcg-bs/media-ai-platform/issues/36).

**Method**: grounded in a full read of every module listed above, current as of Round 6
(2026-08-29) — not a design description of what was originally planned.

---

## What this is

Generation v1 is the **cold-start** path of the "Ideal Ad" product (see `CONTEXT.md`'s
**Generation** entry): given a client's raw product photo and a stated intention, it
synthesizes a new ad image from scratch, grounded in two independent sources of "what
works" — Critique's statistical models (Cox survival, SHAP) and real top-performing
Corpus ads (retrieval). It does **not** re-render an existing draft ad (that's a
separate, not-yet-built re-render path — see wayfinder issue #41).

The entry point is `pipeline.generation.pipeline.generate_cold_start_ad()`, invoked via
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
5. generate_background_and_product()   -- pipeline/generation/background.py + masking.py
   ┌─── regeneration loop (capped at MAX_REGENERATION_PASSES = 2) ────────────┐
   │ 6. plan_layout()                  -- pipeline/generation/layout.py       │
   │ 7. compose_ad()                   -- pipeline/generation/compositor.py   │
   │ 8. review_blend()                 -- pipeline/generation/blend.py        │
   │ 9. review_ad()                    -- pipeline/generation/reviewer.py     │
   │ 10. review_feature_fidelity()     -- pipeline/generation/feature_fidelity.py │
   │    if not passed and not exhausted: re-run step 5 with feedback folded  │
   │    into `intention`, then loop back to step 6                          │
   └──────────────────────────────────────────────────────────────────────┘
```

Note step 2 now depends on step 1's output (`guide`) — Round 7 added directive-alignment
scoring to reference-ad selection, so it needs the guide's directives to check candidate
ads against before step 3 ever runs.

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
post-generation fidelity check (section 10). That changes what "top" has to mean: a
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
**alignment score**, image bytes, dominant color, background style, hook framework). A
per-ad image fetch failure (stale Facebook CDN URL) is logged and skipped, never
raised — callers must handle receiving **fewer than `n`, including zero** (confirmed
live: a full run once saw 100% of 3,057 candidates 403 — see
[the Round 6 live-verification findings](./generation-failure-modes.md#round-6-live-verification-findings)) —
reconfirmed again live during Round 7 (same 100% failure rate).

### 3. `style_reference.py` — `derive_style_brief(guide)`

**Rescoped in Round 7.** This agent used to feed reference-ad *images* into a vision
call and ask it to independently read off "background treatment, color choices" —
dimensions the guide already has a rigorous, larger-sample SHAP/Cox answer for
(`dominant_color`, `background_style`, `contrast_ratio_type`, ...). Letting a vision
model re-derive an already-measured dimension from a handful of images risked a small,
uncontrolled qualitative sample overriding a real statistical one — exactly the
ungrounded-LLM-judgment failure mode this whole project is built to avoid wherever a
deterministic/statistical answer already exists (CLAUDE.md's own stated principle).

The agent is now **text-only** and does two things only:
1. **Translates** each measured, direction-reliable guide directive into concrete,
   renderable creative language, anchored to the directive as ground truth — the
   prompt states explicitly that the directives are "the authoritative source for any
   dimension they name," not something to independently re-derive.
2. **Fills `font_personality`** — the one dimension Step 2's extraction genuinely never
   measures (no font-family feature exists anywhere in the schema) — as its own
   qualitative judgment, kept visibly separate from the real statistical directives.

Reference-ad images aren't gone from the pipeline; they moved to the fidelity check
(section 10), which uses them to verify the output *after* generation instead of
(mis)informing what to generate beforehand.

### 4. `copywriter.py` — `draft_copy()`

One text-only `GenAIClient.extract_structured_text()` call producing `AdCopy` (headline,
secondary copy, CTA text, optional price/offer text), informed by the guide's
copy-style/hook-framework directives (framed explicitly as correlational signals, not
commands, so the model doesn't over-fit to one historical pattern). Rendering this text
into pixels is deterministic (`compositor.py`) — this agent decides *what it says*, never
how it's drawn.

### 5. `background.py` + `masking.py` — `generate_background_and_product()`

**The mechanism that changed most this session (Round 6).** Produces the single
background+product image every later step composites onto. As of Round 6:

```
product_photo_bytes
    │
    ▼
BackgroundRemoverClient.remove_background()   -- Replicate: 851-labs/background-remover
    │  (RGBA cutout; alpha channel marks the product)
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

Scene description comes from `_scene_description()`: prefers the `StyleBrief` (grounded
in real reference ads) outright over the guide-only description when one is available,
rather than merging two potentially-conflicting scene texts into one prompt. The fill
prompt carries three explicit numbered rules (no text of any kind, no depicting any
second bottle/jar/container anywhere in the fill, environment/props only) — see
[failure mode #6](./generation-failure-modes.md#6-anti-duplication-prompt-instruction-is-probabilistic-not-guaranteed)
for why rule 2 exists and its real, honest limits.

**Why this replaced Flux Kontext Pro** (the prior mechanism, client still present in
`replicate_client.py` as `FluxKontextClient`, unused on this path): Flux Kontext has no
mask input at all — every call re-renders the *entire* frame, product included, which
was silently garbling the product's own label text. See
[failure mode #5](./generation-failure-modes.md#5-flux-kontexts-whole-image-re-render-silently-garbled-product-label-text)
for the full root-cause chain. `FluxKontextClient` is kept, not deleted, as a candidate
fallback for the orchestration work scoped on wayfinder map
[#42](https://github.com/hmcg-bs/media-ai-platform/issues/42).

### 6. `layout.py` — `plan_layout()`

A Gemini vision call over the *actual* background+product image (not a static guess) —
fixes the original collision bug where a fixed-fraction default layout had no idea where
the product landed, so headline/body text visibly overlapped it. Returns a `LayoutPlan`:
`product_bbox` plus up to four text zones (headline/secondary/cta/price-offer), all as
0-1 fractions, guaranteed by the prompt not to overlap the product or each other. Treated
as a strong prior, not ground truth — `compositor.py` still clamps every box to the
canvas and never trusts it blindly (matching this project's `color_reported` vs.
`color_measured` discipline from ADR-008).

### 7. `compositor.py` — `compose_ad()`

Deterministic PIL assembly — no generative model call, per ADR-006's deterministic-first
principle. Draws each `ElementSpec` (headline, secondary copy, CTA, price offer) onto the
background+product image:

- **Shrink-to-fit text**: wraps and shrinks font size until the whole block fits *both*
  box dimensions (an original bug only checked width — see
  [failure mode](./generation-failure-modes.md) history in the module's own docstring).
- **Real bundled fonts**: loads TrueType files from `pipeline/generation/assets/fonts/`
  (DejaVu family) by absolute path, keyed by `FontPersonality`, raising loudly if a file
  is missing rather than silently falling back — see
  [failure mode #1](./generation-failure-modes.md#1-font-resolution-silently-fell-back-to-pils-tiny-bitmap-font).
- **WCAG auto-contrast**: `_ensure_legible_color()` samples the real background pixels
  under a text/button box and swaps to black/white if the requested color fails WCAG AA
  contrast — applied to both text color and, since Round 5, CTA fill color (see
  [failure mode #3](./generation-failure-modes.md#3-cta-fill-color-was-never-contrast-checked)).
- **Background bands**: an optional solid/semi-transparent rectangle behind text, so
  legibility never depends on a single sampled pixel being representative of a whole box
  that might straddle a busy background.
- **Drop shadows**: every text/CTA element composites a blurred shadow layer underneath
  itself first — a cheap depth cue added after the blend-review agent repeatedly flagged
  code-composited text as looking "flat" next to the photographic background.

### 8. `blend.py` — `review_blend()`

A narrowly-scoped agent, deliberately separate from `reviewer.py`: judges *only* whether
the composited text/CTA layer looks visually unified with the AI-generated background
(lighting, edge integration, shadow consistency, seams/halos) — never content, never
directive-adherence. Kept separate for the same "don't ask one model call to judge too
many unrelated things" reason that shaped the Stage 5 cognitive-extraction eval framework
elsewhere in this project.

### 9. `reviewer.py` — `review_ad()`

Rates the fully assembled ad against the `GenerationGuide`'s directives — for each one,
does the image visually follow it (e.g. `dominant_color=green: lower_is_better` → does
the ad avoid a green-dominant palette)? Also flags general visual-quality problems
(garbled text, a duplicated product, awkward cropping) and recommends regeneration only
for a real defect, not stylistic disagreement with a low-magnitude directive.

### 10. `feature_fidelity.py` — `review_feature_fidelity()`

**New in Round 7.** The comparison role reference ads were rescoped into: does the
*final*, fully-composited ad actually replicate the feature pattern the guide's
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
never block generation or fabricate a verdict.

### The regeneration loop

`generate_cold_start_ad()` runs steps 6-10 up to `MAX_REGENERATION_PASSES + 1` times.
`passed = review.overall_pass and blend_review.blends_well and
fidelity_review.overall_fidelity_pass` — all three gates must clear independently; any
one failing does not get short-circuited by the other two passing. On a
failed-but-not-exhausted pass, all three agents' feedback (whichever flagged a problem)
is folded into the `intention` string passed to a **fresh**
`generate_background_and_product()` call (a targeted re-edit, not a full restart from
the original product photo's original background) — the next loop iteration also
re-runs `plan_layout()`, since a re-edited frame may place the product differently.

---

## Client layer: which provider, which call

| Client | Provider | Used by | Notes |
|---|---|---|---|
| `GenAIClient` | Vertex Gemini | guide→copy, style brief, layout, blend, review (all of steps 3-4, 6, 8-9) | `extract_structured` (image+schema), `extract_structured_text` (text+schema), `extract_structured_multi_image` (N images+schema, Round 5) |
| `BackgroundRemoverClient` | Replicate (`851-labs/background-remover`, pinned version — a community model, 404s by bare name) | step 5 | Produces the RGBA alpha matte `masking.py` consumes |
| `FluxFillClient` | Replicate (`black-forest-labs/flux-fill-pro`, official, bare name) | step 5 | Masked inpaint — the Round 6 mechanism |
| `FluxKontextClient` | Replicate (`black-forest-labs/flux-kontext-pro`) | *unused* on this path since Round 6 | Kept as a fallback candidate, not deleted |

`GenAIClient.generate_image()` (Gemini-native image output, e.g. `gemini-2.5-flash-image`)
exists and is tested but is **not called anywhere in this pipeline** — it was evaluated
early on but the masked-inpaint mechanism above is what shipped for faithful
product-photo fidelity. `QwenLayersClient`, `QwenVLClient`, `EmbeddingClient`, and
`ReplicateVisionClient` (also in `replicate_client.py`) are used elsewhere in this
codebase (Step 2's Layer Decomposition, landing-page enrichment) — not by Generation v1.

---

## How this differs from Step 2's extraction pipeline

Worth naming explicitly, since it's the central open question on wayfinder map
[#42](https://github.com/hmcg-bs/media-ai-platform/issues/42) (the modular-harness
effort): Step 2's extraction pipeline (`pipeline/orchestrator.py`,
`pipeline/stages/base_stage.py`) threads one shared `PipelineContext` object through a
list of `BaseStage` instances, each wrapped in a uniform `try/except StageError` for
per-stage fallback. Generation v1 has **no equivalent** — each step above is a plain
function call with its own explicit parameter list, and the only existing
resilience/control mechanism is the capped regeneration loop described above (which
operates on the *whole* steps-6-through-9 block, not per-step) plus generic
`tenacity`-based retry-on-transient-failure inside each client's `_execute`/API call.
There is currently no per-call cost/latency instrumentation, no step-level fallback (e.g.
routing around a failed `FluxFillClient` call to `FluxKontextClient`), and no golden-set
quality evaluation for any of this pipeline's own agent judgment calls (style brief
plausibility, blend-quality correctness, guide-adherence correctness) — all three are the
explicit scope of map #42, not yet built.

---

## Files at a glance

```
pipeline/generation/
├── guide.py             Cox/SHAP models -> GenerationGuide (directional signals)
├── reference_ads.py      guide -> directive-aligned top-N real ads, images included (Round 7 rescope)
├── style_reference.py    guide -> StyleBrief (text-only; Round 7 dropped reference-ad images)
├── copywriter.py          Intention + guide -> AdCopy
├── background.py          StyleBrief + product photo -> background+product image (orchestrates masking.py)
├── masking.py             RGBA alpha matte -> Flux-Fill-convention inpaint mask (Round 6)
├── layout.py              Background+product image -> LayoutPlan (zones, not overlapping product)
├── elements.py            ElementSpec/AdSpec/FontPersonality data models
├── compositor.py          AdSpec -> final PNG bytes (deterministic PIL drawing)
├── blend.py               Visual-cohesion-only review of the composited result
├── reviewer.py            Guide-adherence + general quality review of the composited result
├── feature_fidelity.py    Final ad + reference ads -> does the output replicate the guide's pattern? (Round 7)
├── pipeline.py            generate_cold_start_ad() -- orchestrates all of the above
├── cli.py                 Local CLI entry point
└── assets/fonts/          Bundled DejaVu TrueType files (Round 5)
```
