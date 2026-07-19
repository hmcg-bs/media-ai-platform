# Social Media Analysis Agent — Master Architecture Plan

> **Core Philosophy:** Bypass AI hallucinations and enterprise-grade costs by combining deterministic code, statistical mathematics, and targeted generative AI. Every decision the system makes is grounded in mathematical evidence, not LLM guesswork.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                    GOOGLE CLOUD PLATFORM                             │
│                                                                      │
│  [Meta Marketing API]    [Meta Ad Library]                           │
│         │                      │                                     │
│         ▼                      ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  STEP 1: DUAL-DATA INGESTION                                │    │
│  │  Cloud Functions + Apify + Cloud Scheduler                  │    │
│  │  BigQuery: own_ads_performance + competitor_ads_raw         │    │
│  └──────────────────────────┬──────────────────────────────────┘    │
│                             │ GCS event trigger                      │
│                             ▼                                        │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  STEP 2: ENSEMBLE EXTRACTION                                │    │
│  │  Cloud Run + Cloud Workflows + Vertex AI                    │    │
│  │  OpenCV · Cloud Vision · Gemini Flash-Lite · Gemma 4        │    │
│  │  BigQuery: ads_extraction_results                           │    │
│  └──────────────────────────┬──────────────────────────────────┘    │
│                             │ n >= 50 new rows                       │
│                             ▼                                        │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  STEP 3: PATTERN DISCOVERY                                  │    │
│  │  BigQuery ML (XGBoost) — no LLM                             │    │
│  │  BigQuery: pattern_discovery_results                        │    │
│  └──────────────────────────┬──────────────────────────────────┘    │
│                             │ new patterns available                 │
│                             ▼                                        │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  STEP 4: SUPERVISED FINE-TUNING                             │    │
│  │  GCS JSONL Dataset + Vertex AI SFT (LoRA on Gemma 3)        │    │
│  │  Vertex AI Model Registry                                   │    │
│  └──────────────────────────┬──────────────────────────────────┘    │
│                             │ new model deployed                     │
│                             ▼                                        │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  STEP 5: STAKEHOLDER UI                                     │    │
│  │  Streamlit on Cloud Run + Google IAP                        │    │
│  │  Queries SFT Endpoint + BigQuery                            │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Infrastructure Stack

| Layer | Service | Purpose |
|---|---|---|
| **Data Warehouse** | BigQuery + BigQuery ML | Storage, SQL analytics, XGBoost training |
| **Object Storage** | Google Cloud Storage (GCS) | Raw images, JSONL datasets, DLQ files |
| **Orchestration** | Cloud Workflows + Cloud Scheduler | Event-driven pipeline chaining + cron |
| **Compute** | Cloud Run + Cloud Functions | Stateless containers, event handlers |
| **AI — Extraction** | Vertex AI (Gemini Flash-Lite, Gemma 4) | Cognitive creative analysis |
| **AI — Training** | Vertex AI SFT + LoRA | Fine-tuning Gemma 3 on performance patterns |
| **AI — Inference** | Vertex AI Endpoint | Serving the fine-tuned model |
| **Vision** | Cloud Vision API | Deterministic OCR |
| **Secrets** | Google Secret Manager | API keys, tokens |
| **Monitoring** | Cloud Logging + Cloud Monitoring | Structured logs, alerts, cost tracking |
| **Auth** | Google Identity-Aware Proxy | Stakeholder UI access control |
| **Scraping** | Apify | Competitor Meta Ad Library scraper |
| **Frontend** | Streamlit (Docker on Cloud Run) | Stakeholder-facing dashboard |

---

## Step-by-Step Pipeline

---

### Step 1 — Dual-Data Ingestion

📄 **[Full Engineering Blueprint →](./step1_dual_data_ingestion.md)**

**What it does:** Collects two fundamentally different but complementary datasets — your own ads with exact ROAS metrics from the Meta Marketing API, and competitor ads with `days_active` as a performance proxy from the Meta Ad Library via Apify.

**Why both are needed:**
- Own data gives you exact truth (ROAS, CTR, spend) but only covers your account
- Competitor data gives you market-wide signal — an ad running 60+ days without being paused is statistically profitable

**Key outputs:**
- `BigQuery: own_ads_performance` — exact performance metrics per creative
- `BigQuery: competitor_ads_raw` — competitor creative inventory with proxy metrics
- `GCS: raw-creatives/{own|competitor}/{ad_id}.jpg` — source images for Step 2
- `BigQuery: ads_master_view` — unified view normalising both datasets

**Trigger:** Cloud Scheduler (daily cron)

---

### Step 2 — Ensemble Extraction

📄 **[Full Engineering Blueprint →](./step2_ensemble_extraction_pipeline.md)**
📄 **[Claude Code Build Prompt →](./claude_code_prompt.md)**

**What it does:** Converts raw ad images into structured, machine-readable JSON by combining three extraction strategies in parallel — never relying on a single expensive LLM for everything.

