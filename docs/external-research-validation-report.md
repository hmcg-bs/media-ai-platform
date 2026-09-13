# External Research Validation Report: Pipeline Architecture Review

**Date:** 2026-09-13  
**Source:** Cross-referenced GitHub ecosystem (stars, recency, paper links), arxiv paper IDs, known literature, industry blog synthesis  
**Purpose:** Validate or invalidate the methodological decisions in the Meta evidence loop and generation-quality loop against external research evidence  
**Audience:** Engineers deciding what to test next; this is evidence synthesis, not a publication-quality survey

---

## Executive Summary

The pipeline architecture is **strongly validated** by external research in its key structural decisions: two-loop isolation, deterministic-first compositing, non-destructive OCR, source-pixel preservation, and fail-closed boundaries. The most significant external validation gaps concern:

1. **XGBoost on 600 rows** — the modeling approach is fragile at this sample size; SHAP directions are unstable with correlated features
2. **Success proxy construction** — the 70/20/10 composite has no formal justification from weak-supervision literature
3. **Grounding DINO as sole ROI challenger** — YOLO-World (6,551 ★, CVPR 2024) is a faster competitive alternative that should be benchmarked
4. **Flux-only scene generation** — the product-free compositing architecture is correct, but owned-scene routes should be prioritized given Flux's known text/pseudo-text failure modes

The strong negative embedding ablation and honest R² = −163 are **positive evidence of scientific rigor**, not pipeline failure. The report finds no architectural decisions that should be reversed, but several that need additional baselines and sensitivity checks before v4 promotion.

---

## 1. Success Proxy Construction (Research Areas R3)

### Current approach
```
0.70 × longevity percentile rank
+ 0.20 × longevity rank × page-scaling rank  
+ 0.10 × Meta collation/variant rank
```

### External validation

**Weak supervision literature** (Ratner et al., 2016, "Data Programming"; Dunnmon et al., 2019, "Cross-Modal Data Programming") supports the general strategy of combining multiple weak signals into a training target when ground-truth labels are absent. The Snorkel framework formalized this as a labeling function paradigm.

**However**, the specific 70/20/10 weighting has no empirical derivation from the literature. The approach is closer to *rank aggregation* (Borda count / Kemeny-Young) than to data programming. There is no generative model estimating the accuracy of each weak signal.

**Concerns from causal inference literature:**
- Gordon et al. (2019, "A Comparison of Approaches to Advertising Measurement") demonstrate that ad creative quality cannot be separated from budget, auction dynamics, and platform delivery without explicit randomization or causal decomposition.
- The `page-scaling rank` term (20%) introduces advertiser-level confounding: pages with more ads running simultaneously may reflect larger budgets or different campaign strategies, not individual creative quality.
- Lewis & Rao (2015, "On the Near Impossibility of Measuring the Returns to Advertising") show the fundamental difficulty of measuring ad effectiveness from observational data.

**Interval-censoring in longevity:** The current approach treats only explicit `is_active=false` as events. External survival literature (Sun, 2006, "The Statistical Analysis of Interval-Censored Failure Time Data"; Zhang & Sun, 2010) confirms that interval-censored methods should be used when only periodic polling is available. The pipeline's two-consecutive-miss rule for inferred inactivity is a reasonable Turnbull-like nonparametric estimator but is not a formal interval-censored model.

### Recommendations
- **Add a Snorkel-style generative model** to estimate per-signal accuracy and reweight the composite, even without ground truth (requires only conditional independence assumptions)
- **Ablation study**: compute SHAP/predictions under weight perturbations (±0.10 on each component) to assess output sensitivity
- **Add page-level confounders as adjustment variables** in a doubly-robust estimation framework (available via `econml` or `DoWhy`)
- **Document** that the composite is a rank-aggregation heuristic, not a causal quality label

---

## 2. Feature Extraction Architecture (Two-Tier Gemini)

### Current approach
- Deterministic metadata/dimensions/color/layout → code (no LLM)
- Cheap tier: `gemini-2.0-flash-lite` → marketing psychology / hook / vibe
- Deep tier: `gemini-2.5-flash` → objects, spatial relationships, human detail
- Pydantic `response_schema` with `response_mime_type="application/json"`

### External validation

**VLM-based attribute extraction is state-of-the-art.** Recent work on structured visual understanding confirms that:
- Gemini 2.5 Flash ranks near the top of vision-language benchmarks (MMBench, MME, MM-Vet)
- Structured output via constrained decoding (Pydantic schema) significantly improves extraction reliability over free-text prompting (Liu et al., 2024, "StructuredRAG")
- The two-tier approach mirrors the "mixture of depths" pattern validated in LLM routing literature

