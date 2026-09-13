# Current Meta Ads model/data and image-generation pipelines: research briefing

**Evidence cut:** 2026-09-13  
**Repository:** `hmcg-bs/media-ai-platform`  
**Scope:** current Supplements static-image work, including two local-only feature branches that are not present on the remote  
**Audience:** research agents and engineers deciding what to test next  
**Status:** evidence synthesis; not a deployment, merge, model promotion, or publication-quality claim

## Executive decision summary

The project has two deliberately non-contaminating loops:

1. The **Meta evidence loop** observes real public competitor ads, preserves their lifecycle and extraction provenance, learns correlational associations with a bounded public-data success proxy, and emits a versioned SHAP-derived guidance bundle.
2. The **generation-quality loop** uses only rights-approved client assets plus that bounded guidance, generates or reuses product-free scenes, composites protected source pixels and text deterministically, quarantines failures, and evaluates technical safety and craft quality. Generated images, generated defects, and reviewer preferences are never Meta success labels.

The current accepted handoff is frozen **global v3**, containing exactly four visual and four copy directives. It is useful as a constrained baseline, but it is not a realism model, conversion model, causal creative strategy, or generally promotable performance model. Its advertiser-disjoint 101-row holdout has MAE 0.3550 versus 0.3731 for the training-median baseline, but R² is −163.426 and its Q3 mean prediction exceeds the mean target by 0.3550. Only `other supplements` reaches even the old 10-row descriptive segment threshold. No survival directive is admissible.

The current generation branch has materially stronger **structural safety**: strict rights/hash admission, product-free and owned-scene routes, source-pixel preservation, deterministic layout/type/contrast/grounding gates, quarantine, non-destructive OCR checks, six-dimension review, and a quarantine-only automatic product-extraction challenger. It does **not** yet have evidence that generated ads are photorealistic or publication-ready. No approved real packshot/scene corpus, successful-ad benchmark, labelled OCR benchmark, real ROI/matte benchmark, blinded craft evaluation, or paid promotion run exists.

The immediate critical path is therefore evidence acquisition, not another unbounded model or prompt swap: complete the human taxonomy; obtain rights-approved packshots/owned scenes and benchmark ads; run product enrichment; mature a later immutable Meta window; curate ROI/matte and OCR benchmarks; then run frozen, cost-recorded comparisons.

## 1. Evidence basis and branch topology

### 1.1 Sources inspected

This briefing reconciles:

- `CLAUDE.md`, `CONTEXT.md`, ADR-005 through ADR-009, architecture/failure-mode docs, and current `master` implementation/tests;
- full Changes captures transferred from the model thread `T-01a0943f-f7ab-766d-8041-ee12dcf94ca4` and generation thread `T-01a0943f-bf14-7346-a445-23cc3c2bfb6e` into temporary directories;
- committed feature-branch docs, code, tests, v3 reports/manifests, durable session records, and thread-reported test outputs;
- ignored model artifacts explicitly downloaded from the model thread: the blank taxonomy review, baseline observation window, and corrected v3 bundle-v2 manifest;
- source-thread reconciliation reports confirming both final feature-branch heads are clean and all transferred implementation bytes are committed.

No claim below assumes that a thread message transferred code. Branch files were inspected from explicit Changes/file transfers. Local feature commits cannot be fetched from GitHub because neither branch was pushed.

### 1.2 Exact ref relationship

```diagram
                                 local-only; not on origin
              ┌──────────────── auto/meta-ads-model-pipeline @ a013f8c…
              │                  30 commits / clean
              │
b095191… ─────┼──────────────── local master == origin/master
              │
              └──────────────── feat/generation-quality @ db9908f…
                                 31 commits / clean
```

At this briefing's start:

- this checkout's local `master` and `origin/master` both resolved to `b0951916ff24981a218247606f9fa29d410a5a07`;
- there is no `main` or `origin/main`; references to “main” in generic instructions mean this repository's `master`;
- the model branch is local-only, clean at `a013f8c5d87e512788af616972c23562b82bf29d`, with 30 commits and 92 changed paths since the common base;
- the generation branch is local-only and clean at `db9908fab4d70a2638d9ac1f75d6a06b2246f9c2`, with 31 commits and 77 changed paths since base;
- this document is authored on `docs/current-pipelines-research-brief`, forked from `b095191…`. It does not import either implementation delta and does not alter `master`.

### 1.3 Evidence vocabulary

- **Verified artifact/code fact:** directly inspected in transferred bytes or a committed repository artifact.
- **Verified run finding:** exact output was recorded by the owning thread, often with an artifact/hash; this briefing did not repeat paid calls.
- **Offline structural evidence:** tests prove control flow, schema, geometry, hashing, or fail-closed behavior, not provider accuracy or visual quality.
- **Hypothesis/proposal:** not yet supported by the required real dataset or frozen evaluation.
- **Blocked:** an explicit prerequisite is missing; code must fail closed rather than substitute an AI guess.

## 2. End-to-end architecture and the two loops

```diagram
┌──────────────────────────── META EVIDENCE LOOP ─────────────────────────────┐
│ Apify/Meta search + page inventory                                          │
│      │ paid raw/checkpointed results                                        │
│      ▼                                                                       │
│ normalized immutable ad snapshots ───────▶ GCS image archive (optional)     │
│      │                                  content-addressed + rights metadata  │
│      ├────▶ repeated lifecycle observations ─▶ KM/Cox evidence gate         │
│      │                                                                       │
│      ▼                                                                       │
│ 120-row human taxonomy/adjudication gate                                     │
│      │ only explicit is_supplement=true                                      │
│      ▼                                                                       │
│ Shopify/structured page extraction ─▶ ZenRows unresolved-URL backfill       │
│      │ product-enrichment-coverage-v1 + exact corpus hash                    │
│      ▼                                                                       │
│ Step 2: metadata/color + Cloud Vision OCR + Replicate cognitive extraction  │
│      │ optional retrieval embeddings, explicit missing/fallback states       │
│      ▼                                                                       │
│ immutable feature matrix ─▶ advertiser-disjoint temporal model evaluation   │
│      │ no-embedding primary + embedding ablation + survival                 │
│      ▼                                                                       │
│ report + SHAP guide + survival report ─▶ immutable hashed handoff bundle     │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │ constraints; never generated labels
                                       ▼
┌──────────────────────── GENERATION-QUALITY LOOP ────────────────────────────┐
│ rights/hash-bound packshot | extracted cutout | product-free owned scene    │
│      ▼                                                                       │
│ fixed v3 loader + asset/medium/route admission                              │
│      ▼                                                                       │
│ owned scene reuse OR Flux product-free scene plate                          │
│      ▼                                                                       │
│ source-pixel product composite + deterministic copy/type/layout/shadows     │
│      ▼                                                                       │
│ duplicate/layout/contrast/OCR/identity/cutout/grounding technical gates     │
│      ▼                                                                       │
│ immutable rights-cleared reference comparison + six human craft dimensions │
│      ├────────────▶ approved candidate (future; currently blocked)          │
│      └────────────▶ quarantine + defect/cost/latency labels                 │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │ creative-quality learning only
                                       └─▶ renderer/route/evaluator experiments

Later real deployed ads ─▶ new immutable Meta window ─▶ score OLD model first
```

### 2.1 Storage and provenance

Local paid-call boundaries use exclusive sidecar locks, atomic temporary-file rename, resumable run/checkpoint IDs, and distinct output paths for concurrent work. API tokens are settings-only through `pipeline.config.get_settings()` and must not appear in CLI arguments, logs, or artifacts.

ADR-009 defines append-only BigQuery tables:

| Table | Logical grain | Purpose |
|---|---|---|
| `competitor_ad_snapshots` | ad × observed content | Normalized ad/page IDs, dates, URLs, query provenance, proxy inputs. |
| `ad_feature_snapshots` | ad × feature content/schema | Master-schema extraction/matrix snapshots and provider versions. |
| `ad_lifecycle_observations` | ad × poll run | Active, explicitly inactive, complete miss, or unknown evidence. |
| `survival_validation_reports` | report input hash/time | Immutable horizon evidence. |

BigQuery streaming de-duplication is best effort, so readers must select logical content IDs/latest rows; physical row count is not truth. GCS references are stripped, bounded WebP objects addressed by content hash and written with generation preconditions. Manifests bind bytes, provenance, rights, and role.

