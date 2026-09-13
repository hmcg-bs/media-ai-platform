# Meta Ads learning and image generation: joint evidence plan

**Status:** Partially implemented on the Meta feature branch; human-dependent gates remain blocked
**Date:** 2026-09-12  
**Scope:** Supplements static-image ads  
**Decision authority:** No branch may be pushed or merged into `master` without explicit owner
authorization.

This plan joins the Meta Ads observation/model pipeline and the image-generation pipeline without
allowing either to manufacture evidence for the other. It is based on the verified local branch
states and artifacts below. It does not claim conversion performance: the available public
commercial-ad data contains no ROAS, spend, conversions, or dependable engagement ground truth.

## Verified baseline

### Branches

| Workstream | Local branch | Evidence baseline head | Base | Verification |
|---|---|---|---|---|
| Meta data/model | `auto/meta-ads-model-pipeline` | `5f9e501aea8a3d1c647f948cb58a974bf092a45b` | `b0951916ff24981a218247606f9fa29d410a5a07` | Pipeline 446 tests; ingestion 446 tests; focused warehouse/survival 50 tests; Ruff and diff check passed |
| Generation | `feat/generation-quality` | `9c6aa0e115bf8ee72b57f40a42031837ac9fb951` | `b0951916ff24981a218247606f9fa29d410a5a07` | 470 tests and Ruff passed; latest paid imagery hardening has not been live-verified |

Both workstreams were local and clean when the evidence baseline was recorded. Neither has been
pushed or merged. This plan and its session records are the only later Meta-branch changes. The
generation branch has nine reviewable commits from `1b42852` through `9c6aa0e`; the Meta branch keeps
implementation and evidence/logging changes in separate commits. Branch heads are operational
provenance, not a request to integrate either branch.

### Meta evidence

- The Supplements corpus contains 3,555 unique image ads from 12 searches and 1,151 pages.
- The reproducible extraction sample contains 600 ads: Cloud Vision OCR completed for all 600,
  cognitive extraction completed for 598 with two explicit fallbacks, and embeddings exist for all
  600.
- The advertiser-disjoint temporal split is 499 training rows and 101 newest-advertiser test rows,
  with zero advertiser overlap.
- The accepted model omits embeddings: holdout MAE is 0.3550 versus the training-median baseline's
  0.3731, and holdout top-20% precision is 0.3333. Embeddings made both MAE and ranking precision
  worse and are excluded.
- The target is a public-data composite proxy, not conversion performance: 70% longevity rank,
  20% longevity × scaling rank, and 10% variant rank. Brand, collection, and campaign decisions can
  confound it.
- Only `other supplements` clears the current 10-row descriptive segment gate. The newest Q3
  cohort is materially shifted. Cross-subcategory and trend conclusions are unsupported.
- The initial keyword scrape is not a product taxonomy. Pets, topical products, equipment, and
  ambiguous products can contaminate Supplements training unless manually excluded. The seeded
  120-row stratified taxonomy file is not yet labelled.
- BigQuery contains 3,555 ad snapshots, corrected lifecycle observations for all 3,555 ads, and 613
  distinct feature snapshots. BigQuery streaming produced 614 physical feature rows; readers must
  use logical content IDs/latest-row selection because streaming de-duplication is best effort.
- Only two warehouse ads are explicitly inactive. The model sample has zero training and zero test
  events. Active-ad `end_date` values from the actor are delivery/scrape-window boundaries, not
  observed stops. All 30/60/90/180-day survival horizons are therefore `insufficient_evidence`, and
  Cox guidance is prohibited.

### Contract currently accepted by generation

The v3 handoff is immutable and consists of:

| Artifact | Schema | SHA-256 |
|---|---|---|
| `.amp/in/artifacts/meta_model_handoff_supplements_generation_guide_v3.json` | generation guide | `63b0ecc8e3c88e8a9b5b7627aebf8441a5b084d054d3f23dade0b45d3556a110` |
| `.amp/in/artifacts/meta_model_handoff_supplements_training_report_v3.json` | `meta-ads-training-report-v2` | `2d9307fa9f671744ca053af545b3d191f39e81e2757d41ab95f9a534a463043e` |
| `.amp/in/artifacts/supplements_survival_validation_v1.json` | `survival-validation-v1` | `2dddda67eea155498dcdb14ee690b15649fa40cab7986a23115f274c1492d0cb` |