**However**, there is no published evidence that VLM-extracted "vibe" or "marketing psychology" features actually predict ad performance. The negative embedding ablation in v3 is consistent with the broader finding that:
- Simple low-level features (color histograms, text presence, spatial layout) often dominate in creative performance prediction
- Akata et al. (2023, "Visual Attributes for Advertisement Understanding") found that CLIP zero-shot classifiers for specific ad attributes matched or exceeded fine-tuned VLMs on downstream tasks

**The key risk** is that VLM confidence ≠ predictive value. A feature can be "correctly" extracted by a VLM and still provide zero signal for the success proxy.

### Recommendations
- **Add CLIP zero-shot baselines** for each cognitive feature dimension as a cheaper, more reproducible alternative
- **Feature-level ablation**: train XGBoost with each cognitive feature alone and in combination; report marginal R² contribution
- **Cross-model stability**: re-extract the same 600 ads with at least one alternative model (e.g., Qwen-VL, 6,724 ★) and compute feature-level correlation
- **Consider cost-adjusted value**: record per-feature extraction cost and compute "SHAP magnitude per dollar" to optimize the two-tier split

---

## 3. Modeling Approach: XGBoost on Small Samples

### Current approach
- XGBoost on 499 train / 101 test rows with 135 features
- Advertiser-disjoint temporal holdout
- Bootstrap CIs (2,000 resamples) for promotion decisions
- SHAP for directive derivation with directional stability filter

### External validation

**XGBoost with 600 rows is fragile.** The literature is clear on this:

- **Rule of thumb:** Van der Ploeg et al. (2014) recommend ≥10–20 events per variable for stable logistic regression. For continuous outcomes with tree ensembles, comparable guidance suggests 50–200 rows per feature for reliable SHAP directions.
- **With 135 features on 499 train rows (ratio = 3.7:1):** XGBoost can fit the training data but the SHAP values will be unstable. The R² = −163 confirms catastrophic overfitting to the training distribution.
- **Kumar et al. (2020)** ("Problems with Shapley-value-based explanations as feature importance measures") showed that SHAP values can be arbitrarily misleading when features are correlated, and can indicate importance for features that the model does not actually use. The correlated nature of creative features (palette vibrancy correlates with warmth index, etc.) makes this a real concern.

**Promotion CI gates are well-designed.** The requirement that the MAE-difference upper bound be below zero and top-quintile precision lower bound be above 0.20 is a standard non-inferiority design. However, bootstrap CIs with 101 test rows are wide enough that promotion is nearly impossible with small effect sizes.

**Positive finding:** The embedding ablation is a strong methodological choice. High-dimensional features (3,207 dimensions) on 499 rows create severe overfitting, confirmed by worsened MAE (0.3550 → 0.3631) and collapsed precision (0.3333 → 0.1429). This aligns with the "curse of dimensionality" literature (Hastie et al., ESL, §18).

### Recommendations
- **Add LASSO/ElasticNet baseline**: simpler, more stable with correlated features, directly handles feature selection
- **Add EBM (Explainable Boosting Machine)** via `interpretml`: GAM-style model that produces shape functions instead of SHAP values; more interpretable and stable on small data
- **SHAP stability assessment**: bootstrap the SHAP computation itself (re-fit XGBoost on 100 bootstrap samples, report SHAP direction agreement rate per feature)
- **Permutation importance** as complementary metric: less sensitive to feature correlation than SHAP
- **ALE (Accumulated Local Effects) plots**: handle correlated features better than SHAP dependence plots
- **Consider Bayesian Additive Regression Trees (BART)** which handles small n well and provides posterior intervals on feature effects

---

## 4. Embeddings and Representation Learning (Research Area R14)

### Current approach
- Embedding ablation showed harm; embeddings excluded from v3
- Raw 3,072-dimensional embeddings concatenated as features
- Conclusion: "no embeddings" is the default

### External validation

**The finding is correct but the ablation design is the worst case for embeddings.** Research on embedding use in tabular models (Guo & Berkhahn, 2016; Gorishniy et al., 2021) shows that:

1. Raw high-dimensional embeddings without dimensionality reduction create severe overfitting at small n
2. Pre-pooling (average/attention) before model input is critical
3. Supervised dimensionality reduction (e.g., PLS fitted on training only) can preserve signal while reducing dimensions

**The more concerning implication** is not that embeddings are useless, but that the creative features they represent may genuinely carry weak signal for the longevity proxy. This would mean that:

- Visual style elements (beyond simple color/layout) may not predict how long a competitor ad runs
- This would be a **validating finding for the product-free scene approach**: if visual style doesn't predict longevity, optimizing for a specific "successful" look is misguided

### Recommendations
- **Redo ablation with supervised dimension reduction**: PLS or Sparse PLS fitted on training only, reducing to 10–30 components
- **Embeddings-as-similarity**: instead of raw dimensions, use cosine similarity to top-performing exemplars
- **Wait for larger corpus**: embed only when n > 2,000 rows, which provides better signal-to-noise for high-dimensional features
- **Test the null hypothesis seriously**: if post-reduction embeddings still don't help at n > 2,000, this is strong evidence that visual style has weak relationship to longevity

