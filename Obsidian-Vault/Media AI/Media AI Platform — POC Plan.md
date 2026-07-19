# Media AI Platform — Proof of Concept Plan

## What We're Building

A platform that ingests top-performing Meta ads, uses AI to break down what makes them work (hooks, copy, format, tone, visuals), stores that intelligence in a vector database, and lets users search by industry to get actionable copywriting and creative suggestions.

**POC Scope:** Meta ads only. Single user. No auth. Core loop proven end-to-end.

---

## System Architecture Overview

A serverless Google Cloud Platform (GCP) architecture combining exact performance metrics with statistical pattern discovery and fine-tuned AI generation:

### Step 1: Dual-Data Ingestion (The Foundation)

Combining exact metrics with proxy metrics for a complete market picture:

```
Internal Data Path:
├─ Cloud Function → Meta Marketing API (exact ROAS, CTR, Spend)
└─ Writes to BigQuery (single source of truth)

Competitor Data Path:
├─ Scheduled Web Scraper (Apify) → Meta Ad Library
├─ Images → Google Cloud Storage (GCS)
└─ Ad metadata + "Days Active" (performance proxy) → BigQuery
```

### Step 2: Ensemble Extraction (The Explorer)

Processing raw images into structured data without expensive single-model LLM calls:

```
Deterministic Code (100% Accurate, Zero Cost):
├─ K-Means clustering → extract exact HEX color palettes
├─ File metadata → image dimensions (1:1 vs 9:16)
└─ Cloud Vision API → extract on-screen text

Parallel AI (Fast & Focused):
├─ Gemini Flash-Lite via Vertex AI → extract subjective data
│  (hook type, vibe, human face presence)
└─ Strict JSON Schema enforcement → valid, parseable output

Output → Structured JSON to GCS + BigQuery
```

### Step 3: Pattern Discovery (The Mathematician)

Statistical algorithms find proven formulas (no AI in this step):

```
BigQuery ML + XGBoost:
├─ Analyze structured JSON data vs. "Days Active" performance
├─ Calculate Feature Importance mathematically
└─ Output: Proven formulas
   (e.g., "PAS hook + high negative space → 85% success in cosmetics")
```

### Step 4: Supervised Fine-Tuning (The Generator)

Teaching a specialized model to replicate winning formulas:

```
Dataset Creation:
├─ SQL query → export mathematically proven Q&A pairs to JSONL
└─ Example:
   Prompt: "Make a toothpaste ad."
   Response: "Use Clinical Blue + PAS hook."

Training:
├─ Upload JSONL to Vertex AI
├─ Run Supervised Fine-Tuning (SFT) with LoRA
└─ Deploy lightweight, task-specific model endpoint
```

### Step 5: Presentation & Stakeholder UI (The Agent)

```
Streamlit Dashboard on Google Cloud Run:
├─ User login
├─ Chat interface: "Make me a new ad concept"
├─ Route request → fine-tuned Vertex AI endpoint
└─ Return: Copywriting framework, design constraints, hex codes

Hosting: Scales to zero when idle
```

---

## Tech Stack

| Component | Tool | Why |
|---|---|---|
| **Data Warehouse & ML** | Google BigQuery + BigQuery ML (XGBoost) | Serverless analytics, statistical pattern discovery, zero ops |
| **Object Storage** | Google Cloud Storage (GCS) | Raw images, JSONL files, cost-effective |
| **Orchestration** | Cloud Workflows + Cloud Scheduler | Serverless cron jobs, fan-out parallelization |
| **Vision (Deterministic)** | Google Cloud Vision API | Extract text, objects, colors from images |
| **Vision (Subjective)** | Gemini Flash-Lite via Vertex AI | Fast subjective extraction (hook type, vibe, faces) |
| **Fine-Tuning** | Vertex AI Supervised Fine-Tuning (LoRA) | Lightweight model adapters for task-specific generation |
| **LLM Inference** | Vertex AI Endpoints | Deploy fine-tuned models as scalable endpoints |
| **Frontend** | Streamlit on Cloud Run | Chat interface, serverless, auto-scaling, scales to zero |
| **Ad Data Source** | Meta Ad Library API (Apify scraper backup) | Official API for ad metadata + competitor scraping fallback |
| **Internal Metrics** | Meta Marketing API via Cloud Function | Exact ROAS, CTR, spend data from your own campaigns |

