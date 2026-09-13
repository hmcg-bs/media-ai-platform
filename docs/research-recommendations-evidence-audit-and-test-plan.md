# Research recommendations: evidence audit and controlled test plan

**Date:** 2026-09-13

**Status:** Experiment design only; no recommendation is implemented or promoted by this report

**Scope:** Meta competitor-ad data/model pipeline and static-image generation/quality pipeline
**Repository evidence:** Meta branch `auto/meta-ads-model-pipeline` at
`a013f8c5d87e512788af616972c23562b82bf29d`; Generation assessment completed against unchanged
`feat/generation-quality` at `db9908fab4d70a2638d9ac1f75d6a06b2246f9c2`; source documents are
the exact bytes introduced on `origin/master` by
`013e5780b898e63e79ad28aa92ef2f5901d76a01`.

This report audits:

- `docs/external-research-validation-report.md` (SHA-256
  `bdb07068b816c14b00828fe58ecbfb4a4fb9903bd58633429a407d477cdd4b09`),
- `docs/industry-ad-techniques-research.md` (SHA-256
  `a2b393f0c7b42a7c53c6992ea50c42f57b84b837a1d61e8bf15fc5f297e513df`), and
- `docs/research-papers-coldstart-critique-support.md` (SHA-256
  `588f5f0c0bdf1005ea07e47539ae4df9133fc3315e93802c5bd46f875d332c87`).

The documents are useful idea inventories, but they are not reliable decision records without the
qualifications below. Paper metadata and abstracts are often accurate; several interpretations,
quantitative comparisons, and provider-internal descriptions are overstated or unsupported. The
current v3 contract remains the only generation guidance. Generated outputs, automatic defect
labels, craft-review labels, and benchmark preferences are **never** Meta performance labels.

## Executive decision

1. **Keep:** loop isolation, immutable handoffs, advertiser-disjoint temporal evaluation,
   deterministic final copy, source-pixel preservation, output quarantine, human taxonomy and
   rights approval, and fail-closed six-dimension craft review. These controls are justified by the
   system's actual failure costs and data provenance. Research provides broad supporting rationale,
   not direct validation of the exact local thresholds.
2. **Test first:** advertiser-clustered uncertainty; proxy-weight and SHAP-direction sensitivity;
   a truly later old-model evaluation; human taxonomy/product extraction audit; durable benchmark
   approval; and source-stratified ROI/matting baselines. These have high information value and do
   not weaken production safety.
3. **Do not adopt yet:** Snorkel reweighting, binary `longevity > median`, Double ML causal
   directives, conformal guarantees under current shift, Firth/standard Cox guidance, segmented
   directives, learned craft rankers, destructive OCR cleanup, or product-conditioned generation.
   Their required labels, outcomes, overlap structure, exchangeability, assets, or validation sets
   do not exist.
4. **Correct the research record:** two consecutive complete misses are a conservative operational
   detection policy, not a Turnbull estimator; YOLO-World's paper reports 20× speed versus DetCLIP,
   not Grounding DINO; IP-Adapter does not claim style-without-identity; the image-composition survey
   identifies unsolved appearance/geometry/shadow/reflection issues rather than proving this
   compositor complete; and Google/Meta product behavior does not establish their proprietary model
   architecture.

## Ground truth and current boundaries

### Meta evidence plane

- Corpus: 3,555 ads. Reproducible model matrix: 600 rows. Accepted split: 499 train / 101 test,
  newest-advertiser temporal holdout, zero advertiser overlap.
- Accepted no-embedding result: MAE 0.3550 versus training-median baseline 0.3731; top-20% precision
  0.3333. This is weak holdout evidence for a composite public-data proxy—not CTR, conversion,
  spend, ROAS, causal creative quality, or verified longevity.
- Proxy: 70% train-calibrated longevity rank + 20% longevity-rank × page-scaling-rank + 10%
  collation-rank. The weights are an explicit heuristic. Page scale and collation can encode budget,
  catalog, campaign, and advertiser strategy.
- The newest Q3 cohort is shifted. Only `other_supplements` clears the current descriptive segment
  support gate. Cross-category, trend, and conditioned copy claims are unsupported.
- The 120-ad taxonomy review is blank. Product enrichment must remain downstream of passing human
  adjudication; current coverage requirements are 50% product-page, 40% price, 25% description/USP,
  and per-field model support requires at least 100 rows and 10 advertisers.
- Exact lifecycle semantics are `is_active=false; legacy end_date heuristic only when activity
  status is absent`. The model split has zero observed events; the warehouse has two endings; all
  30/60/90/180-day horizons are insufficient. Cox directives are prohibited.
- Current uncertainty uses 2,000 paired row resamples; drift uses numeric PSI and categorical total
  variation descriptively. Promotion also requires taxonomy/product gates, clean provenance,
  ≥100 holdout rows, ≥10 holdout advertisers, zero overlap, MAE-difference CI upper bound below
  zero, and top-20% precision CI lower bound above 0.20.
- Future-window tooling requires a byte-bound, later chained window; new mature rows; target age
  bound to snapshot time; reproducible frozen parameters; taxonomy pass; and no overlap with old
  training advertisers. No qualifying future window exists yet.

### Handoff boundary

The immutable v3 guide/report/survival evidence is the only accepted contract. It re-derives exactly
four visual and four copy directives from no-embedding directional SHAP, excludes missing/pooled
levels, embeddings, campaign operations, unsupported product fields, trend/category claims, and all
Cox guidance, and carries content hashes and Git provenance. Bundle manifest v2 fixes only the
survival filename and does not change evidence bytes. Generation's production loader still uses
three fixed repository-root v3 paths; bundle discovery or a v4 schema requires a coordinated loader
migration and shadow validation. Extra product-gate metadata is not a Generation enforcement
boundary by itself; in particular, the current consumer allowlist can admit `rating` unless a future
loader explicitly validates per-field support.