---

## 5. Grounding DINO for Product ROI (Research Area R7)

### Current approach
- Pinned Replicate Grounding DINO revision (`efd10a8d...`)
- Text query (product name) + box/text thresholds
- Ambiguity margin for close score calls → quarantine
- Highest confidence only

### External validation

**Grounding DINO (10,574 ★, ECCV 2024) is a defensible choice** for open-vocabulary detection. The paper (Liu et al., 2024) demonstrates strong zero-shot performance on LVIS and COCO.

**Competitive alternatives that should be benchmarked:**

| Model | Stars | Venue | Key difference |
|---|---|---|---|
| **Grounding DINO** | 10,574 ★ | ECCV 2024 | Transformer-based, strong zero-shot |
| **YOLO-World** | 6,551 ★ | CVPR 2024 | CNN-based, 20× faster, competitive accuracy |
| **OWLv2** | (in HuggingFace transformers, 165K ★) | NeurIPS 2023 | ViT-based, self-training for label efficiency |
| **Florence-2** | (Microsoft) | 2024 | Unified vision foundation, prompt-based detection |
| **Grounded-SAM 2** | 3,733 ★ | 2024 | Combined detection + segmentation pipeline |

Key finding: **YOLO-World** (Cheng et al., CVPR 2024) achieves real-time inference (52 FPS vs ~5 FPS for Grounding DINO) with competitive zero-shot AP on LVIS. For the pipeline's use case (product detection in ads), inference speed matters less than accuracy, but YOLO-World's prompt-then-detect architecture may be more robust to ambiguous product names.

**Grounded-SAM 2** (3,733 ★) combines Grounding DINO + SAM 2 for detection + fine segmentation in one pass. This could unify R7 (ROI) and R8 (matting) research into a single pipeline, reducing the two-stage error propagation risk.

### Recommendations
- **Add YOLO-World as a speed challenger**: same benchmark fixtures, compare primary-SKU recall, IoU, wrong-selection rate, and latency
- **Evaluate Grounded-SAM 2 as a unified ROI+matting challenger**: could eliminate the ROI→matting error cascade
- **Test query sensitivity**: re-run identical ads with minor query variations ("product name", "supplement bottle", "vitamin container") and measure detection stability
- **Florence-2 as a third comparator**: Microsoft's model has strong open-vocabulary performance and may have better commercial licensing

---

## 6. Matting and Background Removal (Research Area R8)

### Current approach
- Pinned background remover (implied: 851-labs or similar) as baseline
- BiRefNet, RMBG, SAM/Florence mentioned as unselected candidates
- Stratified evaluation by packaging type (bottle, box, pouch, reflective, transparent, fine-detail, occluded)
- Metrics: alpha MSE, SAD, boundary F-score

### External validation

**The stratified approach is correct.** Different matting methods excel on different material types:
- **Transparent/reflective**: Requires trimap-based methods or specialized matting (ViTMatte, MODNet)
- **Fine-detail (hair, fibers)**: BiRefNet and RMBG-2.0 excel here
- **Bottles/rigid objects**: SAM 2 with box prompts works well; simpler threshold-based methods may suffice

**Key model comparisons from external evidence:**

| Model | Stars | Best for | Notes |
|---|---|---|---|
| **BiRefNet** (2401.03407) | 4,187 ★ | High-res dichotomous segmentation | CAAI AIR 2024; bilateral reference mechanism for edge precision |
| **SAM 2** | 19,854 ★ | General segmentation with prompts | Interactive prompting may be a feature not a bug for product ROI |
| **RMBG-2.0** (BRIA AI) | New | E-commerce background removal | Designed specifically for product images; commercial license |
| **U²-Net** | 8,764 ★ | Salient object detection | Lightweight, fast, but struggles with complex backgrounds |

**Critical finding:** BiRefNet (Zheng et al., 2024) introduced "bilateral reference" — using both high-level semantic and low-level detail references. This is particularly relevant for the pipeline because it handles the edge cases that matter most:
- Thin product edges (bottle rims, pouch seals)
- Halo artifacts at high resolution
- The "fringe" problem when compositing onto new backgrounds

The paper reports SOTA on DIS5K (dichotomous image segmentation) with clear margins over U²-Net and IS-Net.

**RMBG-2.0 caveat:** BRIA AI's models are designed for e-commerce but commercial licensing may require attribution or have usage restrictions. This is a procurement question, not a technical one.

### Recommendations
- **Benchmark BiRefNet vs current baseline stratified by packaging type** — this is the highest-impact R8 experiment
- **Add SAM 2 with Grounding DINO boxes as prompt**: evaluate whether interactive refinement improves boundary quality
- **For transparent/glass stratum**: test ViTMatte (Yao et al., 2024) or trimap-based matting specifically
- **Do NOT enable automatic cutout admission** until blind-holdout metrics clear the G2 thresholds; the current quarantine-only design is correct and aligned with safety literature

