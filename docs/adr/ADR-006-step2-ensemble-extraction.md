# ADR-006: Step 2 Extraction is a deterministic-first ensemble with a two-tier Gemini cognitive layer

**Status:** Accepted
**Date:** 2026-06-03
**Author:** Avinaash Padman
**Blueprint:** [docs/blueprints/step2_ensemble_extraction_pipeline.md](../blueprints/step2_ensemble_extraction_pipeline.md)

## Decision

Step 2 turns each Ad Creative into a structured Extraction Result through a chain
of pluggable `BaseStage`s. We **compute every structural feature deterministically**
(image metadata, OCR geometry/typography hierarchy, OpenCV K-Means colour) and
**reserve generative AI for cognitive features only**, split across two Gemini
tiers: a cheap tier (`gemini-2.0-flash-lite`) for marketing psychology, and a deep
tier (`gemini-2.5-flash`) for objects, spatial relationships, and human detail.
Gemini is reached via the `google-genai` SDK against Vertex AI, with a Pydantic
model passed as `response_schema` for guaranteed structured output.

The **Master JSON Schema** (`pipeline/models/output_schema.py`) is the frozen
contract: it is simultaneously the inter-stage `PipelineContext` and the LLM
`response_schema`, and it is what Step 3 will later join against.

Stages fail **independently**: a `StageError` is caught by the orchestrator, the
failed stage is recorded, and the run continues with that stage's fields at schema
defaults. One bad creative never halts the batch.

## Why this shape

- **Cost + correctness.** Colour math, font-size ratios, and dimensions have exact
  answers; sending them to an LLM would add cost and hallucination risk for no gain.
  AI is used only where genuine cognitive understanding is required.
- **Two tiers** keep spend proportional to task difficulty without collapsing the
  ensemble into one expensive call.
- **Schema-as-contract** means downstream steps (and tests) validate against one
  source of truth rather than ad-hoc dicts.
- **Per-stage fallback** suits a solo-maintained, batch pipeline where partial data
  beats a halted run.

## v1 deviations from the blueprint (deliberate)

The blueprint targets full GCP serverless (Cloud Run, Cloud Workflows, GCS-event
triggers, BigQuery sink, a Gemma deep tier). v1 deviates to reach working output on
example creatives fast:

- **Local CLI**, not serverless. Output is validated JSON files on disk; the
  BigQuery sink (Stage 6) is deferred.
- **Gemini for the deep tier, not Gemma.** The blueprint's `gemma-4` is not a real
  model, and any Gemma on Vertex needs a provisioned Model Garden endpoint —
  unjustified infra at this stage. Revisit when Step 4 SFT is built.
- **Stage 4 (landing-page scraper + visual verification) is deferred.** Example
  creatives have no destination URL, so `is_visually_verified_match` stays `null`.

These are scope/sequencing choices, not changes to the target architecture; the
blueprint remains the eventual destination. See also
[ADR-005](./ADR-005-supersede-media-ai-platform.md).