### Generation/quality plane

The safe route generates a product-free plate, composites admitted source pixels, renders final copy
deterministically, and quarantines technical failures. It reduces duplicate/garbled products and
copy errors but does not solve medium mismatch, source quality, perspective, scale, occlusion,
relighting, reflections, ambient spill, or physically correct contact/cast shadows. A flat opaque
illustration cannot become a photographic packshot by pixel preservation. Publication routes need
rights-cleared transparent packshots, compatible owned scenes or medium-matched assets, plus brand
fonts/palette/logo. Scene pseudo-text remains possible. Destructive OCR cleanup lacks Vision access
confirmation and a labeled precision/recall safety benchmark. Six-dimension review (art direction,
imagery quality, typography, visual hierarchy, composition, brand cohesion) correctly fails closed,
but no approved durable benchmark corpus exists.

## Claim-by-claim evidence audit

Classification meanings: **strong** means the source directly supports the narrow claim;
**conditional** means related evidence supports testing but not adoption; **contradicted** means the
source says something materially different; **unsupported** means the cited source does not establish
the claim; **provider-specific/obsolete** means it may describe a vendor/version but cannot be
generalized or reproduced here.

| Research claim/recommendation | Classification | Audit and current mapping |
|---|---|---|
| Weak supervision validates fixed 70/20/10 weights and Snorkel can learn them without labels | **Unsupported extrapolation** | Ratner et al. define noisy, possibly conflicting labeling functions over a latent label and identify parameters only in stated settings. Three continuous, algebraically related rank ingredients are not automatically identifiable labeling functions. Keep heuristic disclosure; test sensitivity first. |
| Two consecutive misses “follows Turnbull” | **Contradicted** | Turnbull is a nonparametric maximum-likelihood estimator over observed censoring intervals. It does not prescribe how many failed API polls establish an event. The local rule is an operational false-positive control and currently approximates inferred events at first miss in Kaplan–Meier, not formal interval-censored NPMLE. |
| Double ML/econml can turn SHAP directions into causal effects | **Unsupported extrapolation** | Chernozhukov et al. address low-dimensional causal parameters under an identified estimand, valid orthogonal score, nuisance assumptions, and cross-fitting. They do not remove unmeasured budget, audience, auction, placement, or campaign confounding. Use only as sensitivity analysis after a causal graph and adequate data; never call output causal by default. |
| `longevity > median` is an easier immediate pre-launch target | **Contradicted by current data semantics** | Active ads are right-censored and the current split has zero observed endings. Thresholding snapshot age would label censoring/collection timing, not survival. Do not test until horizon outcomes mature. |
| Confident Learning can find wrong blank taxonomy or proxy labels | **Conditional/indirect** | Northcutt et al. assume class-conditional label noise and predicted class probabilities. It cannot replace initial human labels and does not directly apply to the continuous longevity proxy. After a real labeled taxonomy exists, out-of-fold CL rankings may prioritize re-review only; they cannot overwrite labels. |
| Negative-transfer literature proves two-loop isolation | **Conditional/indirect** | Wang et al. study transfer-learning performance from unrelated labeled source domains. They do not study reward-model contamination or this architecture. Loop isolation remains mandatory based on provenance/Goodhart risk, but the paper is analogy, not direct proof. |
| Prototypical Networks justify ad-archetype distances as regression features | **Conditional/indirect** | The paper trains episodic few-shot classifiers for unseen classes. Mean distances from fixed general embeddings are a separate method. Test only after validated non-exclusive archetype labels and adequate advertisers per class. |
| MAML should solve cold-start categories | **Unsupported now** | MAML needs many related training tasks and gradient-trained models. Current supported category count and rows are insufficient; XGBoost is not a direct MAML substrate. |
| Tree SHAP gives stable, causal, globally important directives | **Contradicted/qualified** | Lundberg et al. establish local accuracy/consistency for model attribution, not causal effects or stability under correlated features and resampling. Kumar et al. directly critique Shapley feature-importance interpretations. Current 4+4 are bounded provisional associations; add resampling and ablations before any new directive. |
| 50–200 rows per feature is a literature-backed SHAP minimum | **Unsupported** | No cited primary source provides this universal threshold. Effective one-hot/embedding dimensionality also makes “135 features” ambiguous. Report actual transformed dimensions, advertisers, stability, and holdout uncertainty instead. |
| R² = −163 proves accepted XGBoost catastrophic overfit | **Unsupported/mis-mapped** | The accepted v3 promotion evidence is no-embedding MAE and ranking precision. A negative R² may describe an older/component model or unstable near-constant target; the research docs do not bind it to the accepted report field. It must not override the verified 0.3550/0.3731/0.3333 evidence, which is still weak. |
| Current promotion is “standard non-inferiority” | **Incorrect terminology** | Requiring the entire MAE improvement CI below zero is a superiority gate, while precision lower bound above prevalence is another superiority criterion. It is not a pre-specified non-inferiority margin/design. |
| Conformal prediction immediately gives distribution-free valid intervals | **Conditional and currently blocked** | Angelopoulos/Bates guarantees depend on the relevant exchangeability setup; observed temporal/domain shift undermines naïve split conformal. Consider rolling/weighted methods only after multiple untouched windows; always report empirical temporal coverage. |
| Advertiser-level bootstrap is preferable | **Strong rationale; exact method unvalidated** | Rows cluster within advertiser, so row bootstrap understates dependence risk. Use cluster resampling on an untouched evaluation set, but small advertiser counts may make intervals discrete/wide. Compare both; do not tune gates to obtain promotion. |
| PSI is enough for drift; MMD/energy automatically improves it | **Conditional** | PSI/TV are descriptive marginals and bin-sensitive. Joint two-sample statistics can add detection power but need a fixed representation, permutation calibration, multiplicity policy, and enough windows; drift still does not establish trend or causality. |
| Grounding DINO is validated for primary-SKU ROI | **Conditional transfer** | The paper supports open-set detection on generic benchmarks, not ambiguous supplement SKUs or “highest score = primary product.” Local labeled product fixtures are decisive. Keep ambiguity quarantine. |
| YOLO-World is 20× faster than Grounding DINO | **Contradicted** | YOLO-World reports 52 FPS on V100 and a 20× comparison against **DetCLIP**, not Grounding DINO. Hardware, vocabulary caching, resolution, and model variants must be matched locally. Speed is secondary to wrong-SKU rate. |
| BiRefNet is proven best for product matting | **Conditional** | BiRefNet reports strong high-resolution dichotomous segmentation, not alpha matting of transparent/reflective retail packaging. Benchmark masks/alpha edges by material; do not infer commercial fitness or licensing from accuracy. |
| SAM 2/Grounded-SAM 2 unifies ROI and matting safely | **Conditional** | SAM 2 supports promptable segmentation and reports image improvements versus SAM. The combined third-party pipeline and product identity/alpha quality are not established by the SAM 2 paper. Evaluate each stage and end-to-end error propagation. |
| IP-Adapter paper confirms “style, not identity” | **Partly supported, overstated** | The paper describes decoupled text/image conditioning, explicitly notes that reference resemblance does not attain high subject consistency, and does not establish a style-only mechanism, duplicate-product rate, or exact SKU identity. This supports keeping it out of exact-identity production routing, subject to a stringent local identity test. |
| AnyText validates deterministic typography and proves small/long-text failure rates | **Conditional** | AnyText confirms visual text is an active failure area and uses glyph, position, mask, and OCR signals. The research docs' specific `<16pt` and exponential-error claims are not established by the abstract. Deterministic copy remains safer because it is directly testable. |
| ObjectStitch proves visible-product inpainting creates phantom products | **Unsupported as stated** | The cited work is a compositing method, not evidence for a universal phantom-product rate. Local duplicate/identity failures justify quarantine; cite local tests, not the paper, for that defect. |
| The composition survey proves every category is solved locally | **Contradicted** | The survey names placement, blending, harmonization, shadow, reflection, geometry, appearance, and semantic consistency. Current product-free compositing leaves several weak. The survey is a test taxonomy, not validation of completeness. |
| PaddleOCR ensemble is standard and consensus will improve safety | **Unsupported/provider-specific** | Neither cited paper establishes this ad-specific ensemble. OR/AND voting trades precision and recall differently. Benchmark Cloud Vision, PaddleOCR, and fusion policies on labeled ordinary/pseudo/protected/non-text regions before use. |
| CLIP can reliably classify pseudo-text | **Unsupported** | No cited primary benchmark supports this use. A generic embedding classifier may shortcut on style and miss glyph-like artifacts. Treat only as a challenger after labeled fixtures. |
| G2 precision ≥0.99 / recall ≥0.95 are literature-validated | **Unsupported threshold, valid safety posture** | The exact values are product policy. Keep them pre-registered because false deletion is costly, but demonstrate confidence bounds and zero protected-region damage; do not claim literature calibration. |
| The 98% pixel-contrast rule is WCAG compliance | **Contradicted/overstated** | W3C defines contrast ratios (normally 4.5:1; 3:1 for large text) and includes exceptions such as text that is part of significant other visual content. The local pixel-distribution rule is a stricter custom **WCAG-inspired legibility** heuristic, not categorical compliance. |
| Automated aesthetic models can reject bottom 10% safely | **Conditional, blocked** | Generic aesthetic datasets are not ad-craft or brand-rights benchmarks. A negative filter still creates false-rejection risk. Test only after a durable human-rated corpus; no automated pass/accept. |
| Six-dimension human review is a validated performance metric | **Conditional** | It is an appropriate publication-craft rubric, not an ad-performance outcome. Human approval remains authoritative for generation and isolated from Meta labels. Exact ≥4/5 and κ thresholds are policies to calibrate, not published universal constants. |
| Product-page extraction/coverage thresholds are externally validated | **Conditional implementation policy** | Deterministic JSON-LD/Shopify extraction is auditable; ZenRows is a retrieval provider, not evidence of correctness. The 50/40/25% and 100-row/10-advertiser gates are conservative local policies. Validate field accuracy manually and preserve currency/unit/bundle semantics. |
| Meta/Google/TikTok/Amazon use the described embedding, uplift, bandit, and fatigue architectures | **Provider-specific/unverified** | Official Meta docs verify ad relevance diagnostics and that they are retrospective, unavailable below 500 impressions, and not auction inputs. Official Google docs describe Ad Strength as relevance/quantity/diversity feedback and not Ad Rank/auction eligibility. The broader proprietary architecture and “billions per day” claims are not established by cited primary sources. |
| AdCreative.ai/Pencil internal data, market size, and performance lifts | **Unsupported commercial claims** | Self-reported marketing and reviews are not reproducible primary evidence. Do not use for architecture or thresholds. |
| Font/model licenses make the challengers safe to deploy | **Needs asset-level verification** | Verify every exact font/model file, version, license text, modification/embedding terms, weights and redistribution path in the manifest. “OFL” (not “OFN”) does not eliminate provenance duties. YOLO-World paper/code materials identify a CC BY-NC-SA status, so commercial use needs explicit clearance rather than inference from technical availability. |