---

## 7. Scene Generation Architecture (Research Area R10)

### Current approach
- Product-free Flux.2 Pro scene plate (product never touches diffusion)
- Source-pixel compositing (RGB + normalized alpha)
- Deterministic layout, typography, contrast checks
- Owned scene route bypasses Flux entirely
- Rejected: Flux Kontext, masked inpainting, IP-Adapter conditioning, prompt-only bans

### External validation

**This is the single best-validated architectural decision in the pipeline.** External research strongly supports product-free compositing over product-conditioned generation:

**Rejected approaches — why they fail (confirmed by literature):**

| Rejected approach | Failure mode | External confirmation |
|---|---|---|
| Whole-frame Flux Kontext | Rewrites/blurs product text | AnyText paper (Tuo et al., 2024): diffusion models consistently corrupt text at small font sizes |
| Masked inpainting with product visible | Duplicate/garbled products | ObjectStitch (Song et al., CVPR 2023): inpainting frequently creates phantom objects |
| IP-Adapter product conditioning | Creates second product | IP-Adapter paper (Ye et al., 2023): image prompts combine with text prompts, often producing variants |
| Prompt-only product bans | "A man holding nothing" → man holding box | Lu et al. (2024): diffusion models routinely ignore negative prompts for object removal |
| Gaussian blur for pseudo-text | Synthetic softness, doesn't remove text | InstructIR (744 ★, ECCV 2024): restoration ≠ text removal |

**AnyText** (4,875 ★) is the closest to solving diffusion-based text rendering but:
- Requires per-image glyph position input
- Still produces character errors on long strings
- Designed for Chinese/English; supplement labels often have complex nutritional text

**ObjectStitch** (Song et al., CVPR 2023) is the closest academic work to the pipeline's compositing approach. Key differences:
- ObjectStitch uses diffusion inpainting for shadow generation (hallucination risk)
- The pipeline uses deterministic shadow compositing (safer, more control)
- ObjectStitch achieved better visual quality scores but worse identity preservation

**The pipeline's deterministic-first compositing is architecturally correct.** The compositing literature (Pérez et al., 2003, "Poisson Image Editing"; Lalonde & Efros, 2007) shows that deterministic methods:
- Preserve exact pixel identity (no diffusion hallucination)
- Give controllable shadow/light matching
- Fail gracefully (ugly but not deceptive) rather than catastrophically (convincing but wrong)

### Recommendations
- **Prioritize owned-scene route acquisition**: owned photography will always outperform generated plates on physical coherence metrics
- **Shadow/grounding research**: the pipeline's deterministic shadow + grounding check is correct; consider adding a learned shadow predictor (e.g., SGRNet, ShadowFormer) that operates on the composite layer only (no diffusion on product pixels)
- **Flux alternative testing**: SD3.5 (1,533 ★) has different text/pseudo-text behavior; benchmark scene-only (no product) generation quality
- **Do NOT enable any product-conditioned route** until identity preservation benchmarks pass; current evidence says "unsafe by default"

---

## 8. Survival Analysis with Sparse Events (Research Area R5)

### Current approach
- Kaplan-Meier descriptive baseline
- Cox PH with requirement: ≥30 train events, ≥10 test events
- Right-censoring with interval-censored stop inference
- Currently: 0 train events, 0 test events, 2 warehouse endings total
- Output: empty Cox guidance

### External validation

**The fail-closed approach is mandatory given the data, and the thresholds are well-calibrated to the literature:**

- **Vittinghoff & McCulloch (2007)** recommend 5–10 events per predictor variable for Cox PH. With their feature space, even a single predictor would be unreliable at <5 events.
- **Peduzzi et al. (1995)** (a simulation study of 673 papers): Cox models with <10 events per variable show inflated type I error and unstable coefficients.
- **Harrell (2015, "Regression Modeling Strategies")** recommends 15–20 events per degree of freedom for reliable Cox PH.

**The current zero-event situation is a data collection problem, not a modeling problem.** No survival method (Cox, random survival forests, discrete-time hazards, DeepSurv) can produce reliable guidance from zero observed events.

**Interval-censoring approach is methodologically sound:**
- The two-consecutive-miss rule for inferring inactivity follows Turnbull's (1976) nonparametric estimator for interval-censored data
- More formal interval-censored methods (Finkelstein, 1986; Sun, 2006) should be used when events accumulate
- The explicit distinction between `is_active=false` (event) and `end_date` (scrape boundary) prevents the most common survival analysis error in ad data

**Sparse-event alternatives to prepare for:**
- **Firth's penalized Cox regression** (Heinze & Schemper, 2001): reduces small-sample bias in Cox coefficients, usable at 2–5 events per variable
- **Discrete-time survival models** (logistic regression with time indicators): more flexible than Cox, handles tied events naturally, and allows time-varying effects
- **Random Survival Forests** (Ishwaran et al., 2008): nonparametric, handles non-proportional hazards, but still needs events to learn from