| Strategy | Tool | Properties Extracted | Cost |
|---|---|---|---|
| **Deterministic** | Python + OpenCV + Cloud Vision | Dimensions, HEX palette, OCR text, typography hierarchy | ~Free |
| **Structured AI** | Gemini Flash-Lite (Vertex AI) | Hook framework, emotional lever, brand vibe | Very Low |
| **Deep Reasoning AI** | Gemma 4 (Vertex AI) | Product identification, spatial relationships, human micro-details, product verification | Low |

**The critical design principle:** AI is only ever used for features it genuinely requires cognitive understanding for. Color math, font size ratios, and image dimensions are never sent to an LLM.

**Key output per ad:**
```
BigQuery: ads_extraction_results
{
  ad_id, technical_metadata, color_profile, typography_hierarchy,
  product_verification, spatial_and_nested_objects,
  human_model_analysis, marketing_psychology
}
```

**Trigger:** GCS object-created event (new image in `raw-creatives/`)

---

### Step 3 — Pattern Discovery

📄 **[Full Engineering Blueprint →](./step3_pattern_discovery.md)**

**What it does:** Uses BigQuery ML XGBoost — not a generative AI — to mathematically calculate which creative features are statistically correlated with performance. This is the core intellectual engine of the system.

**Why XGBoost, not an LLM:**
- Feature importance scores are mathematically derived, reproducible, and auditable
- An LLM asked "what makes ads perform well?" would produce plausible-sounding hallucinations — XGBoost produces evidence