## Current implementation map

| Boundary | Current owner/path | Audit consequence |
|---|---|---|
| Proxy construction | `pipeline/model_training/success_score.py` | Train-only calibration prevents target leakage, but weights remain heuristic and associational. |
| Temporal split | `pipeline/model_training/evaluation.py` and `run_training.py` | Correct advertiser isolation; one holdout is not evidence of temporal stability. |
| Uncertainty/drift/promotion | `pipeline/model_training/model_evidence.py` | Objective fail-closed checks exist; cluster bootstrap and multi-window drift are challengers, not replacements yet. |
| Taxonomy | `pipeline/validation/taxonomy_adjudication.py` | Human-only labels, ≥20% double review, κ≥0.80, independent adjudication. Blank labels block training. |
| Product enrichment | `pipeline/validation/product_enrichment.py`, `pipeline/supplements_workflow.py` | Free structured extraction then ZenRows; field support and corpus hash are enforced upstream. No completed coverage evidence yet. |
| Lifecycle/survival | `ingestion/ad_lifecycle.py`, `pipeline/model_training/validate_survival.py` | Two-miss policy is conservative inference; current Kaplan–Meier approximation is not Turnbull. Zero model events block Cox. |
| Future old-model evaluation | `pipeline/model_training/observation_windows.py`, `evaluate_future_window.py` | Correct frozen, chained, mature, old-model-first design; no actual future window yet. |
| SHAP → guide | `pipeline/generation/guide.py` | Reliability ratio ≥0.10 and allowlists filter signals, but do not prove causal/stable direction. |
| Immutable boundary | `pipeline/model_training/handoff_bundle.py` | v3 hashes/semantics and no-event evidence freeze correctly; v4 needs coordinated consumer migration. |
| Generation admission/routes/evaluation | Generation feature branch and joint plan | Rights/assets, technical gates, physical coherence, and six craft dimensions remain a separate quality plane. |