---

## Data Strategy: Dual-Data Ingestion

### Path 1: Internal Data (Exact Metrics)

A Cloud Function on a schedule pulls your exact ad performance metrics:

- **ROAS** (Return on Ad Spend) — the ground truth
- **CTR** (Click-Through Rate)
- **Spend** (actual dollars spent)
- **Conversion metrics** (if available)

These metrics flow into **BigQuery** as the single source of truth for your own campaigns.

### Path 2: Competitor Data (Proxy Metrics)

A scheduled web scraper (Apify) pulls competitor ads from the Meta Ad Library:

- **Ad creative** (image/copy)
- **Ad delivery dates** → "Days Active" (how long an ad has been running)
- **Page/funding info**

Images go to **GCS**. Metadata goes to **BigQuery**.

**Why "Days Active" as a performance proxy:**
- Ads that have been running for 30+ days are assumed to be performing well
- Meta keeps underperforming ads off-platform quickly
- This is a cost-free proxy metric for competitor success

**Access:**
1. Create Apify account and deploy Meta Ad Library scraper
2. Create Meta Marketing API token for internal data pulls
3. Schedule both via Cloud Workflows + Cloud Scheduler

---

## AI Processing: Ensemble Extraction

Each ad is processed through a **deterministic + AI hybrid** approach:

### Phase 1: Deterministic Extraction (100% Accurate, Zero Cost)

Running on image metadata and standard algorithms:

```python
# Extract from file metadata
image_dimensions = get_aspect_ratio(image_file)  # 1:1, 9:16, etc.
file_size_bytes = get_file_size(image_file)

# Extract colors via K-Means clustering
color_palette = kmeans_clustering(image_pixels, k=5)
dominant_color_hex = color_palette[0]

# Extract on-screen text via Cloud Vision API
extracted_text = cloud_vision.detect_text(image_url)
```

### Phase 2: Parallel AI Extraction (Fast & Focused)

Using **Gemini Flash-Lite** (ultra-fast, cheap) with strict JSON schema:

```json
{
  "hook_type": "provocative_statement|story|question|pattern_interrupt",
  "has_human_face": true,
  "has_sparkles_or_text_overlay": false,
  "vibe": "empathetic|authoritarian|playful|mysterious",
  "copy_emotion": "fear|joy|curiosity|urgency"
}
```

**Why strict JSON schema:** Forces the model to output valid keys instead of filler text.

### Final Structured Record

Combining both phases + metadata:

```json
{
  "ad_id": "123456",
  "days_active": 45,
  "hook_type": "provocative_statement",
  "dominant_color_hex": "#2E7D9F",
  "aspect_ratio": "9:16",
  "has_human_face": true,
  "vibe": "empathetic",
  "copy_emotion": "fear → relief",
  "extracted_text": "Most people don't know their blood pressure...",
  "copy_length_chars": 127,
  "format": "single_image_with_text_overlay"
}
```

This record goes to **BigQuery** as a structured row (not an embedding).

---

## Pattern Discovery via BigQuery ML

No vector search. Instead, we use **statistical machine learning** to find proven patterns:

### Step 1: Prepare Training Data

```sql
-- Export structured ad data + performance label
SELECT
  dominant_color_hex,
  has_human_face,
  hook_type,
  copy_emotion,
  vibe,
  days_active AS performance_label
FROM ads_structured
WHERE days_active > 30
INTO OUTFILE 'gs://bucket/training_data.json';
```

### Step 2: Train XGBoost Model

In BigQuery ML, train a regressor to predict "days_active" based on visual/copy features:

```sql
CREATE OR REPLACE MODEL ad_success_model
OPTIONS(model_type='linear_reg') AS
SELECT
  dominant_color_hex,
  has_human_face,
  hook_type,
  copy_emotion,
  vibe,
  days_active AS label
FROM ads_structured;
```

