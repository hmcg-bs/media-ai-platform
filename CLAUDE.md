# Social Media Analysis Agent — Claude Code Instructions

## Project Purpose

The **Social Media Analysis Agent** ingests ad creatives, extracts structured
creative features from each image, and mines which feature patterns correlate
with performance. It bypasses LLM hallucination and enterprise cost by combining
deterministic code, statistical math, and *targeted* generative AI — AI is used
only for features that genuinely need cognitive understanding.

> This project **supersedes** the former "Media AI Platform" (a LiteLLM/Supabase
> copy-generation product). See [ADR-005](./docs/adr/ADR-005-supersede-media-ai-platform.md).
> The glossary of domain terms is in [CONTEXT.md](./CONTEXT.md).

**The five Steps:**
1. **Ingestion** — own ads (Meta Marketing API) + competitor ads (Apify → Meta Ad Library) into BigQuery
2. **Extraction** — turn each creative image into a structured feature document *(current focus)*
3. **Pattern Discovery** — BigQuery ML / segment SQL finds feature→performance correlations
4. **Fine-Tuning** — (Phase 2) Vertex AI SFT on proven patterns
5. **UI** — Streamlit dashboard on Cloud Run

---

## Current State (read this first)

We are building **Step 2 (Extraction) only**, as a **local CLI** that runs over a
folder of example creatives and writes validated JSON — no serverless deployment,
no BigQuery, no performance metrics yet. Rationale and scope: see
[ADR-005](./docs/adr/ADR-005-supersede-media-ai-platform.md).

Key v1 constraints:
- **No ROAS anywhere.** There is zero own-ad data; "performance" means competitor
  Longevity (`days_active`), and that only enters in Step 3 — never in Extraction.
- **Stage 4 (landing-page scraper + visual verification) is deferred** —
  `is_visually_verified_match` is always `null`.
- **Steps 1, 3, 4, 5 are not built yet.**

---

## How the AI Layer Works

All generative-AI calls go through **Vertex AI Gemini** via the **`google-genai`**
SDK (`genai.Client(vertexai=True, …)`). Never import the old `litellm`/`anthropic`/
`openai` SDKs.

**Two-tier cognitive layer** (Step 2, Stage 5):

| Tier | Model (config) | Extracts |
|---|---|---|
| Cheap | `gemini_cheap_model` (`gemini-2.0-flash-lite`) | marketing psychology / hook / vibe |
| Deep | `gemini_deep_model` (`gemini-2.5-flash`) | objects, spatial relationships, human detail |

Structured output is enforced by passing a **Pydantic model as `response_schema`**
(with `response_mime_type="application/json"`). Models are configurable in
`pipeline/config.py` — swapping is a config change, not a code change.

> Gemma is intentionally **not** used yet (no Model Garden endpoint to provision).
> Revisit only when Step 4 SFT is built.

---

## Coding Conventions

- **Type hints** on all functions.
- **Pydantic models are the source of truth.** `models/output_schema.py` defines
  the Master JSON Schema once; it is both the inter-stage `PipelineContext` and the
  Gemini `response_schema`. Don't create parallel data shapes.
- **No `os.environ` access** outside `pipeline/config.py` (`get_settings()`).
- **All AI calls via `google-genai`** against Vertex AI.
- **Stages inherit `BaseStage`** and implement `process(context) -> context`.
  Stage failures raise `StageError`; the orchestrator catches it, records the
  failed stage, and continues (per-stage fallback). The pipeline never halts on one
  bad creative.
- **Deterministic-first.** Structural features (dimensions, colour math, OCR
  geometry) are computed in code and never sent to an LLM.
- **Structured logging** via `structlog`; standard events: `stage_started`,
  `stage_completed` (+`duration_ms`), `stage_failed`, `fallback_applied`.
- **Never extend `archive/old-src/`** — it is the retired Media AI Platform scaffold.

---

## Environment & Running

Managed with **`uv`** (Python pinned to **3.12** — the version GCP runtimes and
opencv/google wheels support; the system's 3.14 is not deployable).

```bash
gcloud auth application-default login    # ADC for Cloud Vision + Vertex AI
uv run python -m pipeline.orchestrator --input ./examples --out ./out
```

`uv run pytest pipeline/tests` is offline — it needs no GCP credentials.

`requirements.txt` + `uv.lock` are the committed contract that GCP rebuilds from —
the `.venv` is local-only and gitignored.

---

## Agent skills

- **Issue tracker:** `docs/agents/issue-tracker.md`
- **Triage labels:** `docs/agents/triage-labels.md` (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`)
- **Domain docs:** single context — the glossary is [CONTEXT.md](./CONTEXT.md). See `docs/agents/domain.md`.
- **Session logging:** the Obsidian vault protocol is the `session-logging` skill — invoke it at end of session.

---

## Key Architectural Decisions

- **ADR-006** — Step 2 Extraction = deterministic-first ensemble + two-tier Gemini
  cognitive layer; Master JSON Schema as the contract; per-stage fallback. Records
  the v1 deviations from the blueprint (local CLI, Gemini-not-Gemma, Stage 4
  deferred). *(current)*
- **ADR-005** — Supersede Media AI Platform with the GCP Social Media Analysis
  Agent; defer ROAS/XGBoost/SFT until own-ad data exists. *(current)*
- **ADR-004** — Vault `Structure/` vs `Sessions/` split. *(in force)*
- **ADR-001/002/003** — Superseded by ADR-005 (LiteLLM, agent Pydantic framing,
  platform adapters all belonged to the retired product).

Detailed engineering specs for each Step live in `docs/blueprints/` (not ADRs).

---

Last updated: 2026-06-03