## Prioritized experiment registry

All experiments write immutable manifests containing input/code/model/config hashes, random seed,
split IDs, costs, latency, failures, and exclusions. No experiment can overwrite v3 or relax
quarantine. “95% CI” below means a pre-specified interval appropriate to the design; where no
defensible fixed sample size exists, run a blinded pilot, estimate variance/error prevalence, then
pre-register the confirmatory sample rather than inventing power.

### E1 — clustered uncertainty and promotion sensitivity (P0, Meta)

- **Hypothesis:** row bootstrap understates uncertainty relative to advertiser-cluster resampling,
  and any promotion conclusion robustly survives the appropriate clustered design.
- **Inputs/prerequisites:** frozen 499/101 split with advertiser IDs; no new labels.
- **Baseline/challenger:** current 2,000 paired row bootstrap versus paired advertiser-cluster
  bootstrap; report 500/1,000/2,000/5,000 resample convergence.
- **Design/metrics:** resample test advertisers with all their rows; MAE difference, calibration gap,
  top-20% precision; number/effective size of clusters; CI width and endpoint stability; runtime.
- **Gate:** challenger may replace uncertainty reporting only if deterministic, coverage is valid in
  simulation with matching cluster sizes, and results are not used to retroactively tune the
  promotion threshold. If clustered CI fails current promotion, remain blocked.
- **Safety/rollback/dependencies:** no labels change; keep old report field and add versioned method.
  Revert report selector to row bootstrap if implementation validation fails. Runs immediately.

### E2 — proxy-weight and ingredient sensitivity (P0, Meta)

- **Hypothesis:** v3 directive signs and holdout ranking are stable to plausible weight perturbations,
  or instability reveals that the heuristic cannot support directives.
- **Inputs:** frozen matrix/split; pre-register simplex grid including leave-one-ingredient-out and
  ±0.10 reallocations that sum to 1.
- **Baseline/challenger:** 70/20/10 versus each fixed alternative; no selection on the outer holdout.
- **Metrics/design:** inner advertiser-temporal tuning only; outer MAE, precision, Spearman prediction
  agreement, top-set Jaccard, 4+4 sign/reliability agreement; multiplicity-adjusted descriptive CIs;
  CPU cost/latency.
- **Gate:** do not change weights from this analysis. Retain 4+4 only if signs are stable across
  pre-registered perturbations and later temporal confirmation; otherwise downgrade unstable
  directives. Roll back by selecting immutable v3. Runs after E1 method is fixed.

### E3 — SHAP robustness and simpler model baselines (P0/P1, Meta)

- **Hypothesis:** a directive is credible only if its sign survives advertiser-cluster refits and its
  feature group adds holdout value against simpler models.
- **Inputs:** frozen non-embedding data; transformed-feature inventory and correlation groups.
- **Baseline/challengers:** current XGBoost/Tree SHAP; ElasticNet; optionally EBM if licensing and
  dependency review pass. Use grouped permutation, leave-feature-group-out retraining, and ALE only
  where support is adequate. BART is deferred as disproportionate.
- **Design:** nested advertiser-temporal splits; outer holdout touched once. Bootstrap whole
  advertisers and refit the full pipeline. Report sign agreement, reliability-ratio distribution,
  rank overlap, MAE, calibration, top-20% precision, fit/inference cost.
- **Gate:** a future directive needs ≥0.80 sign agreement as a provisional engineering threshold,
  agreement across at least two importance/ablation views, outer performance gates, and untouched
  future-window confirmation. Threshold is not literature-derived. Failure preserves v3 or removes
  the signal; never substitute native gain importance as a direction.

### E4 — untouched future-window old-model evaluation (P0, Meta; sequential data dependency)

- **Hypothesis:** frozen v3 model retains calibration/ranking on genuinely later mature ads and does
  not depend on old advertisers.
- **Inputs:** later immutable scrape/window, passing taxonomy and product gate, snapshot-bound target
  age ≥30 days or explicit inactive status, and no old-training advertiser overlap.
- **Baseline/challenger:** frozen training-median predictor versus frozen old no-embedding model;
  absolutely no retuning on the future window.
- **Metrics:** clustered MAE difference/CI, calibration gap and slope by temporal cohort, top-20%
  precision/CI, segment coverage, PSI/TV plus experimental joint drift statistic, cost/latency.
- **Gate:** all preconditions, ≥100 rows/10 advertisers, MAE CI upper <0, precision CI lower >0.20,
  and no material calibration failure under a pre-registered tolerance. Otherwise v3 remains
  provisional and no v4. Rollback is artifact selection, never rewriting the future window.