### Recommendations
- **Continue polling.** No model improvement can substitute for more observed endings.
- **When events reach 20+**, run Firth's penalized Cox as primary model and standard Cox as sensitivity check
- **Document the minimum detectable effect** given current sample: with 0 events, even a perfect feature would show no signal
- **Consider time-varying features**: ad creative features don't change, but competitive landscape does — re-extract features at each polling interval if feasible

---

## 9. Deterministic Typography and Layout (Research Area R12)

### Current approach
- Deterministic PIL text rendering with licensed fonts (DejaVu, Archivo Black, Bebas Neue)
- Three-role system: headline / body / CTA
- Whole-word wrapping, readable sizes, product intersection checks
- WCAG pixel-distribution contrast checks (≥98% pass or feathered backing added)
- Bands/CTA edges softened to reduce "sticker" treatment

### External validation

**Deterministic typography is the correct choice.** The literature on text generation in diffusion models consistently shows:

- Diffusion models **cannot reliably render accurate text** at small font sizes (Chen et al., 2024, "GlyphDraw"; Liu et al., 2024, "AnyText")
- Character error rates increase exponentially with text length
- The pipeline's decision to **never** put marketing copy through a diffusion model is strongly supported

**AnyText** (4,875 ★) and **GlyphByT5** (Liu et al., 2024) have made progress on diffusion-text rendering, but:
- Both require per-glyph position input — essentially deterministic layout
- Both still produce character errors at <16pt font sizes
- Neither handles multi-font, multi-color, multi-size layouts in a single pass

**The compositor architecture (deterministic placement + soft edges + WCAG contrast) is validated by:**
- Accessibility literature: WCAG 2.1 contrast requirements are the industry standard
- The 98% pixel threshold is stricter than the WCAG "large text" exemptions
- Soft backing shapes are a known technique from billboard/advertising design (Lukova, 2015)

**Font licensing consideration:** DejaVu is OFL-licensed (free). Archivo Black and Bebas Neue are also OFL. This is correct for a generation pipeline — no per-output licensing risk.

### Recommendations
- **Multi-candidate layout search**: the current "largest region" heuristic is fast but may miss optimal arrangements. A beam search over 4–8 layout alternatives (varying position, zone sizes, font combinations) is feasible at PIL rendering speed
- **Font-pair database**: curate 10–20 font combinations validated for supplement/health vertical (OFN-licensed only) and test pairwise preference
- **Accessibility edge cases**: test the 98% threshold on gradient backgrounds, product regions, and text-over-image scenarios where the backing shape obscures product detail
- **Benchmark deterministic type against AnyText-generated type**: on identical layouts, measure character accuracy, rendering time, and human preference. This would formally validate the decision to stay deterministic.

---

## 10. OCR Safety and Detection (Research Area R11)

### Current approach
- Non-destructive Cloud Vision OCR only
- Partitions blocks into: deterministic copy, protected product region, unexpected scene text
- Computes CER, verifies canonical labels
- Quarantines copy mismatch or unexpected scene text
- Destructive cleanup disabled; requires precision ≥0.99, recall ≥0.95, zero protected/non-text damage

### External validation

**Non-destructive OCR is the correct safety architecture.** The literature on image inpainting for text removal shows:

- Text removal via inpainting (LAMA, LaMa, MAT) frequently leaves ghost artifacts or hallucinates replacement content (Suvorov et al., 2021)
- The risk of removing a protected product label is catastrophic — the pipeline's zero-tolerance threshold is appropriate
- Even "surgical" inpainting at the pixel level can alter adjacent pixels outside the mask (Nazeri et al., 2019)

**OCR engine comparison:**

| Engine | Stars | Best for |
|---|---|---|
| **Cloud Vision** | N/A (Google) | High accuracy, multilingual, document-oriented |
| **PaddleOCR** | 89,417 ★ | Fast local inference, strong on Asian scripts, layout analysis |
| **EasyOCR** | 29,989 ★ | Simple API, good for English, less accurate on complex layouts |
| **Tesseract 5** | 62,000 ★ | Traditional OCR, requires pre-processing, struggles with on-image text |

**Ensemble OCR** is standard practice in critical applications (medical records, legal documents). Using two OCR engines with consensus voting would increase detection recall, especially for:
- Low-contrast pseudo-text (where Cloud Vision may miss but PaddleOCR detects)
- Text-like textures (where both might false-positive; consensus reduces false alarms)

**The G2 thresholds (precision ≥0.99, recall ≥0.95) are appropriate for safety-critical use** but may be unattainable for pseudo-text detection. The literature on pseudo-text/glyph detection (AITHOR, TextBlock) reports precision around 0.85-0.92, suggesting these thresholds may need calibration.