Verified GCP state reported by the model branch: project `clean-patrol-496108-m9`, dataset `ad_intelligence` in `us-central1`, and bucket `clean-patrol-496108-m9-media-ai-ads`; 3,555 ad snapshots, corrected lifecycle observations for all 3,555 ads, and 613 logical feature snapshots (614 physical feature rows). IAM/WIF health was verified for the owning branch without exposing credentials. Every checkout must still verify its own runtime identity and API access.

### 2.2 Fail-closed boundaries

The design intentionally stops at uncertainty:

- unknown/false taxonomy labels cannot enter training;
- incomplete or mismatched product enrichment cannot be silently reused;
- one missing ad or an incomplete/capped page poll is not an ending;
- stale/mismatched hashes, dirty provenance, advertiser overlap, unsupported schemas, or unevaluable Cox evidence block handoff;
- unsupported segment guidance falls back to global v3;
- unknown rights, mismatched bytes, incompatible media, invalid cutouts, or missing grounding evidence block a generation route before a paid call;
- failed technical/craft review writes only quarantine, never a deliverable;
- missing references or missing craft dimensions are failures, not skipped passes;
- OCR detects/reports but does not erase until a separate benchmark passes;
- generated/reviewer labels have no code or conceptual path into the Meta proxy target.

## 3. Meta model/data pipeline

### 3.1 Apify collection and lifecycle semantics

`ingestion.fresh_corpus_scrape` sends URL-encoded Meta search requests through the configured Apify actor, deduplicates by `ad_archive_id`, preserves every matching search query as discovery provenance, checkpoints after each successful query, and retries only incomplete queries on resume. The current corpus contains 3,555 unique image ads from 12 searches and 1,151 pages.

Keyword discovery is explicitly **not** a product taxonomy. Inspected examples and the frozen review strata include pet products, topical products, equipment, and ambiguous products. The 120-row seed-42 review is stratified over query, contamination risk, and candidate subcategory, but those strata are not labels.

Lifecycle polling is page-inventory based because the official Meta Ad Library API's broad commercial-ad coverage is UK/EU-specific and does not provide complete ordinary US commercial-ad status. A returned `is_active=false` or official stop field is explicit event evidence. A missing tracked ID advances inferred inactivity only when the page inventory is complete: source URLs exhausted and another known ad from the same page returned as a positive control. Empty, partial, or result-capped responses remain unknown. Two consecutive complete misses infer an interval-censored stop at the first miss; one miss is not a stop.

**Critical corrected finding:** this actor populates `end_date` as a delivery/scrape-window boundary even for `is_active=true`. The only accepted semantics are:

```text
is_active=false; legacy end_date heuristic only when activity status is absent
```

The earlier 19 Cox train events were artifacts of the wrong interpretation. Corrected v3 has zero observed train events and zero test events; the warehouse has only two explicit endings. This is why survival guidance must remain empty.

### 3.2 Human taxonomy gate

`pipeline.validation.taxonomy_adjudication` freezes a review artifact and requires:

- a primary explicit decision for every row;
- at least 20% independent double review;
- binary Cohen's κ ≥ 0.80;
- independent adjudicator identity, time, and rationale for every binary or subcategory disagreement;
- exact SHA-256 binding between the passing report and approved labels.

Only explicit `is_supplement=true` rows proceed. The downloaded durable artifact is blank, which is positive evidence that code did not fabricate human labels and the present critical-path blocker remains real.

### 3.3 Product-site enrichment

The model branch restores a gated enrichment sequence:

```text
approved taxonomy corpus
  → free Shopify JSON / structured HTML extraction
  → ZenRows JS-rendered backfill once per unresolved unique URL
  → product-enrichment-coverage-v1
  → Step 2 / matrix / training
```

The workflow verifies exact approved `ad_archive_id` set equality when resuming enriched data. Atomic JSON/CSV checkpoints and locks prevent partial or cross-corpus reuse. `ZENROWS_API_KEY` and the configured `ZENROWS_API` alias are read only through Settings. The separate LLM advertorial fallback is not automatically invoked.

The gate binds canonical enriched-ad content by SHA-256 and reports extraction-method provenance. Fixed minimum linked-ad coverage is 50% for any product-page record, 40% for price, and 25% for description or USP. A product field is model-supported only with at least 100 rows and 10 advertisers.

Matrix features include categorical price tier, rating and presence, `log1p(rating_count)`, description/USP presence and lengths, subscription status, variant count, and cultural-branding count. Missing extraction remains missing. `unknown` subscription means no recognized signature, not proof of one-time purchase. Raw price is excluded because currencies, unit counts, and bundles are not comparable.

**Verified narrow ZenRows evidence:** two isolated paid one-URL calls returned authenticated HTTP 200 and checkpointed. One yielded no supported fields; one `/products/` URL yielded description, rating, and review count but no price. Temporary outputs were deleted and never trained. This proves connectivity/checkpoint behavior only—not corpus coverage, extraction precision, price-tier quality, uplift, or SHAP support. The current 600-row model matrix has no product-page evidence.

### 3.4 Step 2 extraction and embeddings

The implemented extraction path uses the Master Pydantic schema as inter-stage state:

- deterministic metadata, image dimensions, colour and layout-derived features;
- Cloud Vision `document_text_detection` for OCR blocks/geometry;
- Replicate-hosted configured cognitive vision for marketing psychology/hook/vibe and deep object/spatial/human detail in the active branch workflow;
- explicit per-ad fallbacks and missingness rather than fabricated zeros;
- optional title/body/USP retrieval embeddings through the configured Replicate embedding client.

The current reproducible sample is 600 ads: Cloud Vision OCR completed for 600/600; cognitive extraction completed for 598 with two explicit fallbacks; embeddings exist for all 600. The matrix carries `page_id` and page-scaling counts for grouping/proxy construction but excludes both from X inputs to avoid directly teaching advertiser identity or target construction.

The embedding ablation is decisive for current v3: adding raw dimensions expanded the model from 135 to 3,207 features and worsened holdout MAE from 0.3550 to 0.3631 while reducing top-20% precision from 0.3333 to 0.1429. Raw embedding dimensions are uninterpretable as image directives and are excluded from guidance even if a later predictive ablation improves.

### 3.5 Success proxy and leakage controls

There is no ROAS, spend, conversion, or dependable engagement ground truth in this US competitor-ad corpus. The bounded target is:

```text
0.70 × longevity percentile rank
+ 0.20 × longevity rank × page-scaling rank
+ 0.10 × Meta collation/variant rank
```

Scaling counts captured active ads for a non-empty Meta `page_id` in the full corpus before sampling; each unknown page ID receives a conservative count of one and unknown IDs are not grouped. Variant is actor/Meta `collation_count`, not landing-page SKU count. Log transforms and percentile ranks limit outlier influence.

This proxy can still be confounded by brand strength, budget/collection coverage, campaign operations, launch timing, and Meta delivery decisions. It is a correlational commercial-observation target, not a causal quality label.

Leakage controls implemented on the model branch include:

- outer holdout of advertisers first seen latest, assigning a page wholly to train or test;
- a second temporal advertiser split inside training for XGBoost parameter selection;
- percentile calibrator fitted on training only;
- rare categorical pooling derived from training frequencies only;
- explicit removal of page ID, page scaling, target fields, and raw campaign-operation fields from X;
- immutable input, implementation, split, seed, worker, branch/commit, and clean-state provenance;
- future-window evaluator that reconstructs the frozen old specification from old training rows only and rejects overlapping ads/advertisers, stale target ages, broken window chains, and unadjudicated taxonomy;
- old-model-first evaluation: a future window cannot enter retraining until its untouched evaluation is persisted.

### 3.6 Uncertainty, drift, survival, and SHAP

The branch adds paired non-parametric bootstrap intervals (2,000 resamples) for challenger-minus-baseline MAE, calibration gap, and precision of the fixed predicted top quintile. Promotion requires the MAE-difference interval's upper bound below zero and top-quintile precision interval's lower bound above the 20% prevalence baseline. Frozen v3 predates this objective promotion report and must not be retroactively described as passing it.

Raw numeric drift uses PSI over training-derived quantile bins; categorical drift uses total-variation distance, with missing-rate deltas reported. Drift is descriptive: it identifies changed inputs but cannot establish a causal trend. Segment generalization requires at least 100 labelled ads and 10 advertisers represented across train and untouched test; smaller slices are descriptive only.