### E5 — taxonomy completion and adjudication quality (P0, Meta + humans)

- **Hypothesis:** keyword-seeded corpus contains material contamination and the fixed review process
  can produce reproducible supplement/subcategory labels.
- **Inputs/rights:** frozen 120-row sample/snapshots; authorized reviewers; definitions for included,
  excluded, ambiguous, topical, pet, equipment, and bundled products.
- **Design:** all rows primary-reviewed; randomly assigned ≥20% independent double review with both
  classes represented; independent adjudicator for every disagreement. Model suggestions hidden
  during initial labels.
- **Metrics:** coverage, prevalence by stratum, Cohen κ with interval, disagreement reasons,
  adjudication rate/time. Current pass gate: all labels, double fraction ≥0.20, κ≥0.80, complete
  independent adjudication.
- **Safety/rollback:** no model or Cleanlab output may create/overwrite labels. Failed version yields
  no approved-label file. Supersede with a new immutable review version after rubric correction.

### E6 — product extraction accuracy and incremental coverage (P0/P1, Meta + humans)

- **Hypothesis:** free structured extraction is accurate where present; ZenRows adds enough accurate
  price/rating/description/USP coverage to justify cost.
- **Inputs/rights:** only taxonomy-approved linked ads; immutable fetched-page provenance and terms/
  access review; human audit set stratified by domain, extraction method, currency, bundles,
  subscriptions, missingness, and dynamic pages.
- **Baseline/challenger:** free Shopify/JSON-LD/HTML versus free+ZenRows. LLM fallback is out of scope.
- **Design/metrics:** deduplicated URLs; blind human transcription of product identity, normalized
  price/currency/unit, rating/count, description/USP and subscription; exact/field precision,
  recall/coverage, stale-page rate, mismatch rate, incremental successful fields per paid request,
  dollars/URL, p50/p95 latency, retries.
- **Sample/confidence:** pilot 30–50 URLs across strata; then size confirmatory audit so the one-sided
  lower 95% bound for each model-supported field exceeds its pre-registered accuracy floor. No floor
  is invented here; owner/domain review must set consequences first.
- **Gate:** upstream 50/40/25 coverage plus 100 rows/10 advertisers per supported field, content hash
  binding, and approved accuracy floors. Missing/ambiguous values remain null. Failure disables that
  field and retains free-only data; it cannot be imputed into “success.”

### E7 — product-field predictive ablation (P2, Meta; blocked by E5/E6/E4)

- **Hypothesis:** an accurately extracted product field improves untouched temporal prediction beyond
  creative features without becoming a confounded generation prescription.
- **Baseline/challenger:** accepted no-embedding specification versus one pre-registered product-field
  group at a time, then a fixed combined group.
- **Design/metrics:** nested advertiser-temporal training; old model first on future window; clustered
  MAE/precision/calibration, missingness-stratum performance, sign stability, advertisers/rows,
  extraction/model cost.
- **Gate:** field-specific data and accuracy gates plus E4 promotion and stable ablation evidence.
  Product fields remain context, not directives, until Generation validates a coordinated v4 gate.
  Rollback removes the group and selects v3.

### E8 — lifecycle poll-policy validation and interval-censored analysis (P1, Meta)

- **Hypothesis:** repeated complete page inventories reduce false inactive inference, and preserving
  `(last_active, first_missing]` intervals avoids false event precision.
- **Inputs:** recurring polls with positive-control completeness, immutable raw responses, explicit
  inactive/stop evidence, request/error telemetry. No event may be fabricated from absence.
- **Baseline/challengers:** explicit-only; one miss; two misses; three misses. Compare delayed returns
  and later explicit statuses. Separately compare current first-miss point approximation with a true
  interval-censored estimator only after enough intervals exist.
- **Metrics:** false-inactive reversals, detection delay, completeness by page/provider, intervals and
  explicit events, cost/poll, latency/error rate. Confidence intervals cluster by page.
- **Gate:** keep two misses unless a pre-registered alternative lowers false inference without
  unacceptable delay. Statistical survival guidance remains blocked until event/holdout gates pass.
  Never describe the operational rule as Turnbull.

### E9 — survival models (P2+, Meta; currently untestable)

- **Hypothesis:** after events mature, interval-aware descriptive survival and a parsimonious model
  generalize to held-out advertisers/time.
- **Inputs:** sufficient explicit/validated inferred event intervals, covariate degrees of freedom,
  test events and admissible pairs. Current 0/0 model events and two warehouse endings fail entry.
- **Baseline/challengers:** Kaplan–Meier/right-censored approximation, interval-censored NPMLE, and
  only then pre-specified Cox/discrete-time/penalized alternatives. “Firth Cox at 20 events” is not
  accepted without implementation/library and method-specific validation.
- **Metrics:** horizon coverage/calibration, time-dependent Brier score, C-index only with admissible
  pairs, coefficient/sign resampling stability, events per degree of freedom, cost.
- **Gate:** joint-plan minimum 30 train and 10 test events, supported horizons, stable coefficients,
  and untouched temporal evaluation. Failure emits empty guidance. Rollback keeps v3 no-Cox state.

### E10 — marginal/joint drift and temporal calibration (P1, Meta; needs more windows)

- **Hypothesis:** a fixed joint drift statistic detects changes missed by PSI/TV and predicts model
  calibration degradation.
- **Baseline/challenger:** PSI/TV versus MMD or energy distance selected before seeing future labels.
- **Design:** freeze preprocessing/kernel/bandwidth and permutation test on baseline; evaluate across
  at least three independent later windows. Report false alarms in within-window splits, power on
  documented synthetic shifts, calibration error, multiplicity, compute/latency.
- **Gate:** adopt only as an additional alert if calibrated false-positive rate and reproducibility
  pass; never auto-promote, claim trend, or change directives from drift alone. Rollback removes the
  alert field without touching evidence.

### E11 — conformal uncertainty (P3, Meta; blocked)

