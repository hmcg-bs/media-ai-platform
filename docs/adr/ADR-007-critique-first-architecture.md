# ADR-007: Critique-first product on a brand-scraped corpus, with layered critique and a composite Ad-Library performance proxy

**Status:** Accepted
**Date:** 2026-06-25
**Author:** Avinaash Padman
**Relates to:** ADR-005 (supersession + ROAS deferral), ADR-006 (Step 2 extraction)

## Decision

The v1 product is **Critique**: a user uploads their *own* ad, the system extracts
its features, classifies its Product Type, looks up the top patterns for that type,
and returns Suggestions. From-scratch **Generation** (re-rendering an "ideal ad")
is a later phase. This replaces the documented Step 5 *brief generator* (category +
audience → invented brief), which is retired.

Five coupled decisions define the architecture:

1. **Critique uses Layer Decomposition.** The user's flat ad is split into RGBA
   layers (background / product / text) via **Qwen-Image-Layered**, so the VLM can
   analyse each layer *and* their Composition (z-order/overlap, e.g. copy on top of
   the product). Qwen runs **only on the user's ad**, never on the corpus.

2. **Hybrid extraction.** The competitor **Corpus** stays cheap (deterministic +
   Gemini on flat images, per ADR-006). Only the user's ad gets the expensive
   layered treatment. Consequently, Suggestions are labelled as either
   *benchmarked* (grounded in corpus Patterns) or *best-practice* (Composition /
   design judgement, not yet corpus-proven). Inpainted Qwen pixels are never
   treated as extraction evidence.

3. **Composite performance proxy.** With no ROAS, "winning" is approximated from
   Meta Ad Library signals only: **Longevity** (`days_active`) as the base,
   **weighted by brand Scaling** (the brand's active-ad count, so a lone
   long-running ad isn't mistaken for a winner), **boosted by Variant** count.
   Engagement (likes/shares) is **excluded** — the Ad Library does not expose it
   for ads (verified, June 2026).

4. **Product Type is a Gemini-classified fixed taxonomy.** A curated ~15–30-entry
   coarse taxonomy (in config) is the **join key** between a user's ad and corpus
   Patterns. Gemini classifies every ad (corpus + user) into it; the raw product
   name is retained for future sub-typing.

5. **"Training" is statistical aggregation, not fine-tuning.** Top features are
   computed per Product Type by aggregating the Corpus against the composite proxy
   (single-feature rankings first; combinations only as volume allows). The
   suggestion engine is a **retrieval-grounded Gemini call** (user features + top
   patterns → suggestions), not a fine-tuned model. Step 4 SFT stays deferred per
   ADR-005.

## Why

- **Brand-centric scraping is forced by the proxy.** Computing Scaling needs a
  brand's *complete* active-ad set, so the scrape axis is the brand (page), seeded
  from a curated competitor list; keyword scraping (partial per-brand slices) would
  break Scaling. Product Type is assigned *after* scraping, not used to scrape.
- **Layered critique only on the user's ad** keeps the bulk pipeline cheap while
  giving a single user a high-fidelity reading — at the cost of some Suggestions
  being best-practice rather than corpus-proven (made explicit by labelling).
- **Longevity is confounded** (big brands keep ads running regardless of creative);
  Scaling weighting is the correction, and the benchmarked/best-practice split keeps
  the critique honest about what the data can and cannot prove.

## Consequences

- The Step 5 brief-generator design is retired; "the model" is a retrieval-grounded
  critique call, not an SFT endpoint.
- New domain language (Critique, Suggestion, Layer Decomposition, Composition,
  Generation, Corpus, Product Type, Scaling, Variant) is recorded in `CONTEXT.md`.
- New dependencies enter the stack for the critique path only: Qwen-Image-Layered
  (hosting — fal.ai vs self-host — TBD) and, later, a code-based render engine for
  Generation.

## Open (not yet decided)

- Exact composite-score formula (how Longevity × Scaling × Variant combine).
- Single-feature vs combination aggregation thresholds per Product Type.
- Qwen hosting (managed API vs self-hosted GPU) and where the critique path runs.