Survival uses right-censoring and Kaplan–Meier/Cox machinery. A horizon needs at least 20 known outcomes and five endings for a descriptive estimate; Cox fitting's five-train-event floor is only a numerical safety floor. A directive requires the stricter planned gate: ≥30 train and ≥10 untouched-test events, admissible pairs, stable coefficient direction under resampling, and advertiser-disjoint evaluation.

Tree SHAP is computed from the accepted no-embedding composite model. Direction is emitted only when `abs(mean_signed_shap) / mean_abs_shap ≥ 0.10`, the feature is renderable, the bucket is allowlisted, and it is not missing/default/pooled/operational evidence. Feature importance without direction is not converted into an instruction.

### 3.7 Exact frozen v3 evidence and interpretation

| Measure | No embeddings | With embeddings | Interpretation |
|---|---:|---:|---|
| Train/test rows | 499 / 101 | 499 / 101 | Newest-first advertiser holdout; 303/76 advertisers; overlap 0. |
| Features | 135 | 3,207 | Raw embeddings create a very high-dimensional small-sample challenger. |
| Test MAE | 0.3550 | 0.3631 | No-embedding is better, but only 0.0181 (about 4.9%) below baseline MAE. |
| Median baseline MAE | 0.3731 | 0.3731 | Point comparison only; frozen report has no promotion-grade CI. |
| Top-20% precision | 0.3333 | 0.1429 | No-embedding beats 0.20 prevalence as a point; embeddings do not. |
| Test R² | −163.426 | −161.104 | Extremely poor variance explanation/transfer; do not call this broadly predictive. |
| Q3 calibration gap | +0.3550 | +0.3631 | Mean predictions materially overstate newest-cohort target values. |
| Observed Cox events | 0 train / 0 test | n/a | Cox is unevaluable and contributes no directives. |

Only `other supplements` has at least 10 test rows (n=51), with no-embedding MAE/calibration gap 0.3559; every named subcategory is smaller. None meets the newer promotion support threshold.

The independent warehouse survival report is also fail-closed:

| Horizon | Eligible outcomes | Endings by horizon | Coverage of 3,555 ads | Reported estimate | Status/interpretation |
|---|---:|---:|---:|---:|---|
| 30 days | 3,008 | 0 | 84.61% | 1.0000 | Insufficient evidence; zero endings do not prove universal survival. |
| 60 days | 2,242 | 0 | 63.07% | 1.0000 | Insufficient evidence. |
| 90 days | 1,611 | 1 | 45.32% | 0.9994 | Insufficient evidence; one ending cannot support a directive. |
| 180 days | 556 | 1 | 15.64% | 0.9994 | Insufficient evidence and low outcome coverage. |

The apparently high survival probabilities are a consequence of two observed endings and heavy censoring, not proof that ads normally survive those horizons.

The exact accepted guide is:

| Bucket | Directive | Direction | Mean absolute SHAP magnitude |
|---|---|---|---:|
| visual | `copy_canvas_coverage` | lower | 0.01222 |
| visual | `palette_vibrancy` | lower | 0.01066 |
| visual | `psychological_warmth_index` | higher | 0.00810 |
| visual | `hook_framework=Direct Offer` | higher | 0.00482 |
| copy | `avg_words_per_block` | higher | 0.02945 |
| copy | `title_length` | lower | 0.00916 |
| copy | `headline_word_count` | lower | 0.00853 |
| copy | `uppercase_ratio` | lower | 0.00753 |

These are associations and should be treated as bounded constraints, not exact thresholds, causal prescriptions, a complete art direction, or proof that increasing/decreasing a feature will improve a new ad.

Excluded from instructions: raw embeddings; Cox coefficients; missing/unknown/default/pooled rare levels; page/campaign/UTM operation fields; non-directional importances; cross-category/trend claims; and reference pixels. No product-derived directive is supported by v3.

### 3.8 Proposed future copy segmentation

Proposal only: represent three co-occurring axes rather than one overloaded copy class:

1. creative archetype: direct response, brand story, educational, testimonial/UGC, comparison;
2. hook framework: Direct Offer, PAS, social proof, curiosity, problem/solution;
3. CTA intent: purchase, subscribe, learn, engage, absent.

One ad may have all three labels. Segment-conditioned guidance remains blocked until definitions and labels are human-validated, support thresholds are met, train/test advertisers and a later temporal window cover the segment, calibration passes, and independently re-derived SHAP direction is stable. Unsupported segments inherit global v3.

## 4. Image-generation pipeline

### 4.1 v3 loader and guide use

The current default loader reads three fixed repository-root filenames, validates schemas, hashes/provenance, split consistency, zero advertiser overlap, no-embedding superiority, corrected lifecycle semantics, zero-event/empty-covariate Cox status, and independently re-derives the four visual plus four copy directives. Production does not silently fall back to mutable local reports.

It tolerates an unknown top-level `product_enrichment_gate` in future training JSON, which is parser compatibility—not enforcement. It does not discover bundle manifests or validate product gate schema, corpus hash, extraction provenance, coverage, or per-field support. This must change before v4 can become selectable.

The guide informs text-only style translation and copy drafting. Reference ads no longer guide generation because a handful of images could override the larger statistical sample; they are post-generation comparison evidence only. Font personality is explicitly qualitative because Step 2 does not measure font family.

### 4.2 Asset rights, admission, packshots, and owned scenes

`generation-brand-assets-v1` binds exact bytes to human/client declarations: asset/SKU IDs, SHA-256, medium (`photographic`, `illustrated`, `three_dimensional`), role, rights status/owner/permitted use, and critical label strings. Owned scenes additionally bind medium, absence of product/text, support-surface coordinate, key-light direction, and grounding evidence (`human_verified` or `renderer_ground_truth`).

Current admitted routes:

- **owned scene:** a matching rights-approved, product-free, text-free client scene is reused without diffusion, blur, or redraw;
- **product-free plate:** a real transparent photographic packshot is composited onto a generated product-free environment;
- **extracted-from-owned-ad fallback:** source ad, matte, crop, rights, SKU, canonical labels, model/config/code provenance, and hashes are mandatory; admission recomputes mask identity and conservative opaque-core RGB equality;
- identity-conditioned and medium-matched illustration/3D routes remain blocked challengers.

An illustrated source may not be routed into photographic generation merely because a prompt asks for medium matching. The NOVA source used in prior experiments is a flat opaque illustration; preserving it cannot create absent photographic detail. A compatible owned scene or real transparent packshot is an input prerequisite, not something edge cleanup can infer.

### 4.3 Automatic product extraction, ROI, and matting

The generation branch implements `generation-extraction-benchmark-v1` with hash-bound source/matte pairs, development/calibration/blind-holdout partitions, explicit rights evidence outside development, and independent annotator/reviewer IDs. Packaging strata are opaque bottle, box, pouch, reflective, transparent/glass, fine detail, and partially occluded.

Localization metrics are primary-SKU IoU/recall, wrong-selection rate, and ambiguity rate by stratum. Matte metrics are alpha MSE, SAD, and boundary F-score by stratum. Blind holdout access requires an explicit flag so routine development cannot tune against it.

The automatic challenger separates `ProductLocalizer` and `AlphaMatteExtractor`. Its receipt binds provider/pipeline/model digest, parameter hash, latency, implementation commit, rights evidence, source/crop/raw matte/mask/cutout hashes, selected crop, ambiguity, and `generated_pixels_present=false`. Its only possible status is `quarantined_pending_human_review`; it cannot create admitted product metadata.

The pinned ROI challenger is Replicate Grounding DINO revision `efd10a8ddc57ea28773327e881ce95e20cc1d734c589f7dd01d2036921ed78aa`, with verified fields `image`, `query`, `box_threshold`, `text_threshold`, and `show_visualisation=False`. Highest confidence only ranks candidates. Scores within the configured ambiguity margin quarantine and bypass matting.

The existing pinned background remover is exposed only as a baseline matte challenger. BiRefNet, RMBG, SAM/Florence and other reputational candidates have not been selected without direct benchmark evidence. No paid Grounding DINO/matte accuracy run has occurred.

The reusable cutout is source-crop RGB plus normalized alpha only. Raw provider RGB is audit-only. Reconstruction, inpainting, de-lighting, global neutralization, hidden-label reconstruction, and extracted-fringe edits are disabled. This preserves identity but cannot repair occluded/missing product information.