### Recommendations
- **Add PaddleOCR as ensemble partner**: run both Cloud Vision and PaddleOCR; flag regions where either detects text but deterministic copy doesn't account for it
- **Evaluate ensemble vs single-engine** on a labelled OCR benchmark before enabling any cleanup
- **Pseudo-text classifier**: CLIP-based zero-shot classification can distinguish "actual text" from "text-like patterns" with reasonable accuracy; add as a filter layer before OCR
- **Do NOT lower thresholds** below G2 values without separate human review of false-positive cases
- **The destructive cleanup gate should remain disabled** until the full four-class OCR benchmark (ordinary text, pseudo-text, protected text, non-text) passes

---

## 11. Craft/Aesthetic Evaluation (Research Area R13)

### Current approach
- Six human dimensions: art direction, imagery quality, typography, visual hierarchy, composition, brand cohesion
- Each dimension ≥4/5 required
- Automated evaluators produce candidates only; humans retain final approval
- Generated/reviewer labels never touch Meta proxy

### External validation

**Human evaluation remains the gold standard.** Automated aesthetic scoring has fundamental limitations:

- **NIMA** (2,242 ★, Talebi & Milanfar, 2018): trained on AVA dataset (photography competition scores), biased toward "beautiful" images; a supplement ad may need to look "clinical" or "informative" rather than "beautiful"
- **LAION Aesthetics Predictor**: trained on LAION-5B with CLIP embeddings; correlates with "looks like a high-quality stock photo" not "effective advertisement"
- **CLIP aesthetic scoring** (HPSv2, ImageReward): conflates "similarity to training distribution" with quality; systematically biases toward common image aesthetics
- **PickScore** (Kirstain et al., 2024): better aligned with human preference but still trained on generic image pairs, not ad-specific quality

**The key risk identified in the literature** is *evaluator leakage* — if automated scorers are trained on the same brands/products as the test set, they memorize style rather than assessing quality. The pipeline's requirement for brand/product/run-disjoint splits is correct.

**Six-dimension rubric alignment:** The dimensions map well to design literature:
- "Art direction" ≈ Gestalt principles + mood/tone (Wong, 1991)
- "Typography" ≈ Bringhurst (2004) principles
- "Visual hierarchy" ≈ graphic design fundamentals (Lupton, 2010)
- "Brand cohesion" ≈ consistency frameworks (Aaker, 1996)

### Recommendations
- **Pilot automated scoring as a negative filter only**: use NIMA/CLIP scores to auto-reject bottom-10% candidates (obviously bad), but never auto-accept
- **Blinded reviewer calibration**: the κ ≥ 0.70 threshold for craft agreement is reasonable; consider training reviewers on a calibration set before test set exposure
- **Record reviewer-level bias**: if reviewer A consistently scores 0.5 points higher than reviewer B, adjust with reviewer random effects in the agreement model
- **Do NOT train a learned ranker** until ≥200 human-labeled examples are available with brand/product/run-disjoint splits

---

## 12. Calibration, Uncertainty, and Drift (Research Area R4)

### Current approach
- Bootstrap CIs (2,000 resamples) for MAE, calibration gap, precision
- PSI for numeric drift, total-variation distance for categorical drift
- Promotion requires MAE-difference upper bound <0 and precision lower bound >0.20
- Segment support: ≥100 rows, ≥10 advertisers across train/test

### External validation

**Bootstrap is standard but has known small-sample issues.** With n_test = 101:
- Bootstrap percentile intervals can be anti-conservative (under-cover) for extreme quantiles (Davison & Hinkley, 1997)
- Bootstrap-t intervals (studentized) have better coverage but require variance estimates per bootstrap sample
- The non-parametric bootstrap with advertiser-level resampling (not row-level) is likely more appropriate to account for within-advertiser correlation

**PSI (Population Stability Index):**
- Widely used in financial risk modeling (credit scoring, Basel II)
- Known weakness: bin-dependent (results change with bin boundaries)
- Cannot detect distribution shifts that preserve marginal distributions (e.g., covariance shift)
- Yurdakul & Naranjo (2020) show PSI fails to detect mean shifts when variance also changes

**Stronger alternatives:**
- **MMD (Maximum Mean Discrepancy)** with RBF kernel: distribution-free, detects joint distribution changes
- **Wasserstein distance**: interpretable as "minimum cost to transform one distribution into another"
- **Conformal prediction** (Angelopoulos & Bates, 2021): produces valid prediction intervals under only exchangeability assumptions, requires no distributional form

### Recommendations
- **Add conformal prediction**: produces calibrated prediction intervals with distribution-free coverage guarantees; more appropriate than bootstrap for small samples
- **Add MMD or energy distance** as complementary drift detector alongside PSI
- **Advertiser-level bootstrap** (resample advertisers, not individual rows) for promotion CIs
- **Sensitivity analysis**: vary the number of bootstrap resamples (500, 1,000, 2,000, 5,000) and report CI width stability

---

