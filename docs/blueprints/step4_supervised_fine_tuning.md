# Step 4: Supervised Fine-Tuning (SFT) — Engineering Blueprint

> **Goal:** Teach a specialised model to directly output the mathematically proven creative formulas discovered in Step 3. The fine-tuned model collapses a complex multi-step lookup into a single, fast inference call grounded in real performance data.

---

## 1. Architecture Overview

```
[BigQuery: pattern_discovery_results]  (output of Step 3)
              │
              ▼
[Cloud Function: Dataset Builder]
   - SQL → JSONL export
   - Write to GCS
              │
              ▼
[GCS: gs://sft-datasets/{version}/train.jsonl]
              │
              ▼
[Vertex AI: Supervised Fine-Tuning Job]
   - Base model: Gemma 3 (1B or 4B)
   - Method: LoRA
   - Platform: Vertex AI Tuning Console / API
              │
              ▼
[Vertex AI: Fine-Tuned Endpoint]
   - Registered model in Vertex AI Model Registry
   - Deployed to a Vertex AI endpoint (scales to zero)
              │
              ▼
[Step 5: Streamlit UI queries this endpoint]
```

---

## 2. Dataset Construction

The dataset is built entirely from BigQuery — no manual labelling required. The patterns discovered in Step 3 become the ground-truth training examples.

### 2.1 JSONL Format (Vertex AI SFT Standard)

Vertex AI SFT requires the `messages` format (chat-style):

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a performance creative strategist. Given a product category and target audience, output a structured creative brief with proven design constraints. Always respond in valid JSON."
    },
    {
      "role": "user",
      "content": "Generate a winning ad brief for: Category=skincare, Target=women 25-40, Platform=Instagram Feed (1:1)"
    },
    {
      "role": "assistant",
      "content": "{\"hook_framework\": \"PAS\", \"background_style\": \"Studio\", \"dominant_hex_palette\": [\"#F5E6D3\", \"#C9A882\", \"#FFFFFF\"], \"human_presence\": true, \"suggested_headline\": \"Struggling with uneven skin tone?\", \"call_to_action\": \"Shop Now\", \"confidence_score\": 0.87, \"sample_size\": 42, \"avg_roas\": 4.2}"
    }
  ]
}
```

### 2.2 Dataset Builder SQL

```sql
-- Generates the SFT training prompt-response pairs from pattern results
SELECT
    TO_JSON_STRING(
        STRUCT(
            [
                STRUCT(
                    'system' AS role,
                    'You are a performance creative strategist. Given a product category and target audience, output a structured creative brief with proven design constraints. Always respond in valid JSON.' AS content
                ),
                STRUCT(
                    'user' AS role,
                    CONCAT(
                        'Generate a winning ad brief for: ',
                        'Category=', COALESCE(inferred_category, 'general'), ', ',
                        'Platform=', COALESCE(aspect_ratio, '1:1'), ', ',
                        'Data Source=', data_source
                    ) AS content
                ),
                STRUCT(
                    'assistant' AS role,
                    TO_JSON_STRING(STRUCT(
                        hook_framework,
                        background_style,
                        contrast_ratio_type,
                        human_presence,
                        texture_visible,
                        avg_performance          AS avg_roas,
                        sharpe_ratio             AS confidence_score,
                        sample_size,
                        metric_type,
                        computed_at
                    )) AS content
                )
            ] AS messages
        )
    ) AS jsonl_row
FROM `{project}.{dataset}.pattern_discovery_results`
WHERE sample_size >= 10        -- Only high-confidence patterns
  AND sharpe_ratio >= 1.5      -- Only statistically stable patterns
ORDER BY sharpe_ratio DESC;
```

### 2.3 Cloud Function: JSONL Export to GCS

```python
def export_sft_dataset(version: str) -> str:
    """
    Queries BigQuery, streams JSONL to GCS.
    Returns the GCS URI of the training file.
    """
    bq_client = bigquery.Client()
    storage_client = storage.Client()

    query = SFT_DATASET_SQL.format(project=PROJECT_ID, dataset=DATASET_ID)
    rows = bq_client.query(query).result()

    gcs_path = f"sft-datasets/{version}/train.jsonl"
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)

    with blob.open("w") as f:
        for row in rows:
            f.write(row["jsonl_row"] + "\n")

    record_count = sum(1 for _ in rows)
    logger.info("sft_dataset_exported", gcs_path=gcs_path, record_count=record_count)

    return f"gs://{GCS_BUCKET}/{gcs_path}"