- **Hypothesis:** a pre-specified temporal/weighted conformal method achieves useful empirical
  coverage under observed shift.
- **Inputs:** multiple untouched, adequately sized windows; exchangeability/weighting rationale;
  frozen model and calibration protocol.
- **Baseline/challenger:** bootstrap prediction evidence versus split/rolling/weighted conformal
  intervals. Metrics: marginal and segment coverage, interval width, abstention, drift sensitivity,
  cost. Target nominal coverage and allowable width must be pre-registered from use consequences.
- **Gate:** coverage lower CI meets target in later windows and intervals are operationally useful.
  No “distribution-free” claim outside the method's assumptions. Failure retains bootstrap reporting.

### E12 — creative taxonomy segmentation/prototypes (P3, joint; blocked)

- **Hypothesis:** three independent co-occurring axes—creative archetype, hook framework, CTA type—
  are reliably labelable and support calibrated segment-specific evidence.
- **Inputs:** human-validated definitions and multilabel data for archetypes (direct response, brand
  story, educational, testimonial/UGC, comparison), hooks (Direct Offer, PAS, social proof,
  curiosity, problem/solution), and CTA (purchase, subscribe, learn, engage, absent).
- **Design:** blinded multi-reviewer labels; advertiser-disjoint temporal prevalence; compare global
  v3 against segment interactions and, separately, prototype-distance features trained only on train.
  Metrics: per-axis agreement, rows/advertisers, calibration, clustered MAE/precision, sign stability,
  rare/multilabel coverage and cost.
- **Gate:** every emitted segment needs validated labels, ≥100 rows/10 advertisers as the current
  minimum, temporal coverage/calibration, and independently re-derived stable SHAP direction.
  Unsupported segments inherit global v3. MAML is deferred until many genuinely related tasks exist.

### E13 — ROI detector benchmark (P1, Generation)

- **Hypothesis:** a challenger reduces wrong-primary-SKU selection or ambiguity without weakening
  quarantine; speed is secondary.
- **Inputs/rights:** human-labeled ad fixtures with all candidate boxes and primary SKU, stratified by
  single/multiple product, text-heavy, illustration/photo/3D, small/occluded product and category.
- **Baseline/challengers:** pinned Grounding DINO versus YOLO-World; Florence only after license/
  deployment review. Same hardware, resolution, prompts, warmup and vocabulary-caching accounting.
- **Metrics/design:** blinded fixture test; primary-SKU recall, precision, IoU, wrong-selection and
  abstention rates, query perturbation stability, p50/p95 latency, memory and per-image cost; paired
  CIs by image.
- **Gate:** one-sided 95% upper bound on wrong-selection must not exceed the pre-registered safety
  ceiling and recall must improve/non-infer within an explicit margin. Keep ambiguity quarantine.
  Rollback selects pinned detector. No “20× Grounding DINO” prior and no challenger without
  commercial-license clearance.

### E14 — product segmentation/matting (P1, Generation)

- **Hypothesis:** BiRefNet or detector-prompted SAM 2 improves boundary quality over the current
  remover without product-pixel loss.
- **Inputs/rights:** owned/approved high-resolution ground-truth masks/alpha mattes stratified by
  bottle, box, pouch, reflective, transparent, fine detail, clipped and occluded; license review.
- **Baseline/challengers:** current pinned remover; BiRefNet; SAM 2 with frozen detector boxes;
  transparent-specific method only within that stratum.
- **Metrics:** alpha MSE/SAD where true alpha exists, IoU and boundary F-score, edge-color/halo score,
  source-pixel retention, catastrophic deletion, p50/p95 latency, GPU memory/cost. Blind human edge
  review validates metric relevance.
- **Sample/confidence:** pilot at least 30 owned/approved cases per material stratum where available;
  use pilot variance and critical-defect prevalence to size a separately frozen confirmatory set,
  clustering uncertainty by product/brand rather than render.
- **Gate:** challenger must improve pre-specified primary metric in every routed stratum, have zero
  critical identity deletions in the confirmatory set, and pass license/deployment review. Otherwise
  quarantine/manual cutout or baseline. Do not call binary segmentation alpha matting.

### E15 — scene/product route and physical coherence (P1/P2, Generation)

- **Hypothesis:** rights-cleared owned scenes outperform product-free generated plates; a grounded
  composite challenger can improve physical coherence while preserving exact identity.
- **Inputs:** real transparent packshots, compatible owned scenes or medium-matched assets, brand
  kit and rights manifests. Same product/copy/v3 guide across paired routes.
- **Baseline/challengers:** frozen deterministic product-free plate compositor; owned-scene edit;
  grounded composite. Product-conditioned diffusion is a separate last challenger only after identity
  fixtures exist.
- **Design/metrics:** randomized candidate order and seeds; blinded reviewers rate medium match,
  scale/perspective, light direction, contact/shadow, reflection/ambient spill/occlusion plus all six
  craft dimensions; technical duplicate/OCR/collision/edge gates; pairwise win rate with advertiser/
  product/run-disjoint confirmatory set; generation cost, p50/p95 latency, retries/quarantine.
- **Sample/confidence:** pilot at least 30 outputs per route; promote only on a separately frozen set
  of at least 100 outputs for the selected challenger, reviewed by three blinded reviewers. Use the
  pilot to power the final comparison rather than treating these minimums as sufficient by fiat.
- **Gate:** zero identity/OCR critical defects; all six dimensions present and passing; pairwise win
  and publication-ready rate confidence interval excludes no improvement over baseline, median
  ≥4/5 on every dimension, and reviewer κ ≥0.70 where κ is appropriate. Otherwise retain baseline/
  manual/owned route. Never source pixels/style/layout from benchmark ads.

### E16 — pseudo-text and OCR detection/fusion (P1, Generation; blocked by labels/access)

- **Hypothesis:** an OCR/fusion challenger improves unexpected-scene-text recall without damaging
  final-copy verification or protected-product regions.