### Step 3: Get Feature Importance

```sql
SELECT feature, importance
FROM ML.FEATURE_IMPORTANCE(MODEL ad_success_model)
ORDER BY importance DESC;
```

**Output Example:**
```
| Feature | Importance |
|---------|-----------|
| hook_type | 0.42 |
| has_human_face | 0.31 |
| dominant_color_hex | 0.18 |
| copy_emotion | 0.09 |
```

This tells us: **Hook type is the single biggest factor in ad longevity.**

### Step 4: Create Fine-Tuning Dataset

Export the highest-performing ads as Q&A pairs for fine-tuning:

```json
{
  "instruction": "Create a healthcare ad hook",
  "input": "Target: patients with chronic pain. Industry: healthcare.",
  "output": "Use a provocative_statement hook with empathetic vibe and a human face. Example: 'You don't have to live with this pain anymore.'"
}
```

---

## File Structure (Project Root)

```
media-ai-platform/
│
├── CLAUDE.md                        ← Claude Code instructions
├── README.md
├── .env.example
├── .gcp-config/                     ← GCP project config
│   ├── terraform/                   ← Infrastructure as code (optional)
│   │   ├── main.tf
│   │   ├── bigquery.tf
│   │   └── cloud_run.tf
│   └── cloud_workflows/             ← Workflow definitions
│       ├── dual_ingestion.yaml       ← Step 1: fetch internal + competitor data
│       └── ensemble_extraction.yaml  ← Step 2: process images + extract
│
├── extraction/                      ← Step 2: Ensemble Extraction
│   ├── deterministic.py             ← Image metadata + K-Means colors
│   ├── vision_api.py                ← Cloud Vision API wrapper
│   ├── gemini_extractor.py          ← Gemini Flash-Lite subjective extraction
│   ├── schema.json                  ← Strict JSON schema for Gemini
│   └── main.py                      ← Cloud Function entry point
│
├── ingestion/                       ← Step 1: Dual-Data Ingestion
│   ├── internal_metrics.py          ← Meta Marketing API → BigQuery
│   ├── competitor_scraper.py        ← Apify Meta Ad Library → GCS + BigQuery
│   └── main.py                      ← Cloud Function entry point
│
├── analysis/                        ← Step 3: Pattern Discovery
│   ├── bigquery_ml.sql              ← XGBoost training queries
│   ├── feature_importance.sql       ← Feature importance extraction
│   └── generate_finetuning_data.sql ← Export Q&A pairs for fine-tuning
│
├── frontend/                        ← Step 5: Streamlit UI
│   ├── app.py                       ← Main Streamlit app
│   ├── pages/
│   │   ├── home.py                  ← Chat interface
│   │   └── analytics.py             ← Pattern dashboard
│   ├── utils/
│   │   ├── vertex_ai.py             ← Vertex AI endpoint calls
│   │   └── bigquery_client.py       ← BigQuery queries
│   └── Dockerfile                   ← Cloud Run deployment
│
├── docs/                            ← Implementation details (separate files)
│   ├── 01-dual-ingestion.md
│   ├── 02-ensemble-extraction.md
│   ├── 03-pattern-discovery.md
│   ├── 04-finetuning.md
│   └── 05-streamlit-ui.md
│
└── Obsidian-Vault/                  ← Knowledge base
    ├── 🏠 Home.md
    ├── Concepts/
    ├── Frameworks/
    ├── Platform/
    └── Research/
```

**Note:** Each step (1–5) will have detailed implementation docs in `/docs/`. This POC Plan is the high-level overview.

---

## Build Plan (5-Phase Architecture)

### Phase 1: Dual-Data Ingestion Setup (Week 1)

**Goal:** Exact metrics + competitor data flowing into BigQuery

