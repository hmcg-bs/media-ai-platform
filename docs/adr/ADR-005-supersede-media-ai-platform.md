# ADR-005: Supersede the Media AI Platform with a GCP Social Media Analysis Agent; defer ROAS/XGBoost/SFT until own-ad data exists

**Status:** Accepted
**Date:** 2026-06-03
**Author:** Avinaash Padman
**Supersedes:** ADR-001, ADR-002, ADR-003

## Decision

We are replacing the **Media AI Platform** (FastAPI + LiteLLM + Supabase/pgvector
+ Next.js copy-generation product) with a **Social Media Analysis Agent** on GCP
(BigQuery, Cloud Run, Vertex AI, Streamlit) that ingests ad creatives, extracts
structured creative features, and mines performance patterns.

We are also **deferring the ROAS → XGBoost → SFT chain** (the plan's Steps 3–4).
We have **zero own-ad performance data** today, so there is no ROAS label to
train on. We build **Step 2 (Extraction) first**, run it on example creatives
with no performance metrics attached, and treat competitor `days_active`
(Longevity) as the only Performance signal until real ads have run.

## Context

The repo's prior documentation (CLAUDE.md, CONTEXT-MAP.md, ADR-001→004) and
scaffold (`src/`, `settings.py`, `requirements.txt`) described the Media AI
Platform. A May 2026 pivot moved the project to a GCP serverless architecture,
but the documentation was never reconciled — every glossary term, ADR, and
dependency still described the abandoned system. This ADR records the pivot so a
future reader doesn't "fix" the new code back toward the old design.

## Why defer the ML/SFT chain

- **No labels.** XGBoost regression on creative features needs hundreds–thousands
  of labelled rows; we have none. SFT's 100-example floor is unreachable.
- **Fabrication risk.** A model fine-tuned to emit `avg_roas` figures it has never
  seen would manufacture the exact evidence the system claims to ground out —
  directly violating the project's "math, not LLM guesswork" philosophy.
- **Cheaper truth exists.** The segment-correlation SQL already produces ranked,
  auditable patterns from Longevity without any model training.

## Consequences

- ADR-001 (LiteLLM), ADR-002 (Pydantic-as-output — *the principle survives*, the
  LiteLLM framing does not), and ADR-003 (platform adapters) are superseded.
  ADR-004 (Vault Structure/Sessions split) still stands.
- "Performance" now means Longevity (`days_active`), not ROAS, until Phase 2.
- The own-ad ingestion path, XGBoost, and Gemma SFT are documented future work,
  gated on having run real ad campaigns.