Generation independently re-derives and accepts exactly four visual and four copy directives from
the no-embedding SHAP evidence. It fails closed on schema/hash/provenance disagreement, non-zero Cox
events, non-empty Cox covariates, or changed longevity semantics. The exact accepted semantics are:
`is_active=false; legacy end_date heuristic only when activity status is absent`. V2's 19 apparent
events are superseded delivery-window boundaries, not evidence.

Embeddings, Cox coefficients, missing/default/pooled levels, campaign-operation fields,
non-directional importance, cross-category/trend claims, and reference pixels are not generation
instructions.

### Generation evidence and limitations

- The safe path generates a product-free scene plate, composites the original RGBA product pixels,
  and renders final copy/CTA deterministically. This prevents diffusion from rewriting product-label
  pixels and final copy.
- Product-free plates reduce duplicate products but cannot coordinate camera, surface plane,
  reflections, relighting, occlusion, perspective, or physically correct contact/cast shadows with
  the product. Product-conditioned generation previously produced duplicate/garbled products.
- The current NOVA source is a flat opaque illustration. Preserving it cannot make it a photographic
  packshot; placing it in a photographic/CG scene creates a dominant medium mismatch. Edge cleanup
  cannot restore absent source detail.
- Confirmed or recurring defects include pseudo-text/text-bearing props, duplicate/ghost products,
  weak grounding, scale/perspective mismatch, incompatible light direction, pasted-on cutout edges,
  approximate shadows, generic typography, weak hierarchy, and disconnected CTA treatment.
- Recent cutout, grounding, composition, and quarantine hardening is covered offline, but no fresh
  paid run has established improved publication quality. Stochastic behavior must be reported with
  sample sizes, never as fixed from unit tests.
- OCR-guided destructive cleanup remains off. Vision access must be checked in the generation
  checkout, and no labelled precision/false-positive benchmark currently justifies erasing pixels.
- Six-dimension comparison correctly requires art direction, imagery quality, typography, visual
  hierarchy, composition, and brand cohesion. It cannot pass without exactly one result for every
  dimension, but no approved durable reference corpus currently exists.

## Target architecture and feedback loop

```diagram
┌──────────────────────────────── META EVIDENCE PLANE ────────────────────────────────┐
│ Apify/Meta observations ─▶ append-only BigQuery snapshots ─▶ manual taxonomy gate │
│          │                         │                              │                 │
│          └─▶ lifecycle polls ──────┴─▶ frozen temporal windows ──┴─▶ Extraction   │
│                                                                       │            │
│                              advertiser-disjoint train/calibrate/test ◀┘            │
│                                             │                                       │
│                          model + baseline + calibration + SHAP                      │
│                                             │                                       │
│                         versioned report/guide/survival contract                    │
└─────────────────────────────────────────────┼───────────────────────────────────────┘
                                              │ constraints only
                                              ▼
┌────────────────────────────── GENERATION/QUALITY PLANE ─────────────────────────────┐
│ client-owned product + brand assets ─▶ medium/rights/quality admission             │
│                                             │                                       │
│ approved route: owned scene | product-free plate | experimental challenger         │
│                                             │                                       │
│ deterministic composition ─▶ hard technical gates ─▶ six-dimension comparison     │
│                                             │                    ▲                  │
│                                  quarantine or candidate             │              │
│                                                                  │                  │
│                         approved, immutable GCS benchmark corpus ┘                  │
└─────────────────────────────────────────────┼───────────────────────────────────────┘
                                              │
                          defects, reviewer labels, costs, latency
                                              ▼
                         generation evaluator/ranker experiments

Real future ad observations ─▶ next untouched Meta window ─▶ evaluate old model first
```

There are two deliberately separate feedback loops:

1. **Creative-quality loop:** deterministic defects and human six-dimension ratings improve the
   renderer, route selector, and eventual craft ranker. They are not Meta performance labels.
2. **Observed-performance loop:** only real ads observed in a later immutable collection window may
   evaluate the Meta proxy model. Generated candidates and reviewer preferences must never be fed
   into the proxy model as successful ads. If first-party conversions become available later, they
   require a separately versioned target and ADR; they must not silently redefine the current proxy.

## Interfaces and ownership boundaries