| Day | Task |
|---|---|
| Mon | Create GCP project, enable BigQuery, Cloud Functions, Cloud Storage, Cloud Workflows |
| Mon | Set up service accounts and IAM roles |
| Tue | Create Meta Developer account → get Marketing API token for internal data |
| Tue | Set up Apify account → configure Meta Ad Library scraper, get API key |
| Wed | Build `ingestion/internal_metrics.py` → fetch from Meta Marketing API → BigQuery |
| Wed | Build `ingestion/competitor_scraper.py` → trigger Apify → download images to GCS → metadata to BigQuery |
| Thu | Deploy both as Cloud Functions |
| Thu | Set up Cloud Scheduler jobs (daily for internal data, 3x/week for competitor scrapes) |
| Fri | Manual QA: Check BigQuery tables are populated, images in GCS, no errors in logs |

**End of Phase 1:** 100+ competitor ads + your exact campaign metrics in BigQuery

---

### Phase 2: Ensemble Extraction (Week 2)

**Goal:** Extract structured data from images without expensive LLM-only approach

| Day | Task |
|---|---|
| Mon | Build `extraction/deterministic.py` — image metadata, K-Means color extraction |
| Mon | Build `extraction/vision_api.py` — Cloud Vision API text/object detection |
| Tue | Build `extraction/schema.json` — define strict JSON output schema |
| Tue | Build `extraction/gemini_extractor.py` — Gemini Flash-Lite subjective data (hook type, vibe, etc.) |
| Wed | Test ensemble extraction on 50 ads, validate JSON output against schema |
| Wed | Build `extraction/main.py` — orchestrate deterministic + AI in parallel |
| Thu | Deploy as Cloud Function, wire into Workflow |
| Thu | Run extraction on 200 ads across 2–3 industries |
| Fri | QA: Check data quality, fix any schema violations, store in BigQuery |

**End of Phase 2:** 200+ ads with structured JSON: colors, text, hook type, vibe, emotions

---

### Phase 3: Pattern Discovery via BigQuery ML (Week 3)

**Goal:** Discover mathematically proven patterns using XGBoost

| Day | Task |
|---|---|
| Mon | Write `analysis/bigquery_ml.sql` — train XGBoost model predicting "days_active" |
| Tue | Run feature importance analysis — find which factors matter most |
| Tue | Manually inspect results: What makes ads run longest? |
| Wed | Write `analysis/generate_finetuning_data.sql` — export top-performing ads as Q&A pairs to JSONL |
| Wed | Upload JSONL to GCS for fine-tuning |
| Thu | Review fine-tuning dataset quality |
| Fri | Buffer/iteration based on findings |

**End of Phase 3:** Feature importance understood. Fine-tuning dataset ready in GCS.

---

### Phase 4: Supervised Fine-Tuning (Week 4)

**Goal:** Deploy a specialized model trained on proven patterns

| Day | Task |
|---|---|
| Mon | Upload JSONL to Vertex AI → initiate Supervised Fine-Tuning (LoRA) |
| Tue | Monitor fine-tuning job (usually 1–4 hours) |
| Wed | Deploy fine-tuned model as Vertex AI Endpoint |
| Thu | Test endpoint: send test prompts, verify output quality |
| Fri | Iterate on model if needed; document endpoint URL + auth |

**End of Phase 4:** Live Vertex AI endpoint serving fine-tuned model

---

### Phase 5: Streamlit UI (Week 5)

**Goal:** Stakeholder-facing chat interface

| Day | Task |
|---|---|
| Mon | Set up Streamlit project, basic structure |
| Tue | Build `frontend/app.py` — chat interface, user input → Vertex AI endpoint |
| Tue | Build `frontend/utils/vertex_ai.py` — call fine-tuned endpoint |
| Wed | Add pattern dashboard — show feature importance, top-performing ads |
| Wed | Test end-to-end: user types prompt → model returns ad suggestion |
| Thu | Build Dockerfile, deploy to Cloud Run |
| Thu | Full end-to-end test on live Cloud Run URL |
| Fri | Demo walkthrough, document user flows |

**End of Phase 5:** Live Streamlit dashboard on Cloud Run. User can generate ad concepts.

---