- **Inputs:** Vision permission decision and human-labeled regions for ordinary scene text,
  pseudo-text, deterministic final copy, protected product text, and non-text, with difficult contrast
  and scripts; rights-cleared images.
- **Baseline/challengers:** Cloud Vision detection-only; PaddleOCR; OR/AND/score fusion; CLIP filter
  only as a separately evaluated challenger.
- **Metrics/design:** frozen threshold tuning set and blind test; region precision/recall, CER for
  canonical copy, pseudo-text recall, protected/non-text false positives, latency/cost. Report
  one-sided confidence bounds, not point values alone.
- **Gate:** detection may assist quarantine only after benchmark evidence. Destructive cleanup remains
  off until precision ≥0.99, recall ≥0.95, zero protected-label deletion and zero material non-text
  damage on blind test; missing OCR fails closed. Rollback disables challenger/cleanup.

### E17 — deterministic typography/layout search (P2, Generation)

- **Hypothesis:** a small deterministic candidate search improves six-dimension typography,
  hierarchy, and composition without copy, collision, contrast, or brand violations.
- **Inputs/rights:** licensed font-file manifests, approved brand assets, same admitted packshots/
  scenes, approved benchmark rubric. Correct `OFL`, not `OFN`.
- **Baseline/challenger:** current largest-region/three-role layout versus pre-registered 4–8 candidate
  search over zones and licensed font pairs. AnyText is an optional research comparator, never the
  production-copy baseline.
- **Metrics:** exact OCR/CER, overflow/collision/safe-zone/contrast, reviewer pairwise preference and
  six dimensions, diversity, render latency/cost. Brand/product/run-disjoint blind set.
- **Gate:** zero hard-gate regressions and confirmatory pairwise/six-dimension improvement with CI
  excluding no improvement. Rollback selects frozen deterministic layout configuration.

### E18 — durable benchmark and evaluator calibration (P0/P1, Generation + humans)

- **Hypothesis:** an immutable, rights-approved, advertiser-diverse reference corpus and calibrated
  rubric produce reproducible six-dimension judgments.
- **Inputs/rights:** locally persisted images—not expiring URLs—with source/ad ID, content hash,
  rights/usage, taxonomy, approval identity/date, and `directive_aligned=false`; references are
  comparison-only.
- **Design:** freeze train/calibration/blind-test partitions before evaluator tuning; at least two
  reviewers per calibration/test item, randomized ordering, concrete defect labels plus six ratings.
  Pilot enough items to estimate dimension prevalence/variance, then power the confirmatory pairwise
  comparison for the smallest worthwhile win-rate improvement. Track reviewer time/cost.
- **Metrics/gate:** per-dimension agreement and κ/ICC as appropriate, missing/duplicate dimension
  rejection, reviewer bias, benchmark segment/medium/advertiser coverage. No candidate can pass
  without exactly one result for every dimension and human approval. Automated scorers/rankers may
  be studied only after ≥200 human-rated examples and strict brand/product/run-disjoint splits; they
  may never auto-accept. Rollback pins prior rubric/corpus and re-scores stored candidates.

### E19 — first-party randomized creative outcomes (P5, joint; unavailable)

- **Hypothesis:** a controlled creative change causes improvement in an owner-selected business
  outcome.
- **Inputs:** authorized first-party ad account, spend, privacy/compliance plan, sufficient traffic,
  pre-registered outcome and stopping rule. Competitor data cannot substitute.
- **Design:** platform-supported randomization/bandit only after an initial fixed-horizon A/B design;
  advertiser/audience/auction controls, intent-to-treat analysis, guardrails and cost accounting.
- **Gate:** appropriately powered effect/CI and safety/business approval. New outcomes require a new
  schema/ADR and may inform later models only as explicitly versioned real deployed-ad evidence.
  Generated candidates alone never become performance labels. Rollback pauses the challenger.

## Recommendations not currently testable

| Recommendation | Why entry fails | Earliest dependency |
|---|---|---|
| Snorkel-learned proxy weights | No latent-label identifiability design, overlapping discrete labeling functions, or gold validation labels | Define estimand/LFs and acquire a gold outcome subset; E2 first |
| Binary longevity classifier | Snapshot age is censored; zero model events | Mature E8/E9 horizons |
| Causal creative directives/Double ML | Major unmeasured confounders and no defensible treatment assignment/estimand | First-party randomization or substantially richer measured design |
| Firth Cox, standard Cox, RSF, DeepSurv | 0 train/0 test events; two warehouse endings | E8 lifecycle accumulation and E9 entry gates |
| Conformal guarantee | One shifted holdout; no sequence of exchangeable/weighted validation windows | E4 across multiple later windows |
| Segment-conditioned guidance/prototypes | Blank taxonomy and inadequate segment advertisers/rows | E5, E4, E12 labels/support |
| MAML/domain meta-learning | Too few validated related tasks/categories and rows | Multiple mature supported categories |
| Embedding revisit/PLS/exemplar similarity | Current raw embeddings harmed holdout; no larger validated corpus or frozen exemplar rights/partition | ≥2,000 is only a planning trigger, not proof; E4/E5 first |
| Destructive OCR cleanup | No labeled benchmark and permission is unresolved | E16 inputs and safety gate |
| Product-conditioned generation | No real identity fixtures/packshots and prior local duplicate/identity failures | E14/E15 assets and identity gate |
| Learned shadow/relighting | No physical-coherence labels or owned asset fixtures | E15 benchmark |
| Automated craft ranker/bottom-decile rejection | No approved durable human-rated corpus | E18; ≥200 labels is a minimum policy trigger, not power proof |
| Creative bandits/conversion uplift | No first-party account/outcomes/spend/randomization | E19 and explicit owner authorization |

## Dependency-aware execution order