**The output it generates:**
- A ranked feature importance table (e.g., `hook_framework` is the #1 predictor of ROAS)
- A segment correlation table showing the specific *combinations* that outperform (e.g., `PAS hook + Studio background + human model → avg 4.2x ROAS`)

**Key output:**
```
BigQuery: pattern_discovery_results
{
  pattern_id, hook_framework, background_style, human_presence,
  avg_performance, sharpe_ratio, sample_size, metric_type
}
```

**Trigger:** Cloud Workflow step fires when ≥ 50 new extraction results land in BigQuery

---

### Step 4 — Supervised Fine-Tuning

📄 **[Full Engineering Blueprint →](./step4_supervised_fine_tuning.md)**

**What it does:** Takes the mathematically validated patterns from Step 3 and uses them as training data to teach a lightweight Gemma 3 model to output structured creative briefs on demand. The model learns to *associate product categories with their proven visual formulas*.

**Why fine-tune instead of prompting:**
- A prompted general model retrieves from its training distribution — it has no awareness of your specific market data
- A fine-tuned model has the performance patterns *baked into its weights* via LoRA adapters
- Fine-tuning is a one-time cost; inference is fast and cheap

**The training data pipeline:**
```
BigQuery SQL (pattern_discovery_results)
    → JSONL prompt-response pairs
    → GCS: sft-datasets/{version}/train.jsonl
    → Vertex AI SFT job (LoRA, 3 epochs)
    → Vertex AI Model Registry
    → Vertex AI Endpoint (staging → production gate)
```

**Retraining trigger:** Cloud Workflow fires when ≥ 50 new patterns are in `pattern_discovery_results` and no SFT job is currently running

---

### Step 5 — Stakeholder UI

📄 **[Full Engineering Blueprint →](./step5_presentation_ui.md)**

**What it does:** Surfaces the fine-tuned model and pattern data through a Streamlit dashboard on Cloud Run. Media buyers and creative directors get structured, data-backed creative briefs in plain English — without needing database access.

**Three pages:**
1. **Brief Generator** — Chat-style input (category + platform + audience) → structured creative brief from the SFT endpoint
2. **Pattern Explorer** — Visual BigQuery dashboard showing feature importance rankings and top-performing combinations
3. **Ad Library** — Browsable view of own + competitor ad inventory with performance metrics

**Deployment:** Docker container on Cloud Run with scale-to-zero (costs nothing when idle) and Google IAP for authentication

---

## Data Flow Summary

```
Meta APIs + Apify
    │
    ▼
[BigQuery: raw performance + competitor data]     ← Step 1
    │
    ▼ (GCS event trigger per image)
[BigQuery: structured extraction per ad]          ← Step 2
    │
    ▼ (batch trigger: n >= 50 new rows)
[BigQuery: pattern_discovery_results]             ← Step 3
    │
    ▼ (batch trigger: n >= 50 new patterns)
[Vertex AI: fine-tuned SFT endpoint]              ← Step 4
    │
    ▼ (on-demand query)
[Streamlit UI on Cloud Run]                       ← Step 5
```

---

## GCS Bucket Structure

```
gs://{project}-social-agent/
├── raw-creatives/
│   ├── own/{ad_id}.jpg
│   └── competitor/{brand_name}/{ad_id}.jpg
├── extraction-cache/
│   └── landing-pages/{ad_id}.jpg          (temp, 24hr TTL)
├── sft-datasets/
│   ├── v1.0/train.jsonl
│   ├── v1.1/train.jsonl
│   └── eval/eval_prompts.jsonl
├── models/
│   └── sft/{version}/adapter_weights/
└── dlq/
    ├── step1/{timestamp}_{ad_id}.json
    ├── step2/{timestamp}_{ad_id}.jpg
    └── step3/{timestamp}_failed_query.sql
```

---

## BigQuery Dataset Structure

```
{project}.social_agent_ds/
├── own_ads_performance              (Step 1 write)
├── competitor_ads_raw               (Step 1 write)
├── ads_master_view                  (Step 1 view — joins both tables)
├── ads_extraction_results           (Step 2 write)
├── ml_training_flat                 (Step 3 view — flattens Step 2 JSON)
├── pattern_discovery_results        (Step 3 write)
├── creative_roas_predictor          (Step 3 BQML model)
└── creative_longevity_predictor     (Step 3 BQML model)
```

---

## Cloud Workflows Orchestration

The full pipeline is coordinated by a single **Cloud Workflow** definition that chains all steps, handles errors, and routes to DLQ as needed.

```yaml
# High-level workflow pseudocode
main:
  steps:
    - trigger_step1_own_ads:
        call: http.post
        args:
          url: ${META_CLOUD_FUNCTION_URL}
        result: step1_own_result

    - trigger_step1_competitor:
        call: http.post
        args:
          url: ${APIFY_TRIGGER_FUNCTION_URL}
        result: step1_competitor_result

    # Step 2 is triggered per-image by GCS events — not inline here

    - check_extraction_count:
        call: bigquery.query
        args:
          query: "SELECT COUNT(*) as n FROM ads_extraction_results WHERE DATE(extracted_at) = CURRENT_DATE()"
        result: extraction_count

    - conditional_step3:
        switch:
          - condition: ${extraction_count.rows[0].n >= 50}
            next: trigger_step3
          - condition: true
            next: end

    - trigger_step3:
        call: http.post
        args:
          url: ${BQML_RETRAIN_FUNCTION_URL}
        result: step3_result

    - conditional_step4:
        switch:
          - condition: ${step3_result.new_patterns >= 50}
            next: trigger_step4
          - condition: true
            next: end

    - trigger_step4:
        call: http.post
        args:
          url: ${SFT_TRIGGER_FUNCTION_URL}
        result: step4_result

    - end:
        return: "Pipeline run complete"
```

---

## Operational Guardrails Summary

| Risk | Mitigation |
|---|---|
| Scraper blocks / timeouts | 10s timeout → null verification flag → pipeline continues |
| Vertex AI cost overrun | Cloud Quotas hard limit: 5,000 extractions/day |
| Infinite scraper loop billing | Apify monthly compute unit budget cap |
| Corrupted image files | DLQ → `gs://dlq/step2/` → Cloud Logging error |
| BigQuery write failure | 3-retry exponential backoff → DLQ JSONL fallback |
| SFT model regression | Staging endpoint evaluation gate before production promotion |
| Stale patterns served to UI | Freshness check: `MAX(computed_at)` must be < 48 hours |
| API schema changes (Meta) | `dict.get()` safe defaults + `WARNING` log on missing fields |
| Solo developer downtime | All steps are stateless and independently re-runnable |

---

## Development Build Order

Build and validate each step independently before connecting them. Each step has a defined output that the next step treats as a stable contract.

- [ ] **Step 1** — Meta API Cloud Function + Apify webhook + BigQuery schemas + dedup MERGE
- [ ] **Step 2** — Full extraction pipeline (see [claude_code_prompt.md](./claude_code_prompt.md))
- [ ] **Step 3** — BQML training SQL + feature importance queries + pattern results table
- [ ] **Step 4** — JSONL dataset builder + SFT job trigger + eval gate + model registry
- [ ] **Step 5** — Streamlit app + Cloud Run Dockerfile + IAP configuration
- [ ] **Integration** — Cloud Workflow definition wiring all steps + end-to-end test run

---

## Cost Model (Estimated Monthly, Solo Developer Scale)

| Component | Estimated Monthly Cost |
|---|---|
| BigQuery storage + queries | $5–20 |
| Cloud Vision API (OCR) | $1.50 per 1,000 images |
| Gemini Flash-Lite (Vertex AI) | ~$0.01 per 1,000 images |
| Gemma 4 (Vertex AI) | ~$0.05 per 1,000 images |
| Cloud Run (Streamlit) | $0 (scale to zero) |
| Cloud Functions | $0 (free tier covers typical volume) |
| GCS storage | $0.02 per GB |
| Apify scraper | $5–49/month (plan dependent) |
| Vertex AI SFT job | $5–20 per training run |
| **Total at 1,000 ads/day** | **~$50–100/month** |