The latest committed branch state adds a rights-bound quarantine CLI. Rights are checked before provider invocation. It hash-checks every artifact, atomically writes only `quarantine/<run-id>/{report.json,provider-raw-do-not-use.png,alpha-mask.png,candidate-cutout.png}`, never overwrites a run, and has no deliverable path. This is committed in `868c6cb…` and its session evidence in `db9908f…`; the owning thread reran the full and focused suites after the final writer change.

### 4.4 Scene routes and Flux usage

The production synthetic route uses Replicate `black-forest-labs/flux-2-pro` to generate a product-free scene plate at explicit dimensions. Flux never receives the product or raw marketing-shaped intention. It receives a translated non-copy scene brief, product-placement space, support-plane/light requests, and verbal keep-clear regions. These are probabilistic instructions, not measured camera/light geometry.

The original RGBA product is fitted without upscaling, anchored to the declared support plane, and composited locally with a tight contact line, directional cast shadow, and edge handling. Owned scenes bypass Flux. `flux-fill-pro` remains for targeted duplicate-region repair, not whole-frame production. `flux-kontext-pro` is retained but unused on this path.

Rejected approaches and reasons:

- whole-frame Flux Kontext rewrote/blurred protected product-label text;
- masked inpainting while the product was visible preserved the source region but induced duplicate/garbled products elsewhere;
- passing the product as Flux style/reference conditioning created a second product;
- prompt-only bans on products/text and prompt-only medium matching failed live;
- whole-frame Gaussian blur suppressed some pseudo-text but caused synthetic softness;
- surgical Flux Fill repair can paint a different duplicate into the erased region, so it remains capped and rechecked.

### 4.5 Layout, typography, composition, contrast, and grounding

After background removal, product geometry is known. The planner compares product-free left/right/top/bottom regions, picks the largest viable area, reserves headline/body/CTA zones, and fails before paid generation if no safe area exists. The plan is frozen across retries so a stochastic bad scene cannot destabilize layout.

Copy generation uses Pydantic length limits; rendering is deterministic PIL. Bundled licensed DejaVu, Archivo Black, and Bebas Neue faces prevent silent fallback to PIL's bitmap font. Curated three-role headline/body/CTA systems vary qualitative typography without pretending SHAP measured font family.

The compositor wraps at whole-word boundaries through role-specific readable sizes, records fit rather than shrinking indefinitely, accounts for actual rendered bounds including shadows/bands, and checks product intersections and canvas containment. Text and CTA contrast use pixel-distribution WCAG checks; at least 98% of sampled background pixels must pass or a restrained feathered backing shape is added. Bands/CTA edges and shadows are softened to reduce sticker-like treatment.

Grounding metadata distinguishes requested, human-verified, renderer-ground-truth, and estimated evidence; estimates require confidence. The deterministic gate compares product bottom with declared support surface at 0.005 normalized tolerance. Physical realism still needs human review of scale, perspective, light direction, contact/cast shadows, ambient spill/reflection, edges, and occlusion. A geometry pass does not prove a realistic composite.

### 4.6 Duplicate, pseudo-text, OCR, and quarantine

Duplicate detection is deterministic-first: a background-removal pass and connected components compare extra blobs with the known product bounding box; ambiguous cases escalate to a structured multi-image vision call. A detected region gets at most two targeted repairs and rechecks before full regeneration. Persistent duplication fails acceptance.

Scene prompts ban products and common text-bearing props, but prompts are not proof. Non-destructive Cloud Vision OCR partitions final OCR blocks into deterministic-copy boxes, protected product region, and unexpected scene text. It computes character error rates, verifies canonical label strings, and quarantines copy mismatch, missing protected labels, or unexpected scene text.

Destructive cleanup is structurally fixed off. Its future benchmark must include ordinary scene text, pseudo-text, protected product text, and text-like non-text. Enablement requires detection precision ≥0.99, recall ≥0.95, selected-cleanup-pixel precision ≥0.99, zero protected-product pixels, and zero non-text pixels selected. Passing arithmetic alone still does not mutate the safety flag; a separate reviewed release change is needed.

Failed technical or craft candidates are written under quarantine and the requested deliverable path is not written. Current automated qualification includes `human_craft_approval_missing`, so no result can honestly be called approved.

### 4.7 Immutable successful-ad references and six-dimension evaluation

The reference corpus is not a prompt/style donor. Each future reference must be an explicitly labelled Supplement, rights/usage approved, independently approved by two reviewers, advertiser-diverse, hash-addressed and retrievable, annotated on all six dimensions, and marked `directive_aligned=false`. It may inform comparison only—not pixels, product, wording, people, scene, exact layout, or imitation.

The six required dimensions are:

1. art direction;
2. imagery quality;
3. typography;
4. visual hierarchy;
5. composition;
6. brand cohesion.

Missing references, missing/duplicate dimensions, any dimension below 4/5, critical defects, or missing human approval fail closed. Automated evaluators produce candidates, not publication authority. A learned craft ranker remains proposal-only until sufficient blinded human labels can be split by brand/product/run to prevent near-duplicate leakage.

### 4.8 Why the Meta model does not solve image realism

The Meta model maps extracted attributes to a public longevity/scaling/variant proxy. It does not observe product cutout quality, camera geometry, lighting consistency, matte fringes, label identity, text deformation, craft review, or photorealism ground truth. SHAP explains this model's associations; it does not supply missing pixels or a rendering loss.

Changing XGBoost, embeddings, or SHAP cannot turn a flat illustration into a packshot, enforce Flux's one-product behavior, infer a physically correct support plane, or guarantee legible generated glyphs. Realism requires better input assets/routes plus dedicated identity, matting, physical-coherence, OCR, and human-craft evidence. Meta guidance should constrain what to attempt; generation-quality evidence decides whether execution is acceptable.

## 5. Verified findings, negative findings, and unresolved hypotheses

### 5.1 Verified

- The corpus and warehouse counts, 600-row extraction coverage, advertiser-disjoint split, v3 metrics, eight directives, and artifact hashes are recorded and byte-bound.
- Embeddings hurt current holdout MAE and ranking; they are excluded from v3.
- Active-ad `end_date` is not stop evidence; corrected survival has zero model events and only two warehouse endings.
- Current keyword taxonomy is contaminated and the human review is blank.
- Product enrichment and gates are implemented, but current training data contains no product-page fields.
- Two ZenRows calls prove authentication/checkpointing only.
- Generation's product-free path structurally protects product RGB and deterministic final copy from diffusion.
- Live failures include rewritten labels, duplicate products, pseudo/ghost text, expired signed CDN URLs, weak grounding, medium mismatch, halo/edge issues, clipped/bleeding typography, and generic craft.
- Facebook CDN URLs expire cryptographically; retries/headers cannot revive them. Refresh/archive near scrape time is required.
- Product conditioning improved scene relevance in some experiments but created duplicate/re-rendered products, violating identity.
- Automatic ROI/matting orchestration, metrics, and quarantine exist; provider accuracy is unmeasured.
- Offline tests prove deterministic contracts, not stochastic realism or ad performance.

### 5.2 Important negative evidence and rejected claims

- No ROAS, conversions, spend, reliable US impression range, or dependable engagement label is present.
- No causal effect of any SHAP directive has been established.
- No category/trend generalization is supported; observed Q3 shift has no verified cause.
- No survival/Cox guidance is supported.
- No product feature has measured coverage, uplift, or directive support.
- No approved reference corpus exists; transient top-ad URLs are not a benchmark.
- Qwen layer decomposition did not yield clean semantic assets in inspected outputs; text/props/background were mixed.
- Prompt compliance, model confidence, and vision-review scores are not identity/rights/physical truth.
- The latest generation hardening has no paid end-to-end validation and no publication-ready claim.

### 5.3 Unresolved hypotheses

- Product-site fields may improve transfer or explain apparent creative associations; this is unknown until taxonomy-approved enrichment and untouched evaluation.
- The Q3 calibration shift may persist in later windows or may reflect snapshot maturity/collection composition.
- Grounding DINO may outperform alternate ROI systems on primary-SKU selection; no real benchmark exists.
- Background remover, BiRefNet/RMBG, or prompted segmentation may be best for different packaging strata; no comparative matte evidence exists.
- Owned scenes may dominate product-free generated plates in physical/craft quality at lower retry cost; no controlled route experiment exists.
- A product-conditioned method might become viable with stronger identity control, but current evidence says it is unsafe by default.
- Contextual copy segmentation may improve relevance without overfitting; labels/support/stability are missing.
- A learned craft/aesthetic ranker may improve selection, but risks brand/style bias and evaluator leakage.