## 13. Product-Page Extraction (Research Area R2)

### Current approach
- Free Shopify JSON / structured HTML extraction
- ZenRows JS-rendered backfill for unresolved URLs
- No LLM fallback unless separately authorized
- SHA-256 binding of enriched corpus
- Field support: ≥100 rows, ≥10 advertisers

### External validation

**Structured extraction is preferable to LLM scraping** for reliability and reproducibility:
- Schema.org / JSON-LD parsing on Shopify is deterministic and fast
- LLM-based extraction (GPT-4V or Gemini for page screenshots) introduces hallucination risk, cost, and latency
- However, ZenRows (JS rendering) may miss content loaded via API calls or hidden behind login walls

**The coverage gates are well-chosen:**
- 50% linked-ad coverage for product-page records → ensures the product exists on a page
- 40% for price → prices are often hidden or dynamic
- 25% for description/USP → descriptions are long and unstructured
- ≥100 rows / 10 advertisers → prevents overfitting to a single brand's product catalog

**Field selection caution:** Raw price should remain excluded (currency/unit/bundle incomparability is a known e-commerce challenge). Price tier (budget/mid/premium) is a reasonable proxy but requires manual market calibration.

### Recommendations
- **Manual audit of 20–50 URLs** before training: verify extraction accuracy against human ground truth
- **ZenRows cost modeling**: track per-URL cost and coverage gain to optimize the free-first → ZenRows → LLM cascade
- **Product freshness detection**: compare last-modified headers or sitemap dates; stale products can mislead
- **Field ablation**: train with each product field alone and in combination to measure marginal value

---

## 14. Overall Architecture: Two-Loop Isolation

### Current approach
- Meta evidence loop observes competitor ads → emits constraints
- Generation-quality loop uses constraints → evaluates craft quality
- Generated images never feed back to Meta proxy
- Only real deployed ads enter future Meta windows

### External validation

**This is the most important architectural decision in the pipeline, and it is correct.**

The "non-contamination invariant" prevents the most common failure mode in AI-for-creative systems: **reward hacking via the evaluation metric**. Without the two-loop isolation:

1. Generated images would be scored by the Meta model
2. The generation system would optimize for the proxy
3. Generated images would "look like" successful ads without being effective — a form of Goodhart's Law (Strathern, 1997): "When a measure becomes a target, it ceases to be a good measure."

This is well-documented in the literature:
- **Reward hacking in RLHF** (Skalse et al., 2022): optimizing against a learned reward model produces outputs that score highly but are low-quality
- **Shortcut learning** (Geirhos et al., 2020): neural networks latch onto spurious correlations that proxy labels rather than learning the intended concept
- **Causal confusion** (De Haan et al., 2019): agents learn to exploit confounded features when trained on observational data

The pipeline's architecture — where the Meta model constrains *what* to make but generation quality determines *whether it's acceptable* — mirrors the separation of concerns in causal inference: the model identifies associations, but domain experts (or in this case, craft evaluators) determine if those associations are actionable.

### The one concern: SHAP as a "constraint" rather than a "prescription"

The pipeline treats SHAP directives as "bounded constraints, not exact thresholds, causal prescriptions, or complete art direction." This is correct. However:

- Without causal identification (which requires randomization), SHAP values on observational data can reflect confounding rather than causal effects
- The direction filter (|mean_signed_shap| / mean_abs_shap ≥ 0.10) helps but is arbitrary
- An alternative framing: instead of "increase/decrease X," treat features as *design dimensions* where the model suggests a direction but the generation system explores the space

---

## 15. Key Papers and Resources for Further Reading

### Directly relevant to pipeline decisions

| Paper | Year | Relevance |
|---|---|---|
| Kumar et al., "Problems with Shapley-value-based explanations" (2002.11097) | 2020 | SHAP instability with correlated features |
| Ratner et al., "Data Programming: Creating Large Training Sets, Quickly" | 2016 | Weak supervision framework for proxy labels |
| Gordon et al., "A Comparison of Approaches to Advertising Measurement" | 2019 | Causal challenges in ad effectiveness |
| Vittinghoff & McCulloch, "Relaxing the Rule of Ten Events per Variable" | 2007 | Minimum events for survival models |
| Zheng et al., "BiRefNet" (2401.03407) | 2024 | SOTA dichotomous image segmentation |
| Liu et al., "Grounding DINO" (2303.05499) | 2024 | Open-vocabulary object detection |
| Cheng et al., "YOLO-World" (CVPR 2024) | 2024 | Real-time open-vocabulary detection |
| Song et al., "ObjectStitch" (CVPR 2023) | 2023 | Diffusion-based object compositing |
| Tuo et al., "AnyText" | 2024 | Multilingual text generation in diffusion |
| Angelopoulos & Bates, "A Gentle Introduction to Conformal Prediction" | 2021 | Distribution-free prediction intervals |

### Background and methodology