| Interface/artifact | Producer | Consumer | Required contract |
|---|---|---|---|
| Competitor ad and lifecycle snapshots | Meta ingestion | taxonomy, lifecycle, training | Append-only BigQuery rows; stable ad/page IDs; observation time; source/run ID; explicit activity status; logical de-duplication |
| Supplements taxonomy labels | Human data curator, sampled by Meta pipeline | extraction/training and benchmark selection | Stable ad ID; `is_supplement`; subcategory; reviewer/adjudication provenance; false/unlabelled rows fail closed |
| Feature snapshots/matrix | Extraction pipeline | model training | Master schema; input hashes; extraction/provider versions; explicit fallbacks/missingness; no secrets |
| Training report, generation guide, survival report | Meta model pipeline | generation guide loader | Versioned schemas and SHA-256; branch/commit/clean state; seed/workers; split counts; leakage/calibration metrics; exact exclusions |
| Approved benchmark manifest and images | Meta candidate ranker + human craft review + GCS archiver | generation evaluator only | Content-addressed WebP and manifest; ad/taxonomy/proxy provenance; rights/usage; approver/date; six-dimension suitability; `directive_aligned=false` |
| Brand asset manifest | Client/brand asset owner | generation route admission/compositor | Rights; product/SKU; medium; source resolution; alpha/matte; clipping; brand fonts/colors/logo; packshot/owned-scene relationship |
| Generation experiment manifest | Generation pipeline | evaluator, reviewer, scribe | Input/output hashes; v3 guide hash; asset/benchmark versions; route/model/config/seed; all gate outcomes; retries, cost, latency; quarantine status |
| Defect and six-dimension labels | deterministic evaluator + blinded human reviewers | generation regression suite/ranker | Rubric version; per-dimension result; defect class/severity; reviewer agreement; no proxy-performance claim |

Reference images are comparison-only. They cannot contribute pixels, product identity, wording,
people, scenes, layouts, or imitated style to generation. Owned scenes are separate client inputs and
may be composed into; their manifest must not misclassify them as successful-ad evidence.

## Objective gates

Thresholds below are proposed promotion criteria. Freeze them before exposing the relevant test
partition; do not tune a threshold after seeing final-test outcomes.

### M0 — provenance and reproducibility

**Entry:** immutable raw observation window and configuration manifest exist.  
**Exit:** input/artifact hashes recompute; schemas match; Git branch/commit/clean state, seed,
worker count, provider/model versions, and proxy definition are present; advertiser overlap is zero;
no credential appears in logs/artifacts; same inputs and seed reproduce split IDs and guide. Any
mismatch blocks promotion.

### M1 — Supplements taxonomy quality

**Entry:** the stratified 120-ad review set is frozen with contamination-risk and candidate
subcategory strata.  
**Tasks:** label `is_supplement` and subcategory; double-label at least 20%; adjudicate every
disagreement; audit accepted and rejected boundary cases.  
**Exit:** every sampled row has an explicit label and provenance; agreement κ ≥ 0.80 on the
double-labelled subset; 100% of disagreements are adjudicated. Training remains restricted to
explicit `is_supplement=true`. If κ misses the gate, revise the taxonomy rubric and relabel before
expanding ingestion.

### M2 — collection, censoring, and segment support

**Entry:** taxonomy gate is usable and BigQuery/GCS writes pass health checks.  
**Tasks:** poll tracked IDs on a stable cadence; preserve complete/partial/unknown poll semantics;
freeze each new scrape as a separate window; report completeness, misses, explicit/inferred events,
and censoring by window/subcategory/advertiser.  
**Exit for horizon reporting:** retain ADR-009's per-horizon minimum of 20 known outcomes and five
endings.  
**Exit for Cox-derived directives:** use the code's five-train-event threshold only as a fit safety
floor; require at least 30 train events and 10 untouched-test events, admissible test pairs, stable
coefficient direction under resampling, and advertiser-disjoint evaluation before any Cox signal may
become instructional. Until then Cox remains empty and explicitly unevaluable.

Do not infer an event from `end_date` when activity status says active. One complete miss remains
unknown; interval-censored disappearance needs the accepted repeated-complete-miss rule.

### M3 — proxy model and temporal calibration

**Entry:** M0/M1 pass; a frozen outer temporal advertiser holdout has not been used for feature,
parameter, pooling, or threshold choices.  
**Tasks:** compare no-embedding and embedding challengers; tune only in training with temporal inner
splits; report MAE, R², top-20% precision, baseline, calibration by quarter/subcategory, bootstrap
uncertainty, feature drift, and advertiser/segment coverage.  
**Exit:** the paired 95% bootstrap interval for challenger-minus-baseline absolute error is below
zero on the untouched window; top-20% precision's lower interval exceeds the 20% prevalence baseline;
zero advertiser overlap; no unsupported segment is summarized as generalizable. A segment becomes
supported only with at least 100 labelled ads, 10 advertisers, representation in train and untouched
test, and a pre-registered calibration comparison. A new temporal window is evaluated by the old
model before it may enter later training.

