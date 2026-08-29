# Generation v1: Known Failure Modes

Catalog of confirmed bugs found and fixed in the ad-image generation pipeline
(`pipeline/generation/*.py`, `pipeline/clients/genai_client.py`,
`pipeline/clients/replicate_client.py`), found via live generation runs and direct pixel
inspection rather than code-only review. Each entry: symptom, root cause, fix, and where
it was confirmed live. Mirrors [`docs/extraction-failure-modes.md`](./extraction-failure-modes.md)'s
own discipline — update this file whenever a new failure mode is root-caused; it's the
durable record a future round should check first, before re-discovering the same bug.

Source: [wayfinder map issue #36](https://github.com/hmcg-bs/media-ai-platform/issues/36).
See its "Round 1" through "Round 6" entries for the full narrative; this file extracts
just the reusable bug-catalog content in the same terse format as the extraction catalog.

---

## 1. Font resolution silently fell back to PIL's tiny bitmap font

**Symptom**: Every generated ad, across every round before the fix, used the same flat,
low-quality typeface — no headline/body distinction, no personality, regardless of what
any upstream agent or guide directive suggested.

**Root cause**: `compositor.py::_load_font` called `ImageFont.truetype("DejaVuSans-Bold.ttf", size)`
with a *bare filename*. This platform's font resolver cannot locate a bare filename
(confirmed live: every single call raised `OSError`), and the calling code silently
caught the exception and fell through to `ImageFont.load_default()` — PIL's tiny built-in
bitmap font, not a real scalable typeface at all. This had been true for every ad
generated all session, not just the round it was caught in.

**Fix**: Bundled real DejaVu TrueType files directly into the repo
(`pipeline/generation/assets/fonts/`, copied from matplotlib's own bundled copy — free,
redistributable, see `LICENSE_DEJAVU` there) and load by absolute path via
`_FONTS_DIR / _FONT_FILES[personality]`. Added a `FontPersonality` axis
(`clean_modern` / `bold_condensed` / `elegant_serif` / `playful_dynamic`), each mapped to
a distinct bundled file, assigned per-ad by the style-reference agent from real reference
ads. A missing bundled file now raises loudly instead of silently degrading.

**Regression test**: `pipeline/tests/test_generation_compositor.py::TestFontPersonality`

**Confirmed live**: v6→v7 smoke test comparison — v7 shows a real bold-condensed
typeface, v6 and every prior round do not.

---

## 2. Negative (`lower_is_better`) guide directives were silently dropped

**Symptom**: Every generated ad used a plain white/studio background, regardless of what
the statistical model actually found about what wins in this category.

**Root cause**: `background.py::_guide_to_scene_description` only ever surfaced
`higher_is_better` signals into the scene-description prompt. When no positive signal
matched, it fell through to a hardcoded default of `"a clean, high-contrast studio
background"` — directly contradicting the real, already-computed finding that
`background_style=Studio` is `lower_is_better` for this category. The negative signal
existed in the guide the whole time; it was just never read.

**Fix**: Rewrote the function to surface `avoid X` instructions explicitly alongside
`use Y` ones, and replaced the fallback with an explicit non-studio, real-world-scene
instruction (`_LAST_RESORT_FALLBACK`) that is never allowed to describe a plain/studio
look.

**Confirmed live before fixing** (not assumed): queried the top-8 real ads by composite
success score directly — **100% `background_style=Busy`, zero `Studio`** — establishing
this was a real, data-contradicting defect, not a stylistic choice.

**Regression test**: `pipeline/tests/test_generation_background.py::TestGuideToSceneDescription::test_negative_signal_becomes_explicit_avoid_instruction`

---

## 3. CTA fill color was never contrast-checked

**Symptom**: A CTA button occasionally rendered in a color that blended into the
background behind it, reducing clickability — flagged directly by the blend-review
agent ("blends into the background").

**Root cause**: `compositor.py::_draw_cta_element` used a hardcoded `#1a1a1a` fill
regardless of what was actually behind it, and derived the button's *text* color from a
fixed value independent of whatever fill ended up rendered.

**Fix**: Extended `_ensure_legible_color()` (already built for body-text contrast) to
also gate the CTA fill color, then derive the button's text color from the fill's *final,
already-corrected* value rather than a value chosen independently of it.

**Regression test**: `pipeline/tests/test_generation_compositor.py::TestCtaAutoContrast`

**Confirmed live**: re-ran the pipeline after the fix — CTA fill/text pairs auto-correct
to a legible combination against the actual rendered background in every subsequent run.

---

## 4. Pandas NaN-truthiness silently passed stale rows through the reference-ad filter

**Symptom**: `get_top_reference_ads()` occasionally returned ads with missing
`dominant_color`/`background_style` fields as if they were valid, real values.

**Root cause**: `compute_composite_success_score()` round-trips rows through a pandas
`DataFrame`; any row missing a column present in *other* rows gets `NaN` filled in for
it. `bool(float('nan'))` is `True` in Python, so a plain `if r.get("dominant_color")`
check let a `NaN`-valued row through as "truthy present," not caught by the filter meant
to exclude incomplete rows.

**Fix**: Changed the filter to `isinstance(r.get("dominant_color"), str)` (and same for
`background_style`) — `NaN` is a `float`, never a `str`, so this check is immune to the
truthiness trap regardless of which columns happen to be NaN-filled on a given row.

**Regression test**: `pipeline/tests/test_generation_reference_ads.py`

---

## 5. Flux Kontext's whole-image re-render silently garbled product label text

**Symptom**: The guide-adherence reviewer's own output said outright — *"Text on the
product label is blurry and illegible... small icons and text at the bottom of the
bottle are unreadable."* Every ad generated through Round 5 had this defect to some
degree; it had been noted as a "known limitation" before being root-caused.

**Root cause**: Flux Kontext Pro (the background/product mechanism used through Round 5)
has no mask input at all — confirmed against its own Replicate schema
(`prompt`, `input_image`, `aspect_ratio`, `output_format` only). Every edit call
re-renders the **entire** frame, product included. A prompt instruction telling the model
to "keep the label exactly as shown" can only ever *ask* a diffusion model not to touch
something — it cannot structurally prevent it, and diffusion models are well known to be
unreliable at faithfully re-rendering small legible text.

**Fix**: Replaced the whole-image edit with a masked inpaint (Round 6). A new
`BackgroundRemoverClient` (`851-labs/background-remover`) produces an alpha matte of the
product; `masking.py::build_inpaint_mask()` turns that into a mask in Flux Fill's own
documented convention (its schema states plainly: *"Black areas will be preserved while
white areas will be inpainted"*); a new `FluxFillClient`
(`black-forest-labs/flux-fill-pro`) regenerates only the white (background) region. The
product's own pixels — including every word of label text — are never sent through the
diffusion process at all, so they return byte-identical to the source photo.

**Regression test**: `pipeline/tests/test_generation_masking.py`,
`pipeline/tests/test_generation_background.py::TestGenerateBackgroundAndProduct`

**Confirmed live, repeatedly**: zoomed crops of the masked/protected product region
across three separate generation runs (isolated tests and the full end-to-end run) all
show pixel-perfect label text — including fine print and a small country-of-origin flag
graphic — even in a run where *other* parts of the image had real defects (see #6 below).

---

## 6. Anti-duplication prompt instruction is probabilistic, not guaranteed

**Symptom**: The open (inpainted) region of the background occasionally contains a
second, smaller, garbled copy of the product — mangled label text, sometimes an
unrelated nonsense badge/graphic near it.

**Root cause**: Masking (see #5) deterministically protects the pixels it's given, but
does nothing to stop the model from *inventing a new instance* of "a labeled product
bottle" elsewhere in the region it's free to fill — ad-style product photography is
exactly the kind of content Flux Fill is biased toward generating. The first version of
the fill prompt only banned generating *text*, not a second product; it wasn't tested
against this specific failure mode until it was observed live.

**Fix (partial, not a full guarantee)**: Rewrote the fill prompt into three explicit
numbered rules, the second of which states directly: *"there is already exactly one
product in this image and it must remain the only one, never duplicated, echoed, or
repeated anywhere in the background."* This measurably reduced the failure rate — three
isolated test runs after the fix came out clean — but is **not deterministic**, since it
is still a text-only negative instruction to a generative model. Confirmed live: the
final full end-to-end verification run (Round 6, `smoke_generated_ad_v11.png`) still
produced a duplicate, garbled second bottle despite the strengthened prompt being live in
that run.

**Regression test**: `pipeline/tests/test_generation_background.py::TestGenerateBackgroundAndProduct::test_prompt_forbids_text_in_the_generated_scene`
(checks the prompt text itself, not generation output — there is no practical way to
regression-test a diffusion model's stochastic compliance with a negative instruction).

**Status**: open, documented rather than silently accepted. A genuinely deterministic fix
would need either a second masking pass specifically excluding "product-shaped" regions
of the fill, or a step-level orchestration/retry policy — exactly the kind of gap
wayfinder map [#42](https://github.com/hmcg-bs/media-ai-platform/issues/42) exists to
address (an observability layer would flag this pattern occurring at a measurable rate
across many runs, informing whether it's worth further investment).

---

## 7. Mask dilation margin left a visible white halo around the product

**Symptom**: A hard, visible white ring traced the product's silhouette against the newly
generated (non-white) background — looked like a compositing seam.

**Root cause**: `masking.py::build_inpaint_mask`'s dilation safety margin (originally
12px, meant to protect label pixels right at the product's edge) grows the *preserved*
region outward — but every pixel inside that margin is **raw original-photo background**,
preserved verbatim, not inpainted. A 12px ring of the source photo's own white background
sitting untouched right next to freshly-generated, differently-colored scenery reads as a
hard-edged halo, confirmed via a direct zoomed crop.

**Fix**: Shrank the margin to 4px and added a `GaussianBlur` feather on the black/white
mask boundary, so the transition from "preserved" to "inpainted" is a gradient a few
pixels wide rather than a cliff — Flux Fill blends naturally across a soft-edged mask in
a way it cannot across a hard one.

**Regression test**: `pipeline/tests/test_generation_masking.py::TestBuildInpaintMask::test_feathering_softens_the_preserve_inpaint_boundary`

**Confirmed live**: re-ran the same product photo before/after the fix — the halo is
visibly present in the "before" crop and gone in the "after" crop.

---

## 8. `dilate_px=0` still dilated the mask by ~2px (code bug, caught while testing #7)

**Symptom**: A regression test asserting "no dilation" behavior failed unexpectedly —
pixels just outside the product's raw boundary came back as *preserved* (black) even when
`dilate_px=0` was explicitly requested.

**Root cause**: `build_inpaint_mask`'s dilation loop was written as
`for _ in range(max(1, dilate_px // 2)):` — the `max(1, ...)` guard, meant to ensure at
least one `MaxFilter` pass for any *positive* `dilate_px`, ran unconditionally, including
when `dilate_px` was exactly `0`. "No dilation" silently became "~2px of dilation" in
every caller, including the default-parameter production path.

**Fix**: Wrapped the loop in `if dilate_px > 0:` so a zero margin means exactly zero
growth, with no minimum forced in.

**Regression test**: `pipeline/tests/test_generation_masking.py::TestBuildInpaintMask::test_dilation_grows_the_preserved_region_outward`
(rewritten to pass `feather_radius=0` explicitly and assert the exact boundary pixel,
which is what caught this in the first place).

---

## 9. Reference-ad images were guiding generation, not verifying it (architecture correction, not a bug)

**Symptom**: none directly observable in output quality, but a real design tension: the
style-brief agent (`style_reference.py`) asked a vision model to independently read
"background treatment, color choices" off 3 reference-ad images — dimensions
(`dominant_color`, `background_style`) the guide already has a rigorous, much
larger-sample SHAP/Cox answer for.

**Root cause**: a small, uncontrolled qualitative sample (3 images, often 0 given
corpus staleness) was positioned to potentially override or dilute a real statistical
finding — exactly the ungrounded-LLM-judgment failure mode this project is built to
avoid wherever a deterministic/statistical answer already exists (CLAUDE.md's own
principle). Surfaced by direct user critique of the architecture, not a live failure.

**Fix (Round 7, 2026-08-29)**: rescoped reference ads out of the generation-input path
entirely and into a post-generation comparison role:
- `style_reference.py::derive_style_brief()` is now text-only, guide-directed — the
  guide's directives are authoritative for every dimension they cover; the model's job
  narrows to translating them into concrete creative language plus filling
  `font_personality` (the one genuinely unmeasured dimension).
- `reference_ads.py::get_top_reference_ads()` gained directive-alignment scoring
  (`_directive_alignment_score`): a candidate ad must itself exhibit the guide's
  confirmed good traits (not just score well for unrelated reasons) to be selected,
  ranked `(alignment desc, composite_score desc)`, with a `min_alignment` floor that
  relaxes automatically if too few real ads clear it.
- New `feature_fidelity.py::review_feature_fidelity()` — a post-generation vision call
  comparing the final ad against the now-representative reference ads, checking whether
  the output actually replicated the confirmed pattern. Wired into the regeneration
  loop as a third independent gate alongside blend and guide-adherence review.

**Regression tests**: `pipeline/tests/test_generation_style_reference.py` (text-only,
no images), `pipeline/tests/test_generation_reference_ads.py::TestDirectiveAlignmentRanking`,
`pipeline/tests/test_generation_feature_fidelity.py`,
`pipeline/tests/test_generation_pipeline.py::test_regenerates_when_fidelity_check_fails_even_if_review_and_blend_pass`.

**Confirmed live**: re-ran `derive_style_brief()` against the real guide — with a
`dominant_color=green: lower_is_better` directive live, the resulting palette
(`#007bff`, `#ffffff`, `#f8f9fa`, `#6c757d`) correctly avoids green, derived purely
from guide text with zero images. Alignment scoring verified against the full
3,057-row corpus offline: a real, meaningful score distribution (-3 to +1), top
candidates by `(alignment, composite_score)` genuinely embody a confirmed directive.

## 10. Reference-ad image URLs are cryptographically expired, not blocked or rate-limited

**Symptom**: `get_top_reference_ads()` returns 0 results in practice, every time it's been
live-tested — 100% of attempted image fetches return HTTP 403, corpus-wide. Every prior
write-up (Round 6, Round 7) treated this as an unexplained staleness rate; this entry is
the actual root cause, found by decoding the URLs directly rather than guessing.

**Root cause**: every `image_urls` entry in `data/supplements_fresh_final.json` is a
Facebook CDN (`scontent.*.fbcdn.net`) URL carrying a signed, embedded expiry as a hex
Unix timestamp in its own `oe` query parameter. Decoded a real sample directly — e.g.
`oe=6A921A90` → `2026-08-28T23:32:32Z` — then checked the **entire corpus**: all 3,057
ads' URLs had already expired at time of check. This is not a rate limit, a User-Agent
block, or a policy change on Facebook's side (all three were live hypotheses in earlier
write-ups) — it's a cryptographically signed, time-limited URL that no retry, header
change, or backoff strategy can extend. Any static, persisted corpus of these URLs goes
100% dead within days of being scraped, by design, regardless of how the fetch code is
written.

**Fix, two parts**:
1. **Free, shipped**: `reference_ads.py::_is_url_expired()` decodes the `oe` timestamp
   locally and skips a known-dead candidate before ever attempting a network fetch —
   turns an ~8-second-timeout-per-candidate 403 storm (a full run once took ~45 minutes
   to exhaust the corpus) into an instant, free skip. Does not recover any usable
   images by itself — only makes the inevitable failure fast and honest.
2. **Paid, attempted, blocked externally**: `ingestion/refresh_image_urls.py` (built in
   a prior session, never previously run against this corpus) re-scrapes the same
   search query and matches fresh URLs back by `ad_archive_id`. Backed up the corpus,
   ran it for real against all 3,057 ads (`count=3000`, user confirmed the ~$2-3 Apify
   cost beforehand) — failed with `ForbiddenError: Monthly usage hard limit exceeded`,
   an **Apify account-level billing/quota limit**, not a code defect. Confirmed the
   corpus file was untouched (byte-identical to the pre-run backup) before discarding
   the backup. This part of the fix is blocked on the user's Apify account, not on any
   code in this repo.

**Regression tests**: `pipeline/tests/test_generation_reference_ads.py::TestIsUrlExpired`,
`TestSkipsExpiredUrlsWithoutNetworkCall`.

**Status**: fast-fail fix shipped and verified; the actual data-recovery fix is a real,
external, account-level blocker — revisit once the Apify usage limit resets or is raised.

## Round 6 live-verification findings

One finding from this round is now fully explained — see bug #10 above (signed URL
expiry, not a policy change or rate limit, root-caused in Round 7). Kept here rather
than merged in since it documents the *evidence trail* (what was observed, and read as,
before the root cause was known) as its own useful record:

- **The (then-)text-only style fallback under-performed the (then-)image-grounded
  path** — a Round 6 finding, superseded in framing by Round 7 (see bug #9 above):
  with zero reference ads available, that run's background came out "plain,
  light-colored studio-like" per the reviewer, the opposite of what the guide's own
  directives call for. At the time this was read as evidence the image-grounded path
  mattered; Round 7's rescope means style derivation is now *always* the text-only
  guide-directed path (there is no image-grounded alternative to compare against
  anymore), so the live-verification comparison above (green correctly avoided,
  purely from guide text) is the more relevant, current evidence that the text-only
  path alone can produce a directive-faithful result. Whether *feature_fidelity.py's*
  own image comparison degrades usefully with 0 reference ads (it does, by design —
  see bug #9) is the analogous open question going forward.