```

### 2.4 Dataset Quality Gates

Before triggering the Vertex AI fine-tuning job, assert these conditions:

| Check | Minimum Threshold | Action on Fail |
|---|---|---|
| Total training examples | ≥ 100 rows | Abort and log warning |
| Unique `hook_framework` values | ≥ 3 distinct values | Log warning, proceed |
| JSONL parse validity | 100% rows parseable | Abort on any parse error |
| Avg `sharpe_ratio` across dataset | ≥ 1.2 | Log warning, proceed |

---

## 3. Vertex AI Fine-Tuning Job

### 3.1 Model Selection
- **Base model:** `gemma-3-1b-it` (instruction-tuned) for fast, cheap inference
- Upgrade to `gemma-3-4b-it` if output quality is insufficient after evaluation
- Do **not** use Gemini family for SFT — only Gemma variants are available for supervised fine-tuning on Vertex AI

### 3.2 LoRA Configuration (Low-Rank Adaptation)
LoRA trains a small adapter over the cross-attention layers without modifying base model weights. This makes the fine-tuned model cheap to store, fast to swap, and easy to version.

Recommended parameters for the Vertex AI SFT console:

```yaml
tuning_task:
  base_model: gemma-3-1b-it
  hyperparameters:
    epoch_count: 3
    learning_rate_multiplier: 1.0
    adapter_size: 4          # LoRA rank — increase to 8 or 16 if underfitting
  training_dataset:
    gcs_uri: gs://{bucket}/sft-datasets/{version}/train.jsonl
    data_split_ratio: 0.9    # 90% train / 10% validation
```

### 3.3 Triggering via API (Automated Retraining)

When a new dataset version is exported, trigger the tuning job programmatically:

```python
from google.cloud import aiplatform

def trigger_sft_job(training_data_uri: str, model_display_name: str):
    aiplatform.init(project=PROJECT_ID, location=REGION)

    job = aiplatform.CustomJob.from_local_script(
        display_name=model_display_name,
        script_path="train_sft.py",
        container_uri="us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.1-13:latest",
        requirements=["google-cloud-aiplatform[preview]"],
        args=[
            f"--training_data={training_data_uri}",
            f"--base_model=gemma-3-1b-it",
            f"--epochs=3",
            f"--lora_rank=4",
        ],
    )
    job.run(sync=False)
    logger.info("sft_job_triggered", job_name=job.display_name, training_uri=training_data_uri)
```

---

## 4. Model Evaluation

After each fine-tuning job completes, run a small evaluation suite before promoting the model to the production endpoint.

### 4.1 Evaluation Prompt Set
Maintain a static set of 20 held-out evaluation prompts in `gs://sft-datasets/eval/eval_prompts.jsonl`. These prompts must never appear in the training data.

### 4.2 Evaluation Metrics

| Metric | Method | Pass Threshold |
|---|---|---|
| JSON parse rate | Parse all 20 outputs as JSON | ≥ 95% parseable |
| Schema key completeness | Check all required keys present | ≥ 90% complete |
| `hook_framework` validity | Value in known Enum list | ≥ 95% valid |
| Latency (P95) | Time-to-first-token on endpoint | ≤ 3 seconds |

```python
def evaluate_model(endpoint_id: str) -> dict:
    eval_prompts = load_eval_prompts(EVAL_GCS_PATH)
    results = []
    for prompt in eval_prompts:
        response = query_vertex_endpoint(endpoint_id, prompt)
        results.append({
            "parseable": is_valid_json(response),
            "schema_complete": has_required_keys(response, REQUIRED_SCHEMA_KEYS),
            "hook_valid": extract_hook(response) in VALID_HOOK_FRAMEWORKS,
        })
    return aggregate_eval_results(results)
```

---

## 5. Model Registry & Versioning

All fine-tuned adapters are registered in the **Vertex AI Model Registry** with semantic versioning and metadata tags.

```python
model = aiplatform.Model.upload(
    display_name=f"creative-brief-sft-v{VERSION}",
    artifact_uri=f"gs://{bucket}/models/sft/{VERSION}/",
    serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/pytorch-cpu.1-13:latest",
    labels={
        "pipeline_version": VERSION,
        "training_examples": str(TRAINING_COUNT),
        "base_model": "gemma-3-1b-it",
        "lora_rank": "4",
    }
)
```

Never delete old model versions. Roll back is a single endpoint re-deployment.

---

## 6. Operational Guardrails

### 6.1 Retraining Trigger Conditions
Only trigger a new fine-tuning job when **all** of these conditions are met:
- At least 50 new pattern rows have been added to `pattern_discovery_results` since the last training run
- Dataset quality gates (Section 2.4) pass
- No fine-tuning job is currently running (check Vertex AI Jobs API before submitting)

### 6.2 Cost Guard
Fine-tuning on Vertex AI is charged by compute hour. Set a maximum training duration in the job config:
```yaml
timeout: 3600  # 1 hour maximum — job auto-terminates if exceeded
```
Alert via Cloud Monitoring if a job exceeds 45 minutes (early warning threshold).

### 6.3 Staging → Production Promotion Gate
New model versions are deployed to a **staging endpoint** first. They are only promoted to the production endpoint (used by Step 5) if all evaluation metrics pass. Implement as a Cloud Workflow step:

```yaml
# Cloud Workflow step
- evaluate_model:
    call: http.post
    args:
      url: ${EVAL_CLOUD_FUNCTION_URL}
      body:
        endpoint_id: ${staging_endpoint_id}
    result: eval_result
- check_eval:
    switch:
      - condition: ${eval_result.body.json_parse_rate >= 0.95}
        next: promote_to_production
      - condition: true
        next: alert_and_abort
```