The current v3 model remains provisional: it beats the point baseline but has only one window,
limited segment coverage, and measured Q3 shift. Improvement of model parameters alone is not a
release criterion.

### B0 — successful-ad benchmark approval and persistence

**Entry:** M1 labels exist; fresh image URLs or downloaded bytes are available.  
**Tasks:** rank candidates using the bounded proxy, enforce advertiser diversity, human-review craft
quality and rights, archive no more than eight initial references as 1600 px WebP q82 with immutable
hashes and manifests.  
**Exit:** every reference is an explicitly labelled Supplement, independently approved by at least
two reviewers, has provenance/usage constraints and all six craft dimensions annotated, remains
retrievable by hash, and is marked `directive_aligned=false`. No expired CDN URL is a persisted
reference. Freeze the reference-corpus version before generation tuning; generated examples used to
calibrate the reviewer rubric must remain separate from later blinded generated test candidates.

The measured storage mean is 85,415 bytes per compressed ad; eight references are approximately
0.65 MiB. Archiving the full 3,555-ad corpus would be about 0.283 GiB and approximately $0.006/month
at the cited storage-only rate, excluding requests and egress.

### G0 — asset admission and route selection

**Entry:** client supplies product and brand assets with usage rights.  
**Tasks:** classify source medium as photographic, illustrated, or 3D; validate resolution,
silhouette completeness, clipping, alpha/matte, label legibility, and ownership; distinguish
packshots from owned scenes.  
**Exit:** photographic routing requires a real transparent packshot or a compatible owned
photographic scene. Illustrated/3D inputs use a medium-matched route or approved owned scene. Opaque,
clipped, low-resolution, rights-unknown, or medium-incompatible assets fail closed with actionable
reason codes. A scene reference cannot substitute for a product identity fixture.

### G1 — deterministic technical safety

**Entry:** G0 passes and exact v3/benchmark manifests are pinned.  
**Tasks:** check product pixel fidelity, duplicate products, scene pseudo-text/text-bearing props,
final-copy OCR, clipping/bleed, safe zones, product/text collisions, contrast, alpha-edge contamination,
and output quarantine.  
**Exit:** exactly one product; zero detected pseudo-text/text-bearing props; exact expected final copy;
zero product-label regressions; zero clipping, bleed, collision, or safe-zone failures; WCAG contrast
for deterministic copy; and all required gate records present. Failed candidates are never written
to a deliverable path, even after retries.

Halo/source-fidelity thresholds must be calibrated on labelled real and synthetic fixtures before
use. Prompt assertions are not proof of model compliance.

### G2 — OCR cleanup safety

**Entry:** generation checkout passes a Vision health check using established WIF/config, and a
labelled benchmark contains ordinary scene text, pseudo-text, protected product-label text, and
text-like non-text detail.  
**Tasks:** measure detection precision/recall separately from erasure safety; retain before/after
artifacts and protected-region masks.  
**Exit to enable cleanup:** ≥99% precision in pixels selected for destructive cleanup, ≥95% recall
on labelled scene pseudo-text, zero protected product-label deletions in the benchmark, and no
material non-text erasure in blinded review. Otherwise OCR remains detection/reporting-only.

### G3 — physical coherence

**Entry:** G0/G1 pass; approved source-specific fixtures exist.  
**Tasks:** blinded human labels for medium compatibility, scale/perspective, surface contact,
light direction, contact/cast shadow geometry, ambient spill/reflection, edges, and occlusion. Compare
owned-scene composition first, product-free plate plus grounded composite second, and any
identity-controlled product-conditioned method only as a challenger.  
**Exit:** discovery pilot of at least 30 outputs per route identifies no systematic critical defect;
promotion run uses at least 100 outputs for the chosen route, with zero critical identity/duplicate/
protected-text failures (giving an approximately 3% one-sided 95% upper failure bound when zero are
observed), median physical-coherence rating ≥4/5, and reviewer agreement κ ≥0.70. Report by source
medium; do not pool illustrated and photographic results.

### G4 — six-dimension craft and release