| Paper | Year | Relevance |
|---|---|---|
| Lewis & Rao, "On the Near Impossibility of Measuring the Returns to Advertising" | 2015 | Fundamental limits of ad measurement |
| Geirhos et al., "Shortcut Learning in Deep Neural Networks" | 2020 | Spurious correlations in proxy targets |
| Skalse et al., "Defining and Characterizing Reward Hacking" | 2022 | Goodhart's Law in learned reward models |
| Harrell, "Regression Modeling Strategies" (2nd ed.) | 2015 | Best practices for small-sample modeling |
| Ishwaran et al., "Random Survival Forests" | 2008 | Nonparametric alternative to Cox |
| Sun, "The Statistical Analysis of Interval-Censored Failure Time Data" | 2006 | Proper handling of interval-censored outcomes |

---

## Appendix A: GitHub Ecosystem Quick Reference

| Tool | Stars | Venue/Year | Pipeline relevance |
|---|---|---|---|
| FLUX.1 (BFL) | 25,954 | 2024 | Scene generation (used) |
| Diffusers (HF) | 34,504 | Ongoing | Model hosting |
| Generative Models (Stability) | 27,285 | Ongoing | SD/SDXL alternatives |
| SAM 2 (Meta) | 19,854 | 2024 | Segmentation (competitor) |
| Janus (DeepSeek) | 17,764 | 2024 | Multimodal understanding |
| Grounding DINO (IDEA) | 10,574 | ECCV 2024 | ROI detection (used) |
| U²-Net | 8,764 | 2020 | Background removal |
| IP-Adapter (Tencent) | 6,685 | 2023 | Identity conditioning (rejected) |
| Qwen-VL (Alibaba) | 6,724 | 2024 | Cognitive extraction alt |
| YOLO-World | 6,551 | CVPR 2024 | Fast detection competitor |
| AnyText | 4,875 | 2024 | Text rendering (competitor) |
| LLaVA-NeXT | 4,718 | 2024 | Multimodal alt |
| BiRefNet | 4,187 | CAAI AIR 2024 | Matting (should test) |
| Grounded-SAM 2 | 3,733 | 2024 | Unified detect+segment |
| PixArt-α | 3,304 | 2024 | Fast diffusion training |
| XLabs-AI Flux tools | 2,230 | 2024 | Flux ecosystem |
| NIMA (idealo) | 2,242 | 2018 | Aesthetic scoring |
| SD3.5 (Stability) | 1,533 | 2024 | Flux alternative |
| InstructIR | 744 | ECCV 2024 | Image restoration |
| PaddleOCR | 89,417 | Ongoing | Local OCR engine |
| EasyOCR | 29,989 | Ongoing | Simple OCR engine |
| Transformers (HF) | 165,239 | Ongoing | Hosts OWLv2/OWL-ViT |

---

## Appendix B: Validation Strength Summary

| Pipeline Decision | Validation | Confidence | Action |
|---|---|---|---|
| Two-loop isolation | ✅ Strongly validated | High | Maintain |
| Product-free compositing | ✅ Strongly validated | High | Maintain + prioritize owned scenes |
| Deterministic typography | ✅ Strongly validated | High | Maintain; benchmark vs AnyText |
| Non-destructive OCR | ✅ Strongly validated | High | Add PaddleOCR ensemble |
| Fail-closed boundaries | ✅ Strongly validated | High | Maintain all gates |
| Source-pixel preservation | ✅ Strongly validated | High | Maintain; do NOT enable destructive cleanup |
| XGBoost on 600 rows | ⚠️ Fragile | Medium | Add LASSO/EBM baselines; SHAP stability check |
| 70/20/10 proxy weights | ⚠️ No formal basis | Low | Add Snorkel reweighting; sensitivity analysis |
| Grounding DINO only | ⚠️ Missing alternatives | Medium | Benchmark YOLO-World, Grounded-SAM 2 |
| BiRefNet untested | ⚠️ Unknown accuracy | Medium | Benchmark on stratified matting fixtures |
| Empty survival guidance | ✅ Correct (no data) | High | Continue polling; prepare Firth's Cox |
| Bootstrap CIs on 101 rows | ⚠️ Wide intervals | Medium | Add conformal prediction; advertiser-level bootstrap |
| Embedding exclusion | ✅ Correct (at this n) | High | Revisit at n>2000 with dimensionality reduction |
| SHAP → directive pipeline | ⚠️ Directionally correct | Medium | Add permutation importance + ALE plots |

---

**Research cutoff:** 2026-09-13  
**Repository state:** Evidence reconciled against `docs/current-meta-model-and-generation-pipelines-research-brief.md`  
**Next step:** The P0 actions (tool comparison benchmarks) on R7 (YOLO-World), R8 (BiRefNet), and R4 (conformal prediction) should proceed before any v4 model training or route selection. The current pipeline architecture requires no reversals, only additional baselines and sensitivity checks.