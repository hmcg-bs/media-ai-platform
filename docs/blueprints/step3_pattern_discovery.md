# Step 3: Pattern Discovery — Engineering Blueprint

> **Goal:** Use pure mathematics — no generative AI — to identify which creative features are statistically correlated with ad performance. The output is a ranked, evidence-based feature importance table that grounds all future creative decisions in data.

---

## 1. Architecture Overview

```
[BigQuery: ads_extraction_results]   (output of Step 2)
              │
              ▼
[BigQuery ML: XGBoost Training Job]
              │
              ├──► [Feature Importance Table]
              │        (ranked creative signals)
              │
              └──► [Segment Correlation Queries]
                       (category × feature × performance)
                                │
                                ▼
              [BigQuery: pattern_discovery_results]
                   (input to Step 4 SFT dataset builder)
```

---

## 2. Pre-Training Data Preparation

Before running the ML model, the raw extraction output from Step 2 must be flattened into a single, wide analytics table. Nested JSON arrays are not directly usable by BigQuery ML.

### 2.1 Flatten the Step 2 Output

```sql
-- BigQuery View: ml_training_flat
CREATE OR REPLACE VIEW `{project}.{dataset}.ml_training_flat` AS
SELECT
    e.ad_id,
    e.technical_metadata.width,
    e.technical_metadata.height,
    e.technical_metadata.aspect_ratio,
    e.color_profile.background_hex,
    e.color_profile.background_style,
    e.color_profile.contrast_ratio_type,
    e.typography_hierarchy.primary_headline.canvas_coverage_percentage       AS headline_coverage_pct,
    e.typography_hierarchy.headline_to_subtext_scale_ratio,
    ARRAY_LENGTH(e.typography_hierarchy.secondary_copy)                      AS secondary_copy_block_count,
    e.human_model_analysis.human_presence,
    e.human_model_analysis.model_count,
    e.marketing_psychology.hook_framework,
    e.marketing_psychology.emoji_count,
    e.marketing_psychology.reading_grade_level,
    ARRAY_LENGTH(e.marketing_psychology.authority_flags)                     AS authority_flag_count,
    e.spatial_and_nested_objects.texture_demonstration.visible               AS texture_visible,
    e.spatial_and_nested_objects.texture_demonstration.texture_type,
    e.product_verification.is_visually_verified_match,
    m.performance_metric,
    m.metric_type,
    m.data_source
FROM `{project}.{dataset}.ads_extraction_results` e
JOIN `{project}.{dataset}.ads_master_view` m USING (ad_id)
WHERE e.ad_id IS NOT NULL
  AND m.performance_metric IS NOT NULL;
```

### 2.2 Normalise Categorical Features

BigQuery ML handles `STRING` columns for classification automatically via one-hot encoding, but you should standardise the enum values to avoid fragmentation:

```sql
-- Normalise hook_framework values before training
UPDATE `{project}.{dataset}.ml_training_flat`
SET hook_framework = UPPER(TRIM(hook_framework))
WHERE hook_framework IS NOT NULL;
```

---

## 3. BigQuery ML Model Training

### 3.1 Training for Own Ads (ROAS as Label)

For your own ads where exact ROAS is available, train a regression model to predict ROAS from creative features.

```sql
CREATE OR REPLACE MODEL `{project}.{dataset}.creative_roas_predictor`
OPTIONS (
    model_type              = 'BOOSTED_TREE_REGRESSOR',
    input_label_cols        = ['performance_metric'],
    booster_type            = 'GBTREE',
    num_parallel_tree       = 4,
    max_iterations          = 50,
    learn_rate              = 0.1,
    subsample               = 0.8,
    colsample_bytree        = 0.8,
    early_stop              = TRUE,
    min_rel_progress        = 0.01,
    data_split_method       = 'AUTO_SPLIT'
) AS
SELECT * EXCEPT (ad_id, data_source, metric_type)
FROM `{project}.{dataset}.ml_training_flat`
WHERE data_source = 'own'
  AND metric_type = 'roas';
```

### 3.2 Training for Competitor Ads (Days Active as Label)

For competitor ads, train a separate model using `days_active` as the proxy metric.

```sql
CREATE OR REPLACE MODEL `{project}.{dataset}.creative_longevity_predictor`
OPTIONS (
    model_type              = 'BOOSTED_TREE_REGRESSOR',
    input_label_cols        = ['performance_metric'],
    num_parallel_tree       = 4,
    max_iterations          = 50,
    data_split_method       = 'AUTO_SPLIT'
) AS
SELECT * EXCEPT (ad_id, data_source, metric_type)
FROM `{project}.{dataset}.ml_training_flat`
WHERE data_source = 'competitor'
  AND metric_type = 'days_active';
```

---

## 4. Feature Importance Extraction

After training, extract the ranked feature weights from the model's internal boosted tree structure.