**Entry:** B0 and G1 pass; the reference-corpus version, generation baseline, and blinded generated
test partition are frozen.  
**Tasks:** exactly one evaluation for each required dimension; deterministic checks first, then
blinded reviewers; compare candidate layouts/typography and routes without showing proxy ranks.  
**Exit:** all six dimensions are present; median rating ≥4/5 for every dimension; no critical defect;
at least two of three reviewers approve; and the challenger improves both publication-ready pass rate
and blinded pairwise preference over the frozen baseline with a 95% interval excluding no improvement.
Cost, latency, retry/quarantine rates, and outcomes by medium are mandatory. Missing references or a
missing/duplicate dimension is a failure, never a skipped pass.

## Prioritized phases and tasks

### Phase 0 — freeze contracts and baselines (P0; sequential foundation)

**Meta owner:** preserve v3 and corrected lifecycle semantics; publish immutable logical queries for
latest snapshots.  
**Generation owner:** pin v3 hashes, quarantine failed outputs, and capture the current deterministic
and latest paid baseline manifests without calling either publication-ready.  
**Exit:** M0 passes; old v2 semantics cannot be selected accidentally; failed candidates cannot enter
approved-output storage.

### Phase 1 — human truth and production inputs (P1; parallel)

1. **Taxonomy lane — Meta:** complete M1 and expand only after agreement is adequate.
2. **Benchmark lane — joint:** select, review, download, and archive B0 references.
3. **Brand-asset lane — Generation/client:** obtain real transparent packshots, medium-compatible
   owned scenes, brand fonts/palette/logo, and rights metadata; implement the G0 review rubric later.
4. **Lifecycle lane — Meta:** continue immutable polling and window collection without changing the
   v3 event definition.
5. **OCR evidence lane — joint:** verify Vision access and build the G2 labelled benchmark; keep
   cleanup disabled.

**Exit:** clean taxonomy, durable references, and production-compatible product fixtures exist. More
generation prompt tuning before these exits is low-value and must not be presented as progress on
publication quality.

### Phase 2 — calibrate measurement (P2; mostly parallel after Phase 1 inputs)

**Meta:** freeze next-window split; add uncertainty/drift reporting and segment support gates; evaluate
v3 unchanged before retraining.  
**Generation:** label duplicate/pseudo-text/OCR/collision/clipping/halo/source-fidelity fixtures;
calibrate G1/G2 and the six-dimension rubric; measure inter-rater agreement.  
**Joint:** version benchmark, taxonomy, guide, and evaluator schemas in one experiment manifest.

**Exit:** evaluation can reject plausible wrong implementations and is not calibrated on the final
test set.

### Phase 3 — controlled architecture challengers (P3; sequential on Phase 2)

Run bounded, cost-recorded experiments in this order:

1. compatible owned-scene composition with deterministic text;
2. product-free plate plus improved physically grounded compositing;
3. identity-controlled product-conditioned generation only if real packshot fixtures and duplicate/
   protected-text gates can prove product integrity;
4. medium-matched illustration/3D route rather than pretending those sources are photographic.

Use the same assets, copy, v3 guide, benchmark partition, and reviewer protocol across routes. Do not
change model, route, prompt, and evaluator simultaneously. G1 is required before paid human craft
review; G3 selects the physical architecture.

### Phase 4 — composition, typography, and craft ranking (P4; sequential on B0/G3)

Generate multiple deterministic layout/typography candidates, rank first by hard constraints, then
by calibrated six-dimension evidence. Keep the 4+4 v3 directives as bounded correlational constraints,
not an art director. A learned craft ranker is allowed only after sufficient approved human labels;
train/calibration/test examples must be split by product/brand and generation run to prevent near-
duplicate leakage.

**Exit:** G4 passes against the frozen baseline. Otherwise retain the baseline and record the failure.

### Phase 5 — temporal model refresh and closed-loop validation (P5; sequential on a new window)

Evaluate v3 against the untouched future Meta window before any retraining. If M1–M3 pass, train a
versioned challenger and issue v4 artifacts. Generation must validate v4 in shadow mode and
independently re-derive supported directives before it can become default. Changes in directives
trigger a new generation evaluation; they do not invalidate prior benchmark ratings.

No survival directive ships until M2's higher promotion gate passes. No conversion claim ships until
first-party conversion ground truth exists under a separately approved design.

## Dependency-aware execution order