## 6. Pipeline interface contract and v4 migration

### 6.1 Immutable bundle

Frozen v3 bytes:

| Role | Schema | SHA-256 |
|---|---|---|
| generation guide | generation-guide-v1 shape | `63b0ecc8e3c88e8a9b5b7627aebf8441a5b084d054d3f23dade0b45d3556a110` |
| training report | `meta-ads-training-report-v2` | `2d9307fa9f671744ca053af545b3d191f39e81e2757d41ab95f9a534a463043e` |
| survival report | `survival-validation-v1` | `2dddda67eea155498dcdb14ee690b15649fa40cab7986a23115f274c1492d0cb` |

The first provenance manifest is immutable but has a consumer-incompatible survival path. Corrected `supplements-v3-bundle-v2` changes only that relative filename while retaining evidence bytes/hashes; its manifest SHA-256 is `a36074fd9e042cbd40cdf802c12a06287eb319abb1d28b4d32fd7e945814bbfd`. It does not change the fixed-path generation loader.

A v4 bundle must be a new immutable directory/manifest, never overwritten v3 filenames. It must map exact artifact roles to relative path, SHA-256, and schema, and carry full producer provenance: branch/full commit/clean state, implementation/input hashes, target definition, seed/workers, split IDs/counts, leakage checks, model/baseline and uncertainty metrics, calibration/drift/segment support, lifecycle semantics, exclusions, and promotion decision.

### 6.2 `product_enrichment_gate` migration

Before generation may use v4 product context, its shadow loader must:

1. reject absent or unknown product-gate schemas for any bundle claiming product directives;
2. accept exactly `product-enrichment-coverage-v1` (or a separately reviewed successor);
3. validate the bound approved enriched-corpus hash and expected ad identity/version;
4. validate linked-ad coverage policy and extraction-method provenance;
5. map every product-derived directive to its source field;
6. require `model_support=true` and the fixed row/advertiser counts for each mapped field;
7. reject raw price and any unsupported rating/price-tier directive;
8. independently re-derive directives from the bound no-embedding report;
9. run v4 in shadow against frozen generation fixtures before changing the default alias;
10. retain global v3 fallback for absent/unsupported segment or product context.

Current generation clears positioning context and still allowlists `rating` as a visual dimension; metadata tolerance alone could therefore admit an unsupported rating direction. This is a known enforcement gap, not an active v3 problem because v3 has no product gate/directive.

### 6.3 Loop-isolation invariant

The following may train a generation evaluator/ranker: deterministic defect outcomes, route/provider metadata, costs/latency, human identity/OCR/physical/craft ratings, and promotion decisions. They may **never** become `days_active`, scaling, collation, composite proxy, “successful Meta ad,” or any replacement Meta outcome.

Only future observations of genuinely deployed ads may enter a new Meta window. If first-party conversion data becomes available, it requires a new target schema and ADR; it cannot silently redefine the competitor proxy.

## 7. Dependency-aware P0–P5 roadmap

| Phase | Entry | Work | Objective exit |
|---|---|---|---|
| **P0 Freeze** | Current v3 and known generation baseline available. | Preserve corrected lifecycle semantics/hashes; immutable bundle; fixed-path fallback; quarantine failures; record branch/model/config/seed/cost provenance. | Hash/schema/provenance reproduce; advertiser overlap 0; v2 lifecycle cannot be selected; no failed output reaches deliverable storage. Mostly implemented, except bundle discovery remains future work. |
| **P1 Human truth/assets** | P0. | Complete 120-row taxonomy; continue lifecycle windows; approve/archive ≤8 references; obtain rights-approved real packshots/owned scenes/brand assets; label ROI/mattes and OCR cases. | Taxonomy complete, ≥20% double-labelled, κ≥0.80, all disagreements adjudicated; references each have two approvals/rights/six dimensions; real extraction/OCR strata populated; assets pass G0. Currently blocked on humans/data. |
| **P2 Calibrate measurement** | P1 datasets frozen. | Run product enrichment; measure field quality/coverage; calibrate uncertainty/drift/segment gates; evaluate v3 on next window before retraining; calibrate identity/halo/duplicate/OCR/layout/rubric thresholds. | Product coverage gate passes and supported fields have ≥100 rows/10 advertisers; old-model future evaluation persisted; hard-gate benchmark thresholds frozen without holdout tuning; human rubric κ≥0.70. |
| **P3 Route challengers** | P2 gates and rights-approved fixtures. | Compare owned scene, product-free Flux plate, extracted cutout, identity-conditioned challenger, and medium-matched illustration/3D route one factor at a time. | ≥30 discovery outputs/route show no systematic critical defect; chosen route then ≥100 outputs, zero critical identity/duplicate/protected-text failures, median physical coherence ≥4/5, κ≥0.70; cost/latency/retry by medium recorded. Paid authorization required. |
| **P4 Craft/layout** | Successful-ad corpus frozen; P3 route selected. | Compare deterministic layout/type systems and optionally a learned craft ranker with brand/product/run-disjoint labels. | Every craft dimension median ≥4/5, no critical defect, ≥2/3 human approval; publication-ready pass rate and blinded pairwise preference both improve over frozen baseline with 95% interval excluding no improvement. |
| **P5 Temporal refresh** | Genuinely later, mature, untouched Meta window and P1–P2 gates. | Score old v3 first; only then train/calibrate challenger and derive v4; shadow-load v4; rerun generation comparison if directives change. | Model promotion CI gates pass, zero advertiser overlap, supported segments only; v4 bundle/hash/gate validation passes; generation shadow does not regress G1–G4. Otherwise retain global v3. |

The “zero critical failures in 100 outputs” criterion gives only an approximately 3% one-sided 95% upper bound, so it is a minimum promotion screen, not proof of zero risk.

## 8. Prioritized research backlog: answerable investigations

Every investigation below must preregister the decision, primary metric, split, thresholds, and stopping rule before exposing the blind holdout.

### R1 — Curate the truth datasets and benchmark governance (P0)

- **Decision:** what exact annotation and rights protocol makes model, extraction, OCR, and craft results admissible?
- **Compare:** two-reviewer/adjudicator workflow; expert versus trained-generalist annotation; stratified random versus active-error sampling; hash-addressed GCS bundle versus dataset registry/DVC-style manifests.
- **Evidence:** 120 taxonomy rows; ≤8 successful-ad references; real SKU boxes/mattes across seven packaging strata; four-class OCR fixtures; generation examples reserved for rubric calibration versus blind test.
- **Metrics:** κ/weighted κ, disagreement taxonomy, missing provenance, hash reproducibility, advertiser/brand/medium coverage, reviewer hours.
- **Concerns:** image/ad reuse rights, reviewer exposure leakage, personal data in ads, indefinite storage, annotator bias.
- **Adoption gate:** all required rights fields and independent reviews present; taxonomy κ≥0.80; craft/physical κ≥0.70; zero hash/provenance failures. No downstream promotion before this passes.

### R2 — Product-page extraction and weak commercial context (P0)

- **Decision:** whether Shopify/structured + ZenRows produces reliable, sufficiently broad X-side fields and which fields merit training.
- **Compare:** free structured extraction, ZenRows rendered HTML, provider extraction APIs, deterministic schema.org/Shopify parsing, and a separately authorized LLM fallback only for unresolved pages.
- **Evidence:** taxonomy-approved unique URLs with manually audited truth for product identity, currency, normalized unit/bundle, price tier, rating/count, description/USP, subscription, and freshness.
- **Metrics:** field precision/recall/coverage by site/platform; wrong-product rate; staleness; gate pass rate; incremental advertiser-disjoint future-window MAE/calibration/top-quintile precision; SHAP stability.
- **Concerns:** paid request cost/latency, site terms/robots, regional pricing, currency/unit ambiguity, rating manipulation, prompt injection in pages, secret/log leakage.
- **Adoption gate:** fixed coverage gate passes; audited precision target is preregistered and met; each active field has ≥100 rows/10 advertisers; future-window uplift interval passes; no raw price directive.

