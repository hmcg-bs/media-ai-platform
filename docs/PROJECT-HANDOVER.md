# Project Handover: Social Media Analysis Agent

**Purpose of this document**: a single entry point for an agent taking over
governance of this project — orienting it to the long-term objective, what's
actually built vs. planned, the architecture, and the conventions every
sub-agent it delegates to must follow. This is a *pointer* document, not a
replacement for the sources it points to: `CLAUDE.md` (coding rules,
authoritative, load every session), `CONTEXT.md` (domain glossary,
authoritative), and each wayfinder map (the living decision log for its own
effort). When this document and one of those disagree, the source wins —
update this document, don't trust it blindly.

**Last verified against repo state**: 2026-08-30, branch `master`,
commit `b937cb4` + uncommitted Generation v2 work (see
[§8 Pending decisions](#8-pending-decisions--immediate-next-steps)).

---

## 1. Long-term objective

Per `CLAUDE.md` and `CONTEXT.md`: build a GCP-based system that

1. **Ingests** ad creatives (own ads via Meta Marketing API + competitor ads
   via Apify → Meta Ad Library) into a structured corpus.
2. **Extracts** structured Creative Features from each ad image — colour,
   typography, hook framework, human presence, layout, etc. — deterministic
   code where possible, Gemini only where genuine understanding is needed.
3. **Mines Patterns** — which Creative Feature combinations correlate with
   Performance (a composite proxy built from `days_active`, `Scaling`,
   `Variant` count — see `CONTEXT.md`; there is no ROAS data, and there must
   never be a ROAS figure in v1 output).
4. Powers two client-facing products built on top of those Patterns:
   - **Critique**: takes a user's own ad, extracts its features, compares
     against the top Patterns for its Product Type, returns Suggestions.
   - **Generation** ("Ideal Ad"): synthesizes a *new* ad from a client's raw
     product photo + stated intention, grounded in the same Patterns.
5. Eventually a **Fine-Tuning** phase (Vertex AI SFT on proven patterns,
   explicitly Phase 2, not started) and a **UI** phase (Streamlit on Cloud
   Run, explicitly deferred, not started).

This supersedes an earlier, retired product ("Media AI Platform," a
LiteLLM/Supabase copy-generation tool — see
[ADR-005](./adr/ADR-005-supersede-media-ai-platform.md)). Its old code lives
in `archive/old-src/`, `frontend/`, and the top-level `tests/` — **do not
extend these**, they are retired scaffold, not live code.

---

## 2. Current state at a glance

| Step | Status | Wayfinder map | Primary doc |
|---|---|---|---|
| 1. Ingestion | **Live, iterated on extensively.** Scraping + landing-page enrichment pipeline for the Supplements category is mature (coverage numbers below). Recurring-schedule ETL for corpus freshness is discussed but **not built**. | [#1](https://github.com/hmcg-bs/media-ai-platform/issues/1) (shared with modeling, still open) | `docs/architecture-review-ingestion.md`, `docs/extraction-failure-modes.md` |
| 2. Extraction | **Live.** `pipeline/orchestrator.py` + `pipeline/stages/*` — deterministic (metadata, OCR, colour) + cognitive (Gemini) stages, per-stage fallback. Wired to the ingestion corpus. | [#1](https://github.com/hmcg-bs/media-ai-platform/issues/1) | `docs/adr/ADR-006-step2-ensemble-extraction.md`, `docs/blueprints/step2-ensemble-extract.md` |
| 3. Pattern Discovery / modeling | **Live, but methodology evolved past the original plan.** Cox survival (`days_active`, censoring-aware) + composite z-scored success-score with XGBoost/SHAP feature attribution — not a single straight regressor per target as originally scoped. | [#1](https://github.com/hmcg-bs/media-ai-platform/issues/1) | `pipeline/model_training/*`, ADR references inline in map #1 |
| Generation (cold-start) | **Live, v2 just shipped this session.** Layout-first architecture, deterministic-first duplicate-product detection + surgical repair, multi-variant generation. Re-render path (existing draft → Critique → regenerate) is **not built**. | [#36](https://github.com/hmcg-bs/media-ai-platform/issues/36) | `docs/generation-v1-architecture.md`, `docs/generation-failure-modes.md` |
| Generation harness (observability/orchestration/eval) | **Barely started.** Only the one-time architecture visualization is done; the actual harness (the map's real destination) has no design yet. | [#42](https://github.com/hmcg-bs/media-ai-platform/issues/42) | issue #42 itself (no dedicated doc yet) |
| 4. Fine-Tuning | **Not started.** Explicitly Phase 2 per `CLAUDE.md`. | — | — |
| 5. UI | **Not started.** Explicitly deferred per `CLAUDE.md`. | — | — |
| Critique (as a product, distinct from Extraction) | **Not built as a product surface.** The pieces it needs (Extraction, Pattern mining) exist; the "compare a user's ad against Patterns and return Suggestions" flow itself does not. | — | `CONTEXT.md`'s Critique entry (definition only) |

**Every wayfinder map is currently OPEN** — none of this project's three major
efforts has been declared done and closed. Treat "open" as "still the live
place to log new rounds of work," not "abandoned."

---

## 3. Repository map

```
Media AI Platform/
├── CLAUDE.md                 Authoritative coding conventions -- read every session
├── CONTEXT.md                 Authoritative domain glossary -- read every session
├── docs/
│   ├── PROJECT-HANDOVER.md    This file
│   ├── adr/                   8 ADRs -- architectural decisions (§6 below)
│   ├── agents/                issue-tracker.md, triage-labels.md, domain.md
│   ├── blueprints/             Original per-Step engineering specs (pre-build, some now stale -- see §6)
│   ├── generation-v1-architecture.md   Generation's current architecture (kept name for link stability, content is v2)
│   ├── generation-failure-modes.md     Generation's live bug catalog (14 entries)
│   ├── extraction-failure-modes.md     Step 2/ingestion's live bug catalog
│   ├── architecture-review-ingestion.md
│   └── meta-ad-image-model-stack.md    Model-choice research for Generation's image models
├── ingestion/                 Step 1: scraping + landing-page enrichment (28 modules)
├── pipeline/
│   ├── orchestrator.py        Step 2 CLI entry point
│   ├── stages/                Step 2's BaseStage chain (metadata, OCR, colour, cognitive, layer-color, imagery)
│   ├── models/output_schema.py  The Master JSON Schema (PipelineContext) -- source of truth for Extraction Results
│   ├── clients/                GenAIClient (Vertex Gemini), replicate_client.py (7 Replicate model clients)
│   ├── feature_engineering/    Corpus -> ML feature matrix
│   ├── model_training/         Cox survival, composite success-score, SHAP, data-quality checks
│   ├── validation/              Golden-set eval harnesses (phase0, price-context, cognitive)
│   ├── generation/              Generation v2 (guide -> ... -> pipeline.py); see §5
│   ├── color/, embedding/, datalab/   Supporting deterministic/embedding utilities
│   └── tests/                  43 test files, offline (no GCP creds needed)
├── data/                       Corpus + feature-matrix + report JSON artifacts (see §7 -- many are dated backups)
├── archive/old-src/, frontend/, tests/   RETIRED (pre-ADR-005 Media AI Platform scaffold) -- do not extend
└── out/                        Local run outputs (Step 2 batch runs, generated ads) -- gitignored working directory
```

---

## 4. Step 1 — Ingestion

**What it does**: scrapes competitor ads (Apify → Meta Ad Library) and each
ad's landing page (a tiered cascade: Shopify JSON fast path → ZenRows managed
scraping → Builder Fingerprint/advertorial extraction → zone-pruned LLM
fallback), producing a `CompetitorAd`/`ProductPage` corpus.

**Current state, live-measured on the original 2,736-ad corpus** (2026-08-26):
`product_page` 97.3%, `price` 70.7%, `variants_featured` 53.0%, `brand_name`
84.1%, `product_name` 94.1%, `rating` 46.1%. A **second, separate** 3,057-ad
"fresh corpus" also exists (built to validate a fully-concurrent
scrape→enrich→Step2 pipeline shape) — **the two are not merged**, and whether
they should be is an open, undecided question (see wayfinder map #1's "Not
yet specified").

**Key files**: `ingestion/tiered_scraper.py` (the cascade), `ingestion/zenrows_scraper.py`,
`ingestion/builder_fingerprint.py`, `ingestion/llm_fallback.py`,
`ingestion/subscription_detector.py`, `ingestion/utm_features.py`.

**Explicitly not started / deferred**:
- The user's own stated goal of a **fixed-schedule recurring scrape** (60-90
  day data currency, persisting soon-to-expire CDN images rather than
  re-scraping once, storage estimation) — discussed in this session's history
  but the user redirected to "make sure generation works well first" before
  any of it was scoped or built. **This is real, stated future work, not
  abandoned** — see [§8](#8-pending-decisions--immediate-next-steps).
- Meta Pixel detection (`ingestion/pixel_detector.py` — scoped, never built).
- Comments/likes/sentiment extraction — confirmed a genuine tooling dead end
  (ZenRows blanket-blocks facebook.com/instagram.com), not pursued further.
- "Good documentation of the current pipeline" + an `improve-codebase-architecture`
  pass on `ingestion/` — flagged in map #1 as concrete-enough-to-just-do,
  **still not started**.

**Facebook CDN signed-URL expiry is a load-bearing fact for this whole
project**: every scraped `image_urls` entry carries a cryptographically
signed, short-lived expiry (an `oe` hex-timestamp query param). A corpus
scraped once and read later will have **dead image URLs**, not flaky ones —
this is *why* Generation's reference-ad retrieval currently returns 0 results
in practice (see §5), and *why* any future recurring-ETL design must treat
"persist the actual image bytes near scrape time" as a hard requirement, not
an optimization.

---

## 5. Step 2 — Extraction

**What it does**: turns one ad creative image into a structured
`PipelineContext` (the Master JSON Schema, `pipeline/models/output_schema.py`)
via a `BaseStage` chain with per-stage fallback (`pipeline/stages/base_stage.py`)
— a stage failure never halts the run, its fields just stay at schema
defaults. Deterministic stages (metadata, OCR geometry, K-Means colour) never
touch an LLM; the Cognitive stage uses Gemini only for what genuinely needs
understanding (hook framework, human presence, marketing psychology).

**Config-flag convention** (`pipeline/config.py`, `Settings.enable_*` fields)
is how optional stages/paths are turned on — this is the established pattern
any new pluggable capability in this project should reuse rather than
inventing a plugin-loader:
`enable_replicate_cognitive`, `enable_datalab_copy`, `enable_layer_color`,
`enable_imagery`, `enable_embeddings`.

**Current state**: wired to the ingestion corpus (Extraction runs over ad
image bytes fetched into memory, never persisted to disk). Concurrency
(~6.7x speedup) is in place. Stage 5's own extraction accuracy has **never
actually been measured against human labels** — a 42-ad golden set exists
(`pipeline/validation/cognitive_validator.py`/`.html`) but is **still awaiting
the user's manual labeling pass**. Treat any claim about Stage 5's accuracy as
unverified until that labeling happens.

**Key ADR**: [ADR-006](./adr/ADR-006-step2-ensemble-extraction.md) —
deterministic-first ensemble, per-stage fallback, Master JSON Schema as the
contract.

---

## 6. Step 3 — Pattern Discovery / Model Training

**What it does**: `pipeline/model_training/` mines which Creative Features
correlate with Performance. The methodology **evolved substantially past the
original destination** on wayfinder map #1:

- **`days_active` (Longevity)**: Cox Proportional Hazards survival analysis
  (censoring-aware), not plain regression — a naive time-split regression
  produced catastrophic negative R² because 94.2% of `end_date` values turned
  out to be corpus scrape-run stamps, not real end dates.
- **Composite success-score model**: a retrospective "what do already-
  successful ads share" model (z-scored blend of all three target signals,
  random train/test split, XGBoost + native Tree SHAP for attribution) —
  this replaced an earlier per-target forecasting framing after user pushback
  ("why would old ads generalize to new ones?").
- This composite model + its SHAP output is **Generation's actual "model
  guidance" source** — `pipeline/generation/guide.py::extract_generation_guide()`
  reads `data/model_training_report_fresh.json` and
  `data/success_score_report_fresh.json` directly. **Any retraining of these
  models changes what Generation's guide contains** — treat them as a live
  dependency, not a one-time artifact.

**Key files**: `pipeline/model_training/survival.py` (Cox), `success_score.py`
(composite score + SHAP), `feature_registry.py` (hand-documented ~60 feature
columns), `data_quality.py` (correlation/missingness-bias/thin-category
checks, published as a sortable Artifact dashboard).

---

## 7. Generation (cold-start path) — v1 → v2

**This is the most recently and heavily worked area.** Full architecture is
documented in [`docs/generation-v1-architecture.md`](./generation-v1-architecture.md)
(kept under that filename for link stability; content is current v2) — **read
that document in full before touching any `pipeline/generation/*.py` file**,
this section is only a summary.

**Entry points**: `pipeline.generation.pipeline.generate_cold_start_ad()`
(one ad) and `generate_cold_start_ad_variants(n_variants=3)` (N candidates for
human selection), both via `pipeline/generation/cli.py`.

**v2 (Round 9, 2026-08-30, this session)** restructured v1 around two
findings:
1. **Layout is computed once, deterministically**, before generation runs
   (`layout_planner.py` + `masking.py::compute_product_bbox()`) — not
   searched per-attempt by a vision call over an already-generated frame.
2. **Duplicate-product hallucination is checked directly and repaired**
   (`duplicate_detection.py`, deterministic-first with vision escalation;
   `background.py::repair_duplicate_region()` for a targeted, capped fix)
   rather than hoped-for by the general review agents.

**Live-verified this session** (real API calls): a full end-to-end run
produced **zero duplicates in a single pass** (vs. the pre-v2 baseline's 3/3
failed attempts on the same product photo); a 3-variant run produced
genuinely distinct outputs sharing one identical `layout_plan`. Full detail
and images: see the session's own summary in wayfinder map #36's Round 9
entry.

**Known, honestly-documented open limitations** (full detail in
[`docs/generation-failure-modes.md`](./generation-failure-modes.md)):
- **#6 / #13**: duplicate-product hallucination is *mitigated*, not
  *eliminated* — surgical repair can itself reintroduce a different
  duplicate in the same erased region, since it inherits the same generative
  bias it's trying to fix. The capped-retry-then-full-regen-fallback design
  is the real safety net, confirmed live to actually engage, not theoretical.
- **#14**: `validate_layout()`'s zone-violation finding is recorded but does
  **not** currently gate the regeneration loop (a deliberate v2 scope
  decision) — flagged as a candidate follow-up, not decided.
- **Reference-ad retrieval returns 0 results in almost every real run**
  (corpus image-URL staleness, see §4) — `feature_fidelity.py` degrades
  gracefully (`checked=False`) when this happens, but it means the
  fidelity-check gate is currently non-functional in practice until the
  corpus-freshness ETL work (§4, §8) exists.
- The re-render path (existing draft ad → Critique → targeted/full
  regeneration) referenced throughout the docs as "wayfinder issue #41" is
  **not built** — cold-start is the only entry point that exists.

**Client layer**: `GenAIClient` (Vertex Gemini) + `replicate_client.py`
(`BackgroundRemoverClient`, `FluxFillClient`, plus `FluxKontextClient` kept as
an unused fallback candidate). GCP ADC credentials in this environment
periodically need `gcloud auth application-default login` — confirmed live
this session to still occur despite service-account impersonation; budget for
this when running anything that calls Vertex Gemini after an idle gap.

---

## 8. Pending decisions / immediate next steps

In rough priority order — these are the concrete things a governing agent
should either act on or explicitly re-confirm are still deferred, not silently
assume are resolved:

1. **Uncommitted work from this session.** As of this document, `git status`
   shows 17 changed/new files under `pipeline/generation/`, `pipeline/tests/`,
   and `docs/` (the entire Generation v2 build) **not yet committed**. Per
   this project's standing rule, commits only happen when the user explicitly
   asks — confirm with the user before committing or pushing.
2. **Corpus merge decision** (wayfinder map #1): should the original 2,736-ad
   corpus and the fresh 3,057-ad corpus be merged, kept separate, or should
   the fresh one replace the original? Unresolved, blocks a clean answer to
   "what is *the* corpus."
3. **Stage 5 eval golden-set labeling** — the tool and sample are ready
   (`cognitive_validator.html`), blocked purely on the user completing manual
   labeling. Step 2's cognitive-extraction accuracy is unmeasured until this
   happens.
4. **Apify account usage-limit block** on `ingestion/refresh_image_urls.py` —
   the durable fix for corpus image staleness needs a fresh Apify scrape;
   last known to be blocked by `ForbiddenError: Monthly usage hard limit
   exceeded`. Check whether the account's usage window has reset before
   re-attempting.
5. **Generation v2 follow-ups** (bugs #13/#14 in the failure-modes catalog):
   whether `validate_layout`'s finding should join the regeneration gate;
   whether duplicate detection's vision-escalation prompt should be tuned to
   also catch smaller/multiple hallucinated objects (missed 3 jars in one
   live test, caught the primary duplicate bottle).
6. **Wayfinder map #42's real destination (the harness) has barely started**
   — only the one-time architecture visualization is done. The central open
   design question (the step-level plugin seam shape) is unresolved and
   blocks orchestration/eval work.
7. **The user's stated recurring-ETL / corpus-freshness goal** (§4) has not
   been scoped into a wayfinder map yet — it was explicitly paused in favor
   of the Generation v2 work just completed. This is the natural next major
   thread once Generation v2's own follow-ups are triaged.
8. **Critique as a product surface** does not exist yet — Extraction and
   Pattern Discovery exist, but nothing implements "take a user's ad, compare
   against Patterns, return Suggestions." Not currently on any open wayfinder
   map's explicit destination.

---

## 9. Data assets — what's current vs. stale

`data/` contains many dated/backup files from iterative fixes. When a
sub-agent needs "the corpus" or "the feature matrix," use the files below —
don't guess from filename recency alone, several `*_backup_*` and unsuffixed
older files are intentionally-kept safety copies, not the active version:

| File | What it is |
|---|---|
| `supplements_fresh_final.json` | The fresh 3,057-ad corpus (current, referenced by `reference_ads.py`) |
| `feature_matrix_fresh.json` | Feature matrix for the fresh corpus (current, referenced by `reference_ads.py`) |
| `model_training_report_fresh.json` | Cox/XGBoost training report, fresh corpus (current, referenced by `guide.py`) |
| `success_score_report_fresh.json` | Composite success-score + SHAP report, fresh corpus (current, referenced by `guide.py`) |
| `supplements_enriched.json` | The original 2,736-ad corpus, landing-page-enriched (current for that corpus) |
| `feature_matrix.json` | Feature matrix for the original corpus |
| `*.pre_*_backup_*.json` | Point-in-time safety backups taken before a risky reprocessing pass — historical record, not live inputs |
| `cognitive_golden_set.json`, `feature_validation_report*.json/.md` | Step 2 validation artifacts |

---

## 10. Documentation map

| Doc | Covers |
|---|---|
| `CLAUDE.md` | Coding conventions, environment, current focus — **read every session** |
| `CONTEXT.md` | Domain glossary — **read every session**, and update inline (domain-modeling discipline) as new terms firm up |
| `docs/PROJECT-HANDOVER.md` | This file |
| `docs/adr/ADR-001..004` | Retired-product decisions (LiteLLM, Pydantic framing, platform adapters, vault structure) — historical, superseded by ADR-005 |
| `docs/adr/ADR-005-supersede-media-ai-platform.md` | Why/how the current project superseded the old one |
| `docs/adr/ADR-006-step2-ensemble-extraction.md` | Step 2's deterministic-first ensemble + fallback design |
| `docs/adr/ADR-007-critique-first-architecture.md` | Critique-first framing decision |
| `docs/adr/ADR-008-layered-color-vlm-imagery-retrieval-embeddings.md` | Layer Decomposition, retrieval-grounded suggestions, the Replicate-exception precedent Generation's clients follow |
| `docs/architecture-review-ingestion.md` | Step 1 architecture |
| `docs/extraction-failure-modes.md` | Step 1/2 live bug catalog |
| `docs/generation-v1-architecture.md` | Generation's full architecture (current = v2) |
| `docs/generation-failure-modes.md` | Generation's live bug catalog (14 entries) |
| `docs/meta-ad-image-model-stack.md` | Model-choice research for Generation's image-generation clients |
| `docs/meta-ads-signal-extraction-spec.md` | Meta Ad Library field reference |
| `docs/blueprints/*` | **Original pre-build engineering specs — several are confirmed stale** (e.g. the Step-1 Stage-4 blueprint predates ZenRows/tiered_scraper entirely). Treat as historical intent, verify against actual code before relying on one. |
| `docs/agents/issue-tracker.md`, `triage-labels.md`, `domain.md` | Agent-skill references for this repo's GitHub workflow |
| Wayfinder maps [#1](https://github.com/hmcg-bs/media-ai-platform/issues/1), [#36](https://github.com/hmcg-bs/media-ai-platform/issues/36), [#42](https://github.com/hmcg-bs/media-ai-platform/issues/42) | The living decision logs — richer and more current than any static doc for "what was decided and why" |

---

## 11. Conventions every sub-agent must follow

These are load-bearing, repeatedly-enforced patterns in this project's actual
history — not aspirational:

- **Deterministic-first.** Never send something to an LLM that code/math can
  compute (colour, dimensions, OCR geometry). This is `CLAUDE.md`'s own
  stated principle and the direct cause of Round 7's Generation rescope (a
  vision model was re-deriving a dimension the guide already measured
  statistically — corrected).
- **`os.environ` only in `pipeline/config.py::get_settings()`.** No exceptions
  elsewhere.
- **All generative AI calls via `google-genai` against Vertex AI**
  (`GenAIClient`), except the ADR-008-scoped Replicate exceptions
  (`replicate_client.py`) — never import `litellm`/raw `anthropic`/`openai`.
- **Live-verification discipline.** A fix is not "done" until it's been run
  against real data/API calls and the actual output inspected (pixel zoom for
  Generation, direct sampling for ingestion). Every entry in both
  failure-modes catalogs follows symptom → root cause → fix → regression test
  → **confirmed live** — don't break that pattern by marking something fixed
  from code review alone.
- **Report negative/partial findings honestly.** This project's own history
  repeatedly documents "fix worked but X still fails" or "mitigated, not
  eliminated" rather than overclaiming — see bugs #6/#12/#13/#14 in the
  Generation catalog. Preserve this norm; it's what makes the catalogs
  trustworthy as a "check here before re-discovering the same bug" resource.
- **Wayfinder protocol** (`mattpocock-skills:wayfinder`): one map per major
  effort, tickets as GitHub sub-issues, round-by-round narrative logged in
  the map's own Notes section. Don't fragment a map's own history across
  multiple docs — the map is the primary narrative record, static docs
  extract the *reusable* content (architecture, bug catalog) from it.
- **Domain-modeling discipline**: update `CONTEXT.md` inline the moment a new
  term firms up, not batched. Challenge fuzzy language against the existing
  glossary before using a new term loosely.
- **Testing**: hand-rolled fakes, no mocking libraries beyond pytest's own
  `monkeypatch` fixture; one test per real, found failure mode; offline by
  default (`uv run pytest pipeline/tests` needs no GCP credentials).
- **Git discipline**: never commit or push without explicit user
  authorization for that specific action; never force-push; new commits over
  amends; review `git status`/diff content before staging broadly.
- **Never extend `archive/old-src/`, `frontend/`, top-level `tests/`** — dead
  code from the superseded product.