```diagram
                   ┌─ taxonomy review ───────────────┐
v3 freeze/baseline ├─ lifecycle polling/windows ─────┼─▶ next-window model evaluation ─▶ v4 shadow
                   ├─ packshots/owned scenes ────────┐
                   ├─ benchmark approval/archive ────┼─▶ gate calibration ─▶ route experiments
                   └─ OCR auth + labelled fixtures ──┘                         │
                                                                               ▼
                                                          six-dimension craft comparison
                                                                               │
                                                                               ▼
                                                                  promotion or rollback
```

Can run in parallel now: taxonomy review, lifecycle collection, asset acquisition, benchmark human
review, OCR fixture design/auth check, and cost/latency instrumentation. Must remain sequential:

- taxonomy approval before taxonomy-gated training or benchmark selection;
- benchmark persistence before reference-based craft claims;
- asset admission before route comparison;
- labelled OCR fixtures before destructive cleanup;
- calibrated hard gates before paid architecture promotion;
- frozen baseline before challenger runs;
- old-model evaluation on a new temporal window before retraining on it;
- human labels before a learned craft ranker;
- model evidence gate before a new generation handoff becomes default.

## Data, IAM, and operational prerequisites

- GCP project `clean-patrol-496108-m9`, BigQuery dataset `ad_intelligence` in `us-central1`, and GCS
  bucket `clean-patrol-496108-m9-media-ai-ads` with uniform bucket-level access.
- The orb principal has dataset Writer/Data Editor, bucket Object Admin, project BigQuery Job User,
  Logging Viewer, Monitoring Viewer, Service Usage Consumer, and Vertex AI User. Verify Cloud Vision
  API enablement and runtime access from each checkout rather than inferring it from role names.
- Configure only through `pipeline/config.py::get_settings()`: `GCP_PROJECT_ID`,
  `BIGQUERY_DATASET`, `GCS_AD_BUCKET`, WIF/ADC variables, `APIFY_API_TOKEN`, and
  `REPLICATE_API_TOKEN`. Never accept tokens as command arguments or serialize them.
- Set `GCS_AD_BUCKET=clean-patrol-496108-m9-media-ai-ads` in the Amp project environment if archive
  commands should work without explicit `--bucket`; explicit bucket selection is already usable.
- Keep paid API output checkpointed before warehouse writes. Use exclusive sidecar locks and distinct
  output/report paths for concurrent jobs; cap per-model workers; use append-only BigQuery and
  content-addressed, generation-preconditioned GCS writes.
- Assign run IDs and immutable manifests before paid calls. Resume by checkpoint/run ID; never rerun a
  paid call merely because persistence failed.

## Existing workflow ownership

Use the existing Generation Development Crew roles rather than adding speculative autonomous agents:

| Role | Joint-plan responsibility |
|---|---|
| Foreman | Own dependency graph, paid-run budget, frozen gates, and one bounded work item at a time |
| Pipeline coder | Implement one reviewable Meta or generation change on the owning feature branch; never merge |
| Ad evaluator | Run deterministic Track A, visual Track B, and six-dimension/physical-coherence rubric; surface disagreements |
| Researcher | Resolve only specific provider/API/model unknowns with primary sources; no paid runs |
| Code reviewer | Review actual diff and files, test quality, secrets/config, concurrency, structural guarantees, and overclaims |
| Scribe | Update architecture, failure catalog, ADR/plan status, run artifacts, and verified sample sizes |

Two decisions remain human-owned rather than delegated to an agent: **Dataset Curator** for taxonomy
adjudication and **Brand/Creative Reviewer** for benchmark rights and craft approval. AI suggestions
may prioritize rows but cannot supply those labels.

For each implementation cycle, the foreman should require: bounded task and gate → coder change →
offline tests → code review → paid run only when authorized/budgeted → evaluator verdict → scribe
record. Parallel research or evaluation is allowed only when it does not share mutable outputs.

## Risks and rollback strategy