### R3 — Success proxy and weak commercial labels (P0)

- **Decision:** retain, reweight, or replace the 70/20/10 composite when only public commercial observations exist.
- **Compare:** current percentile composite; separate multi-task longevity/scaling/collation models; pairwise advertiser/time-matched ranking; positive-unlabelled or interval-censored objectives; weak-supervision label models; eventual first-party outcomes as a separate target.
- **Evidence:** repeated complete lifecycle polls, page-inventory completeness, collection windows, named-brand comprehensive page scrapes, and confounder metadata.
- **Metrics:** temporal advertiser-disjoint calibration, ranking precision/NDCG, stability across windows/categories, sensitivity to collection coverage and proxy weights, correlation with any later first-party outcome without target leakage.
- **Concerns:** proxy gaming, brand/budget confounding, actor drift, sparse endings, false causal interpretation.
- **Adoption gate:** challenger beats simple baselines with preregistered uncertainty on ≥2 untouched windows; target remains explicitly named and versioned. Otherwise retain current bounded proxy.

### R4 — Calibration, uncertainty, and drift policy (P0)

- **Decision:** when to abstain, fall back to global v3, or retrain.
- **Compare:** current bootstrap + PSI/TV; grouped/conformal prediction; quantile regression; isotonic versus Platt-like calibration; hierarchical partial pooling by category; drift detectors such as MMD/energy distance versus interpretable PSI/TV.
- **Evidence:** at least two chained mature windows with advertiser-disjoint rows and adjudicated segments.
- **Metrics:** interval coverage/width, calibration error, false promotion rate, abstention utility, drift detection lead time, stability under advertiser bootstrap.
- **Concerns:** small samples, repeated testing, threshold tuning on holdout, operational complexity.
- **Adoption gate:** calibrated coverage on untouched windows and demonstrably better abstention/promotion decisions than fixed thresholds; retain interpretable reports.

### R5 — Survival and temporal learning (P0)

- **Decision:** whether Cox, discrete-time hazards, or non-parametric survival can safely contribute any directive.
- **Compare:** penalized Cox PH with diagnostics; discrete-time hazard/logistic survival; random survival forests or gradient-boosted survival only after sample support; interval-censoring methods; Kaplan–Meier-only descriptive baseline.
- **Evidence:** ≥30 train and ≥10 untouched-test explicit/inferred qualified events, admissible pairs, repeated complete misses, maturity by horizon, advertiser-disjoint windows.
- **Metrics:** C-index, integrated Brier score, time-dependent AUC/calibration, PH violations, coefficient-direction bootstrap stability, event-definition sensitivity.
- **Concerns:** interval-censoring approximation, sparse events, actor completeness, temporal leakage, compute is secondary to evidence quality.
- **Adoption gate:** all M2 thresholds, stable directions, calibrated untouched test, and no semantics ambiguity. Until then emit empty Cox guidance.

### R6 — Creative archetype/hook/CTA segmentation (P1)

- **Decision:** whether contextual copy guidance improves over global 4+4 without creating sparse unstable rules.
- **Compare:** human multi-axis labels; constrained Gemini-assisted labelling with human audit; multi-label classifiers; hierarchical/global-plus-segment models; interactions versus separate segment models.
- **Evidence:** frozen definitions and double-labelled ads for all three axes, ≥100 rows/10 advertisers per candidate segment across train/test, later-window coverage.
- **Metrics:** label κ/F1, calibration, segment uplift interval, SHAP sign stability, fallback rate, copy-quality blind preference.
- **Concerns:** subjective labels, co-occurrence confounding, prompt/model drift, generated-label contamination.
- **Adoption gate:** human-validated taxonomy, support and temporal gates, independently stable directions; otherwise global v3.

### R7 — ROI/localization for automatic packshot recovery (P0)

- **Decision:** which system most reliably identifies the primary SKU and when it must abstain.
- **Compare:** pinned Grounding DINO; Florence-style grounded detection; SAM/SAM2 seeded by detector; OWL-ViT/OWLv2 or equivalent open-vocabulary detectors; simple human box baseline. Include no-model/manual crop as operational comparator.
- **Evidence:** rights-approved ads with independent primary-SKU boxes across clutter, multiple SKUs, hands/occlusion, small products, and all packaging strata.
- **Metrics:** primary recall at IoU threshold, mean IoU, wrong-selection and ambiguity/abstention rates, calibration of confidence/margins, latency/cost.
- **Concerns:** Replicate/version availability, paid inference, label-query sensitivity, person/brand data, silent model revisions.
- **Adoption gate:** preregistered blind-holdout thresholds with zero or bounded wrong-SKU selections; ambiguity routes to quarantine. Confidence alone never admits.

### R8 — Matting/segmentation and cutout integrity (P0)

- **Decision:** select a default/challenger matte pipeline by packaging stratum.
- **Compare:** current 851-labs remover baseline; BiRefNet/RMBG variants; SAM2/refinement; trimap-based matting (ViTMatte/MODNet-style); commercial APIs only if rights/retention terms permit; human matte baseline.
- **Evidence:** independently reviewed alpha mattes for transparent/glass, reflective, fine-detail, pouches, boxes, bottles, and occlusion; fixed ROI inputs so localization and matte errors are separated.
- **Metrics:** alpha MSE, SAD, boundary F-score, opaque-core identity, halo/fringe contamination, clipping, human edge rating, latency/cost by megapixel.
- **Concerns:** model licenses, provider retention, GPU/API cost, transparent materials, hidden-pixel reconstruction claims.
- **Adoption gate:** blind-holdout superiority over baseline by stratum with zero core-RGB identity violations; outputs remain quarantined until human label/OCR checks.

### R9 — Product conditioning and identity preservation (P1)

- **Decision:** whether any product-conditioned generation route can outperform product-free compositing without duplicate/label drift.
- **Compare:** current product-free Flux.2 plate; owned scene; IP-Adapter/identity-reference conditioning; ControlNet/depth/edge conditioning; masked relighting/compositing; 3D/product-render route where client assets permit. Compare complete routes, not model reputation.
- **Evidence:** real transparent packshots and canonical label dictionaries; same products/copy/layout/guide across routes; ≥30 discovery then ≥100 promotion outputs.
- **Metrics:** exact product count, source/core identity, protected OCR CER, SKU human accuracy, physical coherence, six-dimension craft, quarantine/retry rate, cost/latency.
- **Concerns:** model/provider rights, product imitation, diffusion alteration, training-data policy, higher paid-call multiplier.
- **Adoption gate:** zero critical identity/duplicate/protected-text failures in promotion set plus G3/G4 improvement. Otherwise retain owned-scene/product-free route.

### R10 — Background/scene generation and physical coherence (P1)

- **Decision:** best scene architecture by product medium and asset availability.
- **Compare:** owned photography; Flux.2 product-free plates; alternative dedicated text-to-image models only if authoritative API/licence evidence exists; renderer/3D scenes; retrieval of client-owned scene library; deterministic relighting/shadow methods.
- **Evidence:** medium-stratified packshots/owned scenes, frozen support/light metadata, identical copy and v3 constraints, blinded physical labels.
- **Metrics:** medium match, scale/perspective, support contact, light/shadow direction, ambient spill/reflection, edges/occlusion, pseudo-text/product rate, craft preference, latency/cost.
- **Concerns:** model drift, image rights, synthetic-disclosure obligations, provider safety policies, scene memorization/imitation.
- **Adoption gate:** G3 discovery/promotion thresholds and no regression in identity/OCR. Do not add a model merely because it benchmarks well generically.

### R11 — OCR detection and surgical cleanup (P0 safety)

- **Decision:** remain detection-only or enable a narrowly masked cleanup stage.
- **Compare:** Cloud Vision OCR; local OCR detectors/recognizers; ensemble consensus; pseudo-text classifier; inpainting versus deterministic clone/texture fill only after mask safety passes.
- **Evidence:** four required classes with pixel masks, protected product regions, ordinary valid scene text, low-contrast/gibberish text, logos and text-like textures.
- **Metrics:** detection precision ≥0.99, recall ≥0.95, cleanup-pixel precision ≥0.99, zero protected/non-text selection; OCR CER after cleanup; human non-text damage review; cost/latency.
- **Concerns:** destructive false positives, logos/labels, multilingual copy, Vision IAM, API data handling.
- **Adoption gate:** exact G2 thresholds on blind holdout and explicit reviewed code change; otherwise observation-only.

