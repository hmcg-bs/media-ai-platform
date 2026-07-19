# ADR-008: Universal layer decomposition for colour + VLM imagery + retrieval embeddings, prototyped on Replicate

**Status:** Accepted
**Date:** 2026-07-12
**Author:** Avinaash Padman
**Relates to:** ADR-005 (supersession + ROAS/embedding deferral), ADR-006 (deterministic-first + Gemini), ADR-007 (critique-first; layer decomposition user-only), CLAUDE.md (Vertex-only rule)
**Amends:** ADR-007 decisions #1 and #2 (layer decomposition is no longer user-only)

## Decision

We build the **colour-scheme** and **imagery** extraction components (the remaining two of
the four: copywriting, imagery, colour, positioning) on layer decomposition, and add a
retrieval-embedding path for suggestions. Five coupled decisions:

1. **Layer Decomposition runs on all ads (corpus + user).** **Qwen-Image-Layered** splits
   every ad into RGBA layers (background / product / text). This **amends ADR-007 #1/#2**,
   which reserved Qwen for the user's Critique ad and kept the corpus on a cheap flat-image
   path. The extraction path is now uniform.

2. **Colour = layers *augment* deterministic K-Means (not replace it).** Per ADR-006's
   deterministic-first rule, the layers are *inputs to maths*, not a substitute for it.
   **Revised after a smoke test (2026-07-12):** Qwen-Image-Layered does **not** produce a
   clean semantic bg/product/text split — only the **background layer is clean** (it's a
   compositing tool: foreground/background/shadow, not a copy extractor). So:
   - **Background colour variables** come from the clean background layer via OpenCV K-Means
     (a genuine upgrade over stage_03's perimeter-band approximation). The whole background
     layer is sampled, including inpainted regions (accepted: a gradient reconstructs
     faithfully).
   - **Text colour** stays with the Datalab bbox pixel-sampling already built
     (`TextStyle.color_measured`) — there is no usable text layer.
   Output remains colour **variables** (`background_hex`, `dominant_palette`, style, contrast
   for the background; `color_measured` for text).

3. **Imagery described by Qwen3-VL.** The product/imagery layer(s) are described by
   **Qwen3-VL-8B-Instruct**, producing the imagery-description text. This serves the imagery
   component that the Gemini deep tier (stage_05) currently covers; the two are alternatives
   behind one interface.

4. **Embeddings serve retrieval-grounded suggestions.** **embedding-gemma-300m** embeds the
   copywriting text + imagery description; the vectors power retrieval of similar proven ads
   / patterns for the Critique suggestion engine (ADR-007 #5, "retrieval-grounded"). This
   **supplements** statistical pattern-mining — it does **not** replace XGBoost/statistical
   discovery (ADR-005). It is a scoped reversal of the POC's "no vector search" line, limited
   to suggestion retrieval.

5. **Prototyped on Replicate; Vertex/GCP is the target.** Qwen-Image-Layered, Qwen3-VL, and
   embedding-gemma are accessed via **Replicate** now, each behind a **swappable client
   interface**, so they can be repointed to Vertex / Model Garden later. This is an explicit,
   **temporary** exception to CLAUDE.md's Vertex-only rule, scoped to these three models and
   flagged for migration — not a general adoption of other providers.

## Why

- **Uniform layered extraction** gives clean colour/imagery separation for *every* ad, so
  corpus mining and user critique draw from the same feature definitions (no OCR-box masking
  approximation, no cheap/expensive asymmetry to reconcile). The cost of running Qwen on the
  whole corpus is accepted as a Phase-2 investment — see Open.
- **Deterministic-first is preserved**: layers are masks feeding K-Means maths; the colour
  numbers are still reproducible and free of LLM guesswork (consistent with ADR-006, and with
  the `color_reported` vs `color_measured` split already adopted for Datalab text colour).
- **Embeddings match the documented suggestion mechanism** (retrieval-grounded, ADR-007 #5)
  rather than replacing the statistical core — keeping the "math, not LLM guesswork" spine.
- **Replicate is the fastest way to try these OSS models**; the swappable-client design
  de-risks the eventual Vertex/Model-Garden port and keeps the Vertex-only rule as the target.

## Consequences

- **ADR-007 #1/#2 amended** and **CONTEXT.md updated**: "Layer Decomposition" no longer says
  "only on the user's ad, never the corpus"; it now runs on all ads (this ADR). The
  benchmarked/best-practice suggestion split is no longer *forced* by extraction asymmetry.
- New stack entrants (behind clients): Replicate access for three models, plus a **vector
  store** for suggestion retrieval. CLAUDE.md's Vertex-only rule gains a scoped, documented
  exception.
- stage_03 colour gains a **layer-mask input**; the imagery description may be served by
  Qwen3-VL instead of / alongside the Gemini deep tier (stage_05).
- **Copy is sourced from Datalab plain convert** (not Style Preserver, which drops the main
  headline). The **Datalab extract step** (balanced) fills the marketing hook + value
  proposition and the role-based copy features; stage_05's Gemini cheap tier now **skips**
  `marketing_psychology` when Datalab already filled it (Datalab wins in the full pipeline; the
  Cloud-Vision-only path still gets Gemini). **Product Type is deferred to Step 1** — classified
  after scraping the ad's landing page / product info, via free classification (no fixed
  taxonomy), not during extraction.
- Inpainted/hallucinated Qwen layer pixels are **never** treated as extraction evidence
  (unchanged from ADR-007) — colour is sampled from real layer pixels, not generated fills.

## Open (not yet decided)

- **Cost of universal layer decomposition** at corpus scale — if prohibitive, revert to
  ADR-007's user-only rule for Qwen and keep a cheaper corpus colour path.
- **Vector store choice** and how retrieval results combine with statistical Patterns in
  Step 3 (ranking, dedup, benchmarked-vs-best-practice labelling).
- **Vertex / Model Garden equivalents** for the eventual port (Gemini as VLM; Vertex
  embeddings) and where each model runs.
- Whether the colour "variables" become named design tokens shared with Generation later.