| Risk | Control | Rollback boundary |
|---|---|---|
| Proxy mistaken for conversion performance | Explicit proxy naming, no ROAS fields, temporal/advertiser holdout | Revert guide version; model artifacts remain immutable |
| Taxonomy contamination | Human labels, double-review agreement, false/unlabelled fail closed | Exclude label-set version and rebuild derived matrix |
| Lifecycle misclassification/censoring | Explicit activity semantics, conservative misses, append-only corrections | Select corrected report by version/time; never delete audit rows |
| Temporal/category drift | Frozen windows, old-model-first evaluation, support thresholds | Retain v3; reject challenger or disable unsupported segment |
| Benchmark leakage or imitation | Comparison-only manifest, blind partition, `directive_aligned=false` | Disable benchmark version; retain images for audit only |
| Medium/identity mismatch | G0 route admission and real packshot/owned-scene requirement | Route back to owned scene/manual production or fail closed |
| Pseudo-text, duplicate, OCR damage | G1/G2 deterministic gates and quarantine | Disable OCR cleanup/challenger; deterministic copy remains |
| Grounding/lighting/perspective failure | Source-stratified G3 comparison | Re-enable frozen baseline route; no failed deliverable promotion |
| Evaluator drift or false confidence | Fixed rubric, exact six dimensions, blinded humans, agreement metrics | Pin prior evaluator/schema and re-score stored candidates |
| Concurrent job collision or partial writes | Locks, distinct paths/run IDs, atomic checkpoints, immutable storage | Resume same run; logical de-dup; never overwrite evidence |
| Provider/API/model drift | Versioned clients/config and health checks | Pin previous provider/model/config manifest |
| Credential disclosure | Settings-only secrets, structured redaction, artifact scans | Revoke/rotate credential and invalidate affected artifacts |

Every generation route, grounding method, layout/typography solver, OCR cleanup step, evaluator, and
learned ranker should be independently selectable. Preserve the deterministic compositor and frozen
baseline. Store generated candidates separately from approved outputs, and never relax quarantine to
make a rollback appear successful. Promote manifests and aliases; do not mutate historical artifacts.

## Immediate next decision package (no implementation in this plan)

1. Assign humans to complete the 120-row taxonomy and approve benchmark candidates.
2. Supply a real NOVA transparent packshot or a medium-compatible owned scene plus brand assets and
   rights metadata.
3. Authorize and budget recurring lifecycle polling separately; no scheduler is created implicitly.
4. Confirm whether Generation may use the project's Vision-enabled WIF path for a non-destructive OCR
   benchmark.
5. After inputs exist, freeze baseline/gates and authorize the first controlled paid experiment.

Until those dependencies are satisfied, the evidence-backed default is v3's exact 4+4 guide,
no Cox/trend/category directions, OCR cleanup off, no six-dimension pass, and no claim that current
generated imagery is publication-ready.

## Meta implementation status (2026-09-12)

The Meta branch now implements the unblocked P0–P2 workflow without changing v3 evidence:

- `pipeline.validation.taxonomy_adjudication` creates a blank, versioned review artifact and enforces
  the fixed M1 policy (all rows labelled, at least 20% independent double-review, binary κ ≥ 0.80,
  and independent adjudication with rationale for every binary or subcategory disagreement). The
  passing report cryptographically binds the exact approved-label content. Reviewers cannot lower
  policy thresholds in the artifact. No labels have been supplied by code.
- `pipeline.supplements_workflow` requires the passing adjudication report and matching approved
  labels. Training reports independently reject ads without one coherent human-adjudication sample
  provenance. Adjudicated subcategory takes precedence over keyword/landing-page heuristics.
- training now reports deterministic paired bootstrap intervals for MAE improvement, calibration
  gap, and fixed top-20% precision; raw-feature PSI/total-variation drift; advertiser counts and
  conservative segment support; and an objective promotion decision. Drift remains descriptive.
- `pipeline.model_training.observation_windows` freezes byte-bound baseline/future windows and
  requires a later chained window with new ad IDs. `evaluate_future_window` reconstructs the frozen
  no-embedding specification on old training rows only, rejects stale proxy targets, requires at
  least 30 days represented by each active ad's immutable snapshot (explicit inactive outcomes are
  complete), checks taxonomy and advertiser isolation before fitting, and never tunes on the future
  rows. Waiting after a stale snapshot does not make its target mature.
- `pipeline.model_training.handoff_bundle` freezes the three exact accepted v3 files by their known
  SHA-256 values into an atomic immutable bundle, independently re-derives directives, and verifies
  all files before transfer. A new version requires `promotion_decision.status=promoted`; it never
  overwrites v3. Generation must add explicit bundle discovery before any v4 bundle can be selected.
- Every report/bundle restates loop isolation: generated outputs are prohibited as Meta performance
  labels. Failed Generation candidates remain quarantined by the Generation owner and have no path
  into this taxonomy-gated training workflow.

