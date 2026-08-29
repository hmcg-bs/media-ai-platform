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

## Round 6 live-verification findings (not yet fixed)

Two findings surfaced by the final full end-to-end verification run
(`smoke_generated_ad_v11.png`), kept here rather than filed as bugs since neither is a
code defect — both are real, honest limitations worth tracking:

- **Reference-ad corpus staleness has gotten worse, not better.** 100% of the 3,057-ad
  corpus's candidate image URLs returned HTTP 403 in this run, versus the ~54% figure
  documented in earlier sessions. `reference_ads.py`'s designed fallback (skip and
  continue) worked correctly — the run completed — but this means the retrieval-grounded
  style system (see failure mode #2's fix) is currently *never* actually grounded in real
  images in practice, only in the text-only fallback path. Worth a dedicated
  investigation (is Facebook's CDN policy stricter now, or is this corpus's URL set
  simply older) before assuming Round 5's retrieval-grounding is providing its intended
  benefit at all in production.
- **The text-only style fallback under-performs the image-grounded path.** With zero
  reference ads available, this run's background came out "plain, light-colored
  studio-like" per the reviewer — the opposite of the busy/real-world scene both the
  guide and the fallback prompt explicitly call for. This is evidence, not just theory,
  that Round 5's image-grounded retrieval path was doing real, non-cosmetic work — the
  text-only fallback alone is not an adequate substitute.