### R12 — Typography, layout, and composition optimization (P1)

- **Decision:** whether multi-candidate deterministic search improves craft over current largest-region/three-role systems.
- **Compare:** current heuristic; constraint programming/beam search over zones; font-pair/layout templates; differentiable or saliency-aware scoring; LLM proposals filtered by hard geometry; human-designed baseline templates.
- **Evidence:** frozen assets/scenes/copy, real successful-ad craft corpus, layout annotations, brand fonts/licences, brand/product/run-disjoint candidate sets.
- **Metrics:** zero hard violations; minimum readable size; WCAG pixel coverage; hierarchy/composition/typography ratings; pairwise preference; diversity; render latency.
- **Concerns:** font licensing, overfitting references, accessibility versus brand expression, combinatorial search cost.
- **Adoption gate:** hard gates remain perfect and blinded G4 preference/pass-rate intervals improve; no copying of reference layouts.

### R13 — Aesthetic/craft evaluation and learned ranking (P1)

- **Decision:** whether an automated ranker can reduce human load without becoming publication authority.
- **Compare:** rule-only gates; structured VLM rubric; CLIP/aesthetic scorers; learned pairwise ranker on six-dimensional labels; ensemble with calibrated abstention.
- **Evidence:** ≥2–3 blinded reviewers per candidate, frozen successful-ad corpus, separate rubric-calibration and test sets, brand/product/run-disjoint splits.
- **Metrics:** per-dimension correlation and calibration, pairwise accuracy, critical-defect recall, false-approval rate, reviewer agreement, selection uplift and abstention.
- **Concerns:** aesthetic/popularity bias, reference imitation, provider drift, demographic/brand bias, evaluator leakage.
- **Adoption gate:** zero increase in critical false approvals; significant pairwise/pass-rate improvement; humans retain final approval. Never feed labels to Meta proxy.

### R14 — Retrieval embeddings and interpretable representation alternatives (P2)

- **Decision:** whether any learned representation adds robust future-window value despite v3's negative ablation.
- **Compare:** no embeddings; compact pooled multimodal embeddings; supervised dimension reduction fitted inside training only; interpretable VLM attributes; similarity-to-training support/abstention rather than raw X inputs.
- **Evidence:** larger adjudicated corpus and at least two future windows; train-only transformations; advertiser-disjoint nested tuning.
- **Metrics:** MAE/calibration/ranking CI, dimensionality, drift, stability, nearest-neighbour advertiser leakage, interpretability and directive eligibility.
- **Concerns:** cost, high-dimensional overfit, provider/model version drift, opaque directives.
- **Adoption gate:** consistent untouched-window uplift and no leakage; raw dimensions still never become directives. Current default remains no embeddings.

### R15 — Promotion experiment design and online learning boundary (P0)

- **Decision:** how to progress from offline route selection to real campaign evidence without contaminating targets.
- **Compare:** frozen offline 30/100 route trials; blinded pairwise human tests; first-party randomized creative experiments with predeclared spend/stop rules; multi-armed bandit only after unbiased measurement exists.
- **Evidence:** approved publishable assets, stable campaign instrumentation, separate first-party target schema, holdout creative/brand sets, legal/platform approval.
- **Metrics:** offline G1–G4, cost/latency/retry; online conversions/CTR only under the new first-party design, confidence intervals, novelty/fatigue, adverse-event/brand-safety rate.
- **Concerns:** paid media cost, user exposure, Meta policy/AI disclosure, sequential-testing bias, business risk, privacy.
- **Adoption gate:** explicit human authorization and separate ADR/target; generated/reviewer labels remain outside Meta competitor proxy. Roll back by manifest/alias, never mutate evidence.

## 9. Implementation status, tests, blockers, and immediate actions

### 9.1 Model branch implemented versus blocked

Implemented on `auto/meta-ads-model-pipeline`: append-only BigQuery/GCS warehouse; lifecycle correction and polling semantics; taxonomy/adjudication workflow; atomic artifacts/locks; Step 2 Replicate/Cloud Vision path; feature matrix and embedding ablation; advertiser-disjoint nested temporal evaluation; uncertainty/drift/segment support; survival validation; immutable observation windows; future-window old-model evaluator; objective promotion decision; immutable v3 handoff bundling; benchmark candidate/archive workflow; product-site extraction/gate/features; generation-guide filtering for unsupported product evidence; settings/IAM docs and tests.

Blocked by human/data/IAM/paid work: human taxonomy, full ZenRows enrichment, audited field quality, new model/uplift/SHAP/v4, mature future window, enough endings, rights-approved benchmark references, recurring poll authorization, and any first-party outcome design. GCP was verified in the model checkout; every other checkout must independently verify identity/API access.

Recorded verification at HEAD: `uv run pytest pipeline/tests -q` → **483 passed**, two pre-existing SciPy precision-loss warnings; `uv run pytest ingestion/tests -q` → **446 passed**; focused product/workflow/enrichment suite → **176 passed**; changed-path Ruff and `git diff --check` passed. This documentation run independently reconstructed the 92-path delta from the common base and reproduced 483/446; executable bits for two shell helpers had to be restored because Changes transfer preserves contents, not file mode.

### 9.2 Generation branch implemented versus blocked

Committed through clean HEAD `db9908f`: strict v3 loader, quarantine, rights/medium/route admission, owned scenes, product-free Flux.2 route, source-pixel compositing, cutout/identity/fringe/grounding checks, deterministic layout/type/contrast/composition, duplicate detection/repair, non-destructive OCR, immutable references/six-dimension review, extraction benchmark, localization/matting interfaces, Grounding DINO ROI challenger, extraction provenance, stronger hash-bound `generation-source-ad-rights-v1`, and the atomic/hash-checked quarantine CLI.

Blocked: rights-approved real packshots/owned scenes/source ads, SKU boxes and alpha mattes, approved successful-ad corpus, labelled OCR and craft/physical datasets, calibrated thresholds, paid provider and route runs, blind holdout, human publication approval, and v4 product-gate loader migration.

Recorded verification at clean HEAD: `uv run pytest pipeline/tests -q` → **536 passed, 2 warnings in 23.63s**; focused extraction/CLI suite → **15 passed in 0.47s**; generation/config/client Ruff reported `All checks passed!`; `git diff --check` passed. The two warnings are pre-existing SciPy precision-loss warnings in `test_data_quality.py`. This documentation run refreshed the final 77-path capture, independently reconstructed it from the common base, and reproduced 536/15 (24.19s/0.44s in this orb).

### 9.3 Immediate next actions in dependency order

1. Assign humans and complete/adjudicate the frozen 120-row taxonomy; do not bypass it.
2. Continue lifecycle polling under an explicitly authorized budget/scheduler and freeze a genuinely later window; do not infer stops from active `end_date`.
3. Obtain rights-approved real packshots, compatible product-free owned scenes, source-ad rights evidence, canonical labels, brand fonts/palette/logo, and publication reviewer ownership.
4. Curate and hash the real ROI/matte and OCR benchmarks; freeze calibration and blind-holdout partitions.
5. Approve/archive up to eight advertiser-diverse successful-ad references with two reviewers and all six dimensions.
6. Run free-first then ZenRows enrichment on the approved corpus; manually audit fields before training.
7. Add and test the generation shadow bundle loader/product-gate enforcement before any v4 artifact exists; preserve global v3 fallback.
8. Run the first authorized extraction challenger only after real benchmark fixtures are frozen; the CLI is now durable but its provider accuracy is still unmeasured.
9. Evaluate v3 unchanged on the mature next window. Retrain only after persisting that result.
10. With explicit paid authorization, run 30-output route discovery, then only a qualifying 100-output promotion trial; keep candidates quarantined until human G4 approval.

## 10. Commit and artifact inventory

### 10.1 Shared/base and documentation branch

- `b0951916ff24981a218247606f9fa29d410a5a07` — local `master`, `origin/master`, and both feature-branch bases at evidence cut.
- `docs/current-pipelines-research-brief` — this documentation-only branch, created from that base; its local commit is reported in the document delivery message.

### 10.2 Model branch complete ordered inventory

The owning thread reports 30 commits, 92 changed paths, 8,662 insertions and 489 deletions since base; clean HEAD is the final row:

```text
c0b3be7b6164b35dcffb13ab562e1a4a4d329173 Harden resumable Meta ad ingestion artifacts
3244840b172093eabb5fe4d9dc578bf84b58f58f Align Meta ad training with the success proxy
9c1206c73250af7c37172f4f088f033e667ce168 Log Meta ads pipeline session
bf2e31ee2c71ad8125e84b98baa4e026ee956776 Build end-to-end supplements model workflow
6d9acdc34866743ad737d0ccb931b5782c5672e2 Repair nested Replicate cognitive extraction
153e0e00a8dd15bfea771877a1ec4104d730502c Gate generation guidance on model evaluation
d94c2c1b06b9e90b5601e9c3d320d626ae730911 Log supplements workflow results
3d2eac00e0047612ba749557e1060915881901d4 Harden expanded extraction under API limits
0c1a75a795657aec27ce235ebfdb23fb0e679ff1 Log expanded supplements evaluation
89338df7e66320d06a97d59255edc85f0f2df3e6 Configure keyless GCP auth for Amp orbs
922a0577734ca142dd4916319af251227189a1c2 Log keyless GCP orb setup
105902fcd711846c1fc696443387fb441e7346b8 Correct deployed GCP WIF provider
5cd852fa0345736a112e9286143bab203ed419a8 Pool rare cognitive categories before training
87d7fbfa2f683011f62b7057b44e131e3c20371a Log live OCR and model ablation
ac8022252d34aa7787717d5c6abed80c004a0c87 Persist and validate Meta ad learning evidence
9879783fb89083719c19ce87c91f16a195309a27 Document durable Supplements learning workflow
f6d05cf0cb6f90e02c06d45647e5ae36fcfcf6a9 Log Meta observation warehouse session
6f40f12972468b8570554cc5e64ff0fe46d4e2af Correct live warehouse and longevity semantics
5f9e501aea8a3d1c647f948cb58a974bf092a45b Log live Meta warehouse backfill
4448d0b51c3e2fbb58f765ef884ea0111d223cd5 Plan joint Meta and generation improvements
dba8a1a2c5bc2d8eb2b634c7591c061c27bdb8c6 Log joint pipeline planning session
22196a4aa5494f50623c43ac441f391f23d707e4 Gate Supplements training on adjudicated taxonomy
cf967c5929e4885f61bd3adfc64a0956a952f11a Add temporal evidence and immutable handoff gates
f07b63604fa601f5601cb6a160b85216bd17da06 Document evidence-gated Meta workflow
b8d6019e194813040c5b60143c4863a1353b8be2 Log Meta evidence gate implementation
998a35664e10124982cd633a44117112a58a55d7 Align v3 bundle paths with generation consumer
59f2cd2a0907cc9bd993c63d0e83053353da08cf Record corrected generation handoff
26d613398d511ced8dc79e3433725105f36796eb Restore gated product-site model enrichment
4f99462e07707025055a0051d31833f9c85edef8 Log product enrichment workflow
a013f8c5d87e512788af616972c23562b82bf29d Record ZenRows smoke verification
```

### 10.3 Generation branch complete ordered inventory

The owning thread reports 31 commits and 77 changed paths since base; `git status --porcelain=v1` is empty and clean HEAD is the final row:

```text
1b428525c974d49b96f08ab2e0a586fecbb3ceee Harden image generation quality gates
94a0d08fc70e92dcc1de4217ebd34219e7fcc172 Add keyless GCP credential helper
6f49832ccee19e76dd4e3045505491049c383239 Automate orb GCP credential bootstrap
2777a172ee330b85a1ecc505cf1e433436bf82d5 Generate ads from product-free environment plates
5e61c2d7f4b6718a1e5c48096dac1199e4d2076e Benchmark generation against successful-ad quality
32854e912cfbc889b5b64a59f6f3092043c853db Improve generated scene realism and product grounding
2608cf260d5afad39ffc1b0daadfff0a67cfba9d Add versioned Meta model handoff artifacts
823708e7dcc43b886e64618ed6c2bad1070bb693 Integrate verified Meta model guidance
9c6aa0e115bf8ee72b57f40a42031837ac9fb951 Harden publication imagery composition
225891cd8d5d797d7144e2384d54e72c1476fa5a Quarantine failed generation candidates
0af075ef8c0b0c411de438be6441ca4e080ad4d4 Require evidence-backed generation asset routing
bf487971e89e9db787762e591ff8313cdb26af7b Add fail-closed non-destructive OCR safety gates
d3037baf12c3557ac69a82d3a04ecffb7fcacf2b Cover owned-scene asset admission
86c02bf3dc11aa4036cee7ced0d184525954b7f7 Require approved benchmarks for craft qualification
27b25f14714147b627e2be5866e92100da8608be Ground products with admitted scene geometry
707307c8705348ab5de7781f22a988238f1fca9c Document generation quality release controls
c9d64e52cd6a8297da0441120d2bb30a2598fe34 Record corrected v3 handoff provenance
690698aef3e717276000d436c829cc296a04a32c Freeze product extraction benchmark contracts
6023cc78f228cc90c8c7d8b799a254da31425172 Define fail-closed extracted asset provenance
410178f0ca95b657ba243888d4e02b26bd09044e Verify extracted product identity before reuse
7ed94088dace85ecb9ac56dc3b112ff4bd031af8 Preserve extracted product fringe pending calibration
6ac003f4c18d3442711afaeeda115e46c90da156 Construct extracted cutouts without pixel synthesis
27bf2a58b28164a3c5b4dd269749786e686cf2e4 Document continuous extraction validation
9778c938ea82e31d592697588d39c2c14b846fc8 Measure primary product localization separately
c08671923aa703300cff77271b4f56eed24c3342 Distinguish requested and verified scene lighting
1bc293290f89c7da83ef775d6f75c4762769b79a Document extracted asset operating boundary
48eec7117f4499e68646f3bb6b18cb17376d43b1 Run product extraction challengers under quarantine
294f3d16095085c9f878aaf0c768ea95138891ce Add pinned product localization challenger
e2e10081c0a3e3cfff48a46496e6384b7127a85f Record generation extraction milestones
868c6cbc92a2502ee468495a5ec5acd6d47181dd Persist extraction candidates only in quarantine
db9908fab4d70a2638d9ac1f75d6a06b2246f9c2 Record extraction quarantine workflow
```

### 10.4 Durable v3 and benchmark artifacts

- v3 guide/report/survival hashes are listed in §6.1.
- corrected bundle-v2 manifest SHA-256: `a36074fd9e042cbd40cdf802c12a06287eb319abb1d28b4d32fd7e945814bbfd`.
- blank 120-item/zero-label taxonomy review SHA-256, recomputed after transfer: `f7a3fbd67c9213ad1946118b4fc9f1be257434665a18a050acf9c1f490b695ae`.
- baseline observation-window SHA-256, recomputed after transfer: `88dca78228abdf63ed53521d728ef51fac8406839ecbf308d7f5c3482949c079`.
- generation grounding fixture SHA-256: `175b0d1079dea3d585cddc0cb5ad13c65b934fbfb45b9a3559742d96e4cfd141`; abstract geometry only.
- product extraction source-identity fixture SHA-256: `4ce7caa9a4578ca82e5a9bcdc2b0abffc77bb2df68c772c96231c0a148d37c9e`; abstract fixture only.

## 11. Research-agent operating rules

1. Start each report with branch/commit/clean state, immutable input hashes, model/provider revisions, seed, workers, and whether calls were paid/live.
2. Separate schema/control-flow tests from provider accuracy and visual evidence.
3. Never call a hypothesis a root cause without reproducing the failing path and excluding alternatives.
4. Never reinterpret `end_date`, missing polls, embeddings, feature importance, references, or reviewer preferences as stronger evidence than their contracts allow.
5. Compare one factor at a time on identical frozen assets/copy/guide/evaluator partitions.
6. Preserve calibration and blind holdouts; do not tune after final-test exposure.
7. Report failures, abstentions, retries, quarantine, cost, and latency—not only surviving outputs.
8. Keep source and generated assets rights-bound; reference ads are comparison-only.
9. Do not activate destructive OCR, an automatic cutout, product conditioning, a craft ranker, segment guidance, survival guidance, or v4 based on model reputation or unit tests.
10. Maintain the non-contamination invariant: only real later ad observations evaluate Meta performance; generated/reviewer evidence improves generation only.