Reproducible local artifacts are under `.amp/in/artifacts/`: the blank
`supplements_taxonomy_review_v1.json`, frozen
`supplements_observation_window_baseline_v1.json`, and
`handoffs/supplements-v3-bundle-v2/manifest.json`. Bundle v2 corrects only the survival role's
consumer-compatible relative filename; its evidence bytes and hashes are unchanged. The first
manifest remains immutable. These are workflow evidence, not completed human review or new model
evidence. M1 is blocked on human labels; future-window evaluation is blocked on a genuinely
later scrape with mature targets; survival remains blocked on sufficient observed endings; and B0
remains blocked on rights and craft approval.

### Product-site evidence boundary (2026-09-13)

- The Supplements workflow now places landing-page enrichment after human taxonomy approval and
  before Step 2/matrix/training. It runs the free Shopify/structured tier first, then ZenRows once
  per unresolved unique URL, with atomic checkpoints, output locks, and resume support. The paid LLM
  advertorial fallback remains a separate explicit decision.
- `ZENROWS_API_KEY` and the owner-provisioned `ZENROWS_API` alias are accepted only through
  `pipeline.config`; neither the key nor request credentials enter commands, reports, or logs.
- A versioned `product-enrichment-coverage-v1` report hashes the exact approved enriched corpus and
  fails training below fixed product-page/price/description coverage. Per-field evidence needs at
  least 100 rows and 10 advertisers before it is considered model-supported.
- The matrix uses categorical price tier, rating, log review volume, description/USP presence and
  lengths, subscription status, and existing variant/cultural signals. Missing extraction remains
  explicit. Product features are X-axis context only; generated outputs and landing-page metadata
  are never proxy outcomes.
- Future generation reports may carry this gate, but rating or price-tier directives are removed
  unless their field-specific row/advertiser support passes. Frozen v3 has no product gate and is
  unchanged. No v4 handoff or model-performance claim exists until human taxonomy labels, a real
  enrichment run, and untouched temporal evaluation exist.

### Uncertainty and proxy sensitivity implementation (2026-09-13)

- New model and frozen-future-window evaluations now resample whole advertisers rather than rows
  when estimating MAE-difference, calibration-gap, and fixed top-20% precision intervals. Reports
  identify `paired_advertiser_cluster_bootstrap`, advertiser count, and top-set advertiser count.
  The objective promotion policy fails closed unless this method is present and its cluster count
  equals the reported holdout-advertiser count.
- `pipeline.model_training.proxy_sensitivity` runs seven pre-registered 70/20/10 perturbation and
  leave-one-ingredient-out scenarios on one fixed advertiser-temporal split. It records input byte
  hashes, Git provenance, per-scenario clustered uncertainty, proxy-rank agreement, top-set Jaccard,
  runtime, Tree-SHAP rankings, and cross-scenario sign stability. It is explicitly analysis-only:
  it cannot select weights, emit guidance, or change immutable v3.
- Reproduce after the current human taxonomy and product-enrichment gates pass:

  ```bash
  uv run python -m pipeline.model_training.proxy_sensitivity \
    --matrix data/supplements_feature_matrix.json \
    --ads data/supplements_taxonomy_approved_product_enriched.json \
    --out data/supplements_proxy_sensitivity_v1.json \
    --workers-per-model 1
  ```

- A diagnostic run against the existing pre-taxonomy matrix is retained only to validate the
  harness and quantify why promotion remains blocked. Its `input_eligibility.status` is `blocked`;
  it is not new model evidence. The current 120-ad taxonomy artifact is still blank, so the workflow
  correctly stops before any full-corpus paid ZenRows run. Once humans complete adjudication, the
  existing restartable free-first/ZenRows cascade is the next sequential step; coverage and field
  support must then pass before model training or BigQuery persistence.
- The immutable diagnostic output is
  `docs/artifacts/supplements_proxy_sensitivity_pre_taxonomy_v1.json` (SHA-256
  `2b5f8d9da35e4157b5d30417b6192464528ecc2df2e5d981237e17df8da28257`). It binds matrix
  SHA-256 `791308cb4fbf9f7db94294044ecc31e303a7614609b1d38dbf7acda772a42741` and ads
  SHA-256 `87d47947e457463f68fb5b0607b872a155bcbf43d9ae60a0c9510ccb0a9b28a2` to clean
  commit `b400eb61bd198c7db6dfa67f487765e62ccdc8f6`. Under clustered uncertainty, the
  baseline scenario has MAE 0.3509 versus 0.3731, but top-20% precision is only 0.1905 with
  95% interval [0.0500, 0.34783]; thus ranking promotion fails. This result is diagnostic because
  both taxonomy and product-enrichment eligibility are failed, not a replacement for v3 evidence.