```sql
-- Extract feature importance from own ads model
SELECT
    feature,
    importance_weight,
    importance_gain,
    importance_cover,
    RANK() OVER (ORDER BY importance_gain DESC) AS importance_rank
FROM ML.FEATURE_IMPORTANCE(MODEL `{project}.{dataset}.creative_roas_predictor`)
ORDER BY importance_rank ASC;
```

**Expected output shape:**

| feature | importance_weight | importance_gain | importance_cover | importance_rank |
|---|---|---|---|---|
| hook_framework | 0.31 | 0.42 | 0.28 | 1 |
| background_style | 0.22 | 0.31 | 0.19 | 2 |
| human_presence | 0.18 | 0.27 | 0.24 | 3 |
| headline_coverage_pct | 0.14 | 0.21 | 0.17 | 4 |
| emoji_count | 0.08 | 0.12 | 0.09 | 5 |

---

## 5. Segment Correlation Queries

Feature importance gives you the global ranking. Segment queries give you the *specific winning combinations* that power the SFT dataset in Step 4.

### 5.1 Hook Framework × Performance

```sql
-- Which hook frameworks drive the highest average ROAS?
SELECT
    hook_framework,
    COUNT(*)                        AS ad_count,
    ROUND(AVG(performance_metric), 2)   AS avg_roas,
    ROUND(STDDEV(performance_metric), 2) AS roas_stddev,
    ROUND(MAX(performance_metric), 2)   AS max_roas
FROM `{project}.{dataset}.ml_training_flat`
WHERE data_source = 'own'
GROUP BY hook_framework
HAVING ad_count >= 10
ORDER BY avg_roas DESC;
```

### 5.2 Feature Combination Correlation

```sql
-- Which background_style + hook_framework combos perform best?
SELECT
    background_style,
    hook_framework,
    human_presence,
    COUNT(*)                            AS sample_size,
    ROUND(AVG(performance_metric), 2)   AS avg_roas,
    ROUND(AVG(performance_metric) / NULLIF(STDDEV(performance_metric), 0), 2) AS sharpe_ratio
FROM `{project}.{dataset}.ml_training_flat`
WHERE data_source = 'own'
  AND performance_metric IS NOT NULL
GROUP BY background_style, hook_framework, human_presence
HAVING sample_size >= 5
ORDER BY sharpe_ratio DESC
LIMIT 20;
```

> Use `sharpe_ratio` (mean/stddev) rather than raw average to surface *consistently* high-performing combinations, not flukes.

---

## 6. Pattern Discovery Results Table

Write the final segment analysis output into a permanent BigQuery table for consumption by Step 4.

```sql
CREATE OR REPLACE TABLE `{project}.{dataset}.pattern_discovery_results` AS
SELECT
    GENERATE_UUID()                     AS pattern_id,
    background_style,
    hook_framework,
    human_presence,
    texture_visible,
    contrast_ratio_type,
    COUNT(*)                            AS sample_size,
    ROUND(AVG(performance_metric), 4)   AS avg_performance,
    ROUND(STDDEV(performance_metric), 4) AS performance_stddev,
    ROUND(AVG(performance_metric) / NULLIF(STDDEV(performance_metric), 0), 4) AS sharpe_ratio,
    metric_type,
    data_source,
    CURRENT_TIMESTAMP()                 AS computed_at
FROM `{project}.{dataset}.ml_training_flat`
GROUP BY
    background_style, hook_framework, human_presence,
    texture_visible, contrast_ratio_type, metric_type, data_source
HAVING sample_size >= 5
ORDER BY sharpe_ratio DESC;
```

---

## 7. Operational Guardrails

### 7.1 Minimum Sample Size Gate
Never surface a pattern with fewer than 5 data points — statistical noise masquerading as signal is the most dangerous failure mode here. The `HAVING sample_size >= 5` clause is non-negotiable.

### 7.2 Model Retraining Schedule
Trigger a full model retrain via **Cloud Scheduler** whenever a new batch of Step 2 extractions lands. Use a Cloud Workflow step that checks:
```python
if new_extraction_count >= 50:
    trigger_bqml_retrain()
```
Do not retrain on every single new ad — BQML training jobs have a minimum cost per run.

### 7.3 Model Evaluation Logging
After every retrain, log the evaluation metrics to Cloud Logging and BigQuery:
```sql
SELECT * FROM ML.EVALUATE(MODEL `{project}.{dataset}.creative_roas_predictor`);
```
Alert (via Cloud Monitoring) if `mean_absolute_error` increases by more than 15% compared to the previous run.

### 7.4 Data Freshness Check
Before any downstream consumer (Step 4 or Step 5) queries `pattern_discovery_results`, assert that `MAX(computed_at)` is less than 48 hours old. If stale, trigger a recompute and return a holding state rather than serving stale patterns.