## Key Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Apify scraper encounters Meta blocks | Medium | Use official Ad Library API as primary; Apify as fallback. Monitor logs closely. |
| Gemini Flash-Lite extraction is inconsistent | Low | Strict JSON schema forces structured output; validate schema conformance |
| BigQuery ML model trains on noisy data | Medium | Manually review feature importance; exclude low-confidence ads from dataset |
| Fine-tuning dataset too small (<100 examples) | Medium | Phase 3 generates dataset automatically; scale by running extraction on more ads |
| Vertex AI fine-tuning costs unexpectedly high | Low | Use LoRA (low-rank adapters) to keep costs down; monitor job quotas |
| Cloud Run cold starts slow user experience | Low | Use container pre-warming; POC scale not a concern, but document for v2 |
| BigQuery ML quota exceeded | Low | POC scale won't exceed free tier; request quota increase if scaling beyond 1M rows |
| Fine-tuned model generates off-brand suggestions | Medium | Iterate fine-tuning dataset: add negative examples; retrain with curated Q&A |

---

## Costs (One-Off Estimate for POC)

| Service | Usage | Cost |
|---|---|---|
| **GCP Compute** | | |
| Cloud Functions (ingestion + extraction) | ~1000 invocations | ~£5–10 |
| Cloud Workflows | ~100 workflow runs | <£1 |
| BigQuery (data scan) | ~100 GB scan | ~£0.50 |
| BigQuery ML training (XGBoost) | 1 model training | ~£2–5 |
| **GCP Storage** | | |
| Cloud Storage (ads + images) | ~500 images @ 2MB ea | ~£2–3 |
| Vertex AI | | |
| Fine-tuning (LoRA) | 1 job | ~£5–10 |
| Endpoints (inference) | 100 calls | ~£1–2 |
| **Streaming** | | |
| Cloud Run (Streamlit) | 100 hours | ~£3–5 |
| Cloud Scheduler | Monthly jobs | <£1 |
| **External APIs** | | |
| Apify (Meta Ad Library scraper) | ~200 ad scrapes | ~£10–20 |
| Meta Marketing API | Internal metrics pull | £0 (your own data) |
| Cloud Vision API | ~500 images | ~£1 |
| Gemini API (Flash-Lite) | ~500 extractions | ~£2–5 |
| **Total (One-Off)** | | **~£35–60** |
| **Monthly (after POC)** | Ongoing jobs + Streamlit uptime | **~£10–20** |

---

## What Success Looks Like

At the end of 5 weeks, a user can:

1. **Open the Streamlit dashboard** (Cloud Run URL)
2. **Chat with the system:** "Create me a healthcare ad concept with a provocative hook"
3. **Get an AI-generated response:** Containing:
   - Recommended hook type + example hook
   - Design constraints (aspect ratio, color palette, human face presence)
   - Copywriting framework (PAS, AIDA, etc.)
   - Tone/vibe recommendations
4. **See analytics:** Feature importance dashboard showing which visual/copy elements correlate with longer-running ads
5. **Know it's working:** The model is generating suggestions **based on real, mathematically proven patterns** discovered from competitor ads

That's the POC. Everything after that is refinement, multi-platform support, team collaboration features, and a proper product layer.

---

## Immediate Next Steps

1. **GCP Setup:**
   - Create GCP project
   - Enable: BigQuery, Cloud Functions, Cloud Workflows, Cloud Scheduler, Cloud Run, Vertex AI
   - Create service account with appropriate IAM roles

2. **Third-Party Integrations:**
   - Create Meta Developer account → get Marketing API credentials
   - Create Apify account → set up Meta Ad Library scraper
   - Get Google Cloud Vision API credentials

3. **Git + Documentation:**
   - Clone this repo → create `/docs/` folder
   - Create implementation detail files (see "File Structure" section)
   - Document GCP service account secrets in `.env.example`

4. **Start Phase 1:**
   - Build `ingestion/internal_metrics.py` + `ingestion/competitor_scraper.py`
   - Deploy as Cloud Functions
   - Set up Cloud Workflows + Cloud Scheduler