```diagram
┌──────────────────────── parallel now ─────────────────────────┐
│ E1 clustered CIs ─▶ E2 proxy sensitivity ─▶ E3 SHAP/baselines│
│ E5 human taxonomy ─▶ E6 extraction audit                     │
│ E8 lifecycle polling/validation                              │
│ E18 rights + benchmark rubric                                │
│ E13/E14 fixture and license preparation                      │
└───────────────┬─────────────────┬─────────────────────────────┘
                │                 │
      E5+E6+later window          E14+E18+admitted assets
                ▼                 ▼
       E4 old-model-first      E15 route comparison
                │                 ├─▶ E16 OCR (after auth/labels)
                ├─▶ E7 fields    └─▶ E17 typography/layout
                ├─▶ E10 drift             │
                └─▶ E12 segments           ▼
                     │              optional craft ranker
          multiple mature windows
                     ├─▶ E11 conformal
          sufficient validated events ─▶ E9 survival

          first-party authorization/outcomes ─▶ E19 randomized effects
```

E1, E5, E8, E18, and fixture/rights preparation can proceed independently. E2 follows the
uncertainty decision; E3 follows the fixed split/resampling protocol. E4 is the Meta critical path:
no v4 model evidence should be selected before a real later old-model-first evaluation. Generation
route experiments require admitted assets and approved comparison references; OCR cleanup requires
its own labeled safety evidence. Model-based craft ranking comes after human corpus calibration, not
before.

## Leakage, safety, and rollback rules for every experiment

1. Freeze outer temporal/advertiser or brand/product/run test partitions before feature, threshold,
   prompt, model, or evaluator tuning. All parameter/model/weight selection stays inside training.
2. Keep target ingredients out of predictors. Keep generated/reviewer/benchmark labels out of the
   Meta corpus. Only real later ads or explicitly versioned first-party deployed outcomes can enter
   the observed-performance loop.
3. Hash raw inputs, derived rows, labels, model/report bytes, code commit/branch/dirty state, provider
   model/revision, and manifests. Never overwrite v3 or historical benchmark versions.
4. Report denominator, ads, advertisers, segments, dates, missingness/fallbacks, intervals, seeds,
   retries, cost, latency, and abstention/quarantine—not only successful cases.
5. Failed/missing/ambiguous/rights-unknown states fail closed. No threshold may be relaxed after
   looking at blind results. A changed threshold requires a new preregistered experiment/version.
6. Promotion is alias/config selection of immutable artifacts. Rollback restores the prior alias or
   disables one independent route/gate; it never edits evidence or releases quarantined outputs.

## Primary-source quality record

Directly read for this audit:

- Ratner et al., [Data Programming](https://arxiv.org/abs/1605.07723).
- Chernozhukov et al., [Double/Debiased Machine Learning](https://arxiv.org/abs/1608.00060).
- Kumar et al., [Problems with Shapley-value-based explanations](https://arxiv.org/abs/2002.11097),
  and Lundberg et al., [Tree SHAP](https://arxiv.org/abs/1802.03888).
- Angelopoulos and Bates, [Conformal prediction](https://arxiv.org/abs/2107.07511).
- Wang et al., [Negative transfer](https://arxiv.org/abs/1811.09751); Northcutt et al.,
  [Confident Learning](https://arxiv.org/abs/1911.00068); Snell et al.,
  [Prototypical Networks](https://arxiv.org/abs/1703.05175); Finn et al.,
  [MAML](https://arxiv.org/abs/1703.03400).
- Zhang and Sun, [Interval censoring review](https://pmc.ncbi.nlm.nih.gov/articles/PMC3684949/),
  including its account of Turnbull's NPMLE.
- Grounding DINO, [arXiv:2303.05499](https://arxiv.org/abs/2303.05499); YOLO-World,
  [arXiv:2401.17270](https://arxiv.org/abs/2401.17270); BiRefNet,
  [arXiv:2401.03407](https://arxiv.org/abs/2401.03407); SAM 2,
  [arXiv:2408.00714](https://arxiv.org/abs/2408.00714).
- IP-Adapter, [arXiv:2308.06721](https://arxiv.org/abs/2308.06721); AnyText,
  [arXiv:2311.03054](https://arxiv.org/abs/2311.03054); and the
  [Deep Image Composition survey](https://arxiv.org/abs/2106.14490).
- Official [Meta ad relevance diagnostics](https://www.facebook.com/business/help/403110480493160)
  and official [Google Ad Strength](https://support.google.com/google-ads/answer/9921843).
- W3C, [Understanding WCAG 2.1 contrast minimum](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html).

The source documents cite additional papers and commercial/provider assertions for which they give
no stable primary URL, exact table/section, version, or reproducible evidence. Those claims are not
silently accepted. In particular, the Pitt Ads performance numbers, `StructuredRAG`/Gemini benchmark
claims, Gordon/Lewis causal interpretations, exact events-per-variable recommendations, Firth-Cox
operating threshold, ObjectStitch phantom-product statement, OCR/pseudo-text rates, aesthetic-model
bias claims, vendor market size, commercial tool internals, and Meta/TikTok/Amazon proprietary model
architecture remain **unresolved source-quality issues** until exact primary passages are supplied.
GitHub stars and PyPI presence are popularity/availability metadata, not technical validation; they
also age immediately and should not appear as evidence thresholds.

## Adoption decision

No researched method is adopted by this audit. The immediate experimental sequence is E1 clustered
uncertainty, E2 proxy sensitivity, E5 taxonomy review, E6 product extraction audit, E8 lifecycle
policy validation, and E18 durable benchmark approval in parallel; then E3 and the first genuine E4
future-window evaluation. Generation may prepare E13/E14 fixtures but must not change production
routes before rights, identity, and blind holdout gates. v3 exact 4+4 guidance, output quarantine,
six-dimension fail-closed review, and loop isolation remain unchanged.

Both workstreams stay on their feature branches. Nothing in this report authorizes pushing or
merging to `master`.
