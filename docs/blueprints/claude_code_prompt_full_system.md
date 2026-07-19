# Claude Code — Full System Build Prompt
## Social Media Analysis Agent

> **Reference documents:** Keep `master_architecture_plan.md` and all step blueprints open alongside this prompt. Every architectural decision described there is the source of truth. When in doubt, re-read the relevant blueprint rather than inventing an approach.

---

## ROLE

You are a **Senior System Software Engineer** and GCP specialist building a production-grade, serverless Social Media Analysis Agent for a solo developer. You write code that is observable, modular, and maintainable without a dedicated team.

Your governing principles:
- **Correctness over cleverness.** Clear, boring code is preferred over clever abstractions.
- **Modularity over monoliths.** Every step is independently runnable, testable, and deployable.
- **Observable by default.** Every component emits structured logs. Failures are never silent.
- **Cheap by design.** Prefer deterministic code over AI calls wherever the task is structural. Prefer async and batching to minimise API round trips.

**Before writing any module:** state your assumption about the implementation approach and ask for confirmation. Do not guess on anything that touches GCP auth, API structure, or BigQuery schema.

---

## TOP-LEVEL REPOSITORY STRUCTURE

```
social-media-agent/
├── shared/                         # Shared across all steps
│   ├── config.py                   # All env vars (pydantic-settings)
│   ├── logger.py                   # Structlog setup
│   ├── exceptions.py               # Custom exception hierarchy
│   └── models/
│       ├── __init__.py
│       ├── extraction_schema.py    # Pydantic: Step 2 Master JSON Schema
│       ├── performance_schema.py   # Pydantic: Step 1 BigQuery rows
│       └── sft_schema.py           # Pydantic: Step 4 JSONL format
│
├── step1_ingestion/
│   ├── meta_api_client.py
│   ├── apify_client.py
│   ├── gcs_image_downloader.py
│   ├── bigquery_writer.py
│   ├── cloud_function_entry.py     # Cloud Function handler
│   └── tests/
│       ├── test_meta_api_client.py
│       ├── test_apify_client.py
│       └── test_bigquery_writer.py
│
├── step2_extraction/               # Detailed in step2 claude_code_prompt.md
│   ├── stages/
│   │   ├── base_stage.py
│   │   ├── stage_01_metadata.py
│   │   ├── stage_02_ocr.py
│   │   ├── stage_03_color.py
│   │   ├── stage_04_scraper.py
│   │   ├── stage_05_cognitive.py
│   │   └── stage_06_bigquery.py
│   ├── clients/
│   ├── orchestrator.py
│   └── tests/
│
├── step3_discovery/
│   ├── sql/
│   │   ├── flatten_view.sql
│   │   ├── train_roas_model.sql
│   │   ├── train_longevity_model.sql
│   │   ├── feature_importance.sql
│   │   ├── segment_correlation.sql
│   │   └── write_pattern_results.sql
│   ├── bqml_runner.py              # Executes SQL files, checks job status
│   ├── pattern_evaluator.py        # Freshness checks, quality gates
│   ├── cloud_function_entry.py
│   └── tests/
│       ├── test_bqml_runner.py
│       └── test_pattern_evaluator.py
│
├── step4_sft/
│   ├── dataset_builder.py          # BigQuery → JSONL → GCS
│   ├── quality_gates.py            # Dataset validation before SFT trigger
│   ├── vertex_sft_client.py        # Triggers + monitors Vertex AI SFT jobs
│   ├── model_evaluator.py          # Runs eval prompts, computes pass/fail
│   ├── registry_client.py          # Registers model versions
│   ├── cloud_function_entry.py
│   └── tests/
│       ├── test_dataset_builder.py
│       ├── test_quality_gates.py
│       └── test_model_evaluator.py
│
├── step5_ui/
│   ├── app.py                      # Streamlit entry point
│   ├── pages/
│   │   ├── 01_brief_generator.py
│   │   ├── 02_pattern_explorer.py
│   │   └── 03_ad_library.py
│   ├── components/
│   │   ├── brief_card.py
│   │   ├── pattern_chart.py
│   │   └── ad_card.py
│   ├── clients/
│   │   ├── vertex_client.py
│   │   └── bigquery_client.py
│   ├── Dockerfile
│   └── tests/
│       └── test_vertex_client.py
│
├── workflows/
│   ├── main_pipeline.yaml          # Cloud Workflow definition
│   └── step2_extraction.yaml       # Step 2 sub-workflow
│
├── infrastructure/
│   ├── bigquery_schemas/
│   │   ├── own_ads_performance.json
│   │   ├── competitor_ads_raw.json
│   │   └── ads_extraction_results.json
│   ├── cloud_run_services/
│   │   └── step2_service.yaml
│   └── iam_bindings.sh             # IAM setup script
│
├── shared_requirements.txt         # Common deps across all steps
├── .env.example                    # All required env vars documented
├── Makefile                        # dev shortcuts (test, lint, deploy)
└── README.md
```

---

## SHARED INFRASTRUCTURE (BUILD FIRST)

### 1. `shared/config.py`
Single source of truth for all configuration. Every other module imports from here — no `os.environ.get()` anywhere else in the codebase.

```python
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # GCP Core
    gcp_project_id: str
    gcp_region: str = "us-central1"

    # GCS
    gcs_bucket_name: str
    gcs_dlq_bucket: str = "corrupted-ad-creatives"
    gcs_sft_datasets_prefix: str = "sft-datasets"

    # BigQuery
    bq_dataset_id: str
    bq_own_ads_table: str = "own_ads_performance"
    bq_competitor_ads_table: str = "competitor_ads_raw"
    bq_extraction_table: str = "ads_extraction_results"
    bq_patterns_table: str = "pattern_discovery_results"

    # Meta API
    meta_app_id: str
    meta_app_secret: str
    meta_access_token: str
    meta_ad_account_id: str

    # Apify
    apify_api_token: str
    apify_actor_id: str

    # Vertex AI
    vertex_ai_region: str = "us-central1"
    vertex_gemini_model: str = "gemini-2.0-flash-lite"
    vertex_gemma_model: str = "gemma-4"
    vertex_sft_base_model: str = "gemma-3-1b-it"
    vertex_sft_endpoint_id: str = ""
    vertex_daily_quota_limit: int = 5000

    # Step 2 Config
    scraper_timeout_seconds: int = 10
    max_image_dimension_px: int = 1024
    kmeans_clusters: int = 3
    kmeans_perimeter_pct: float = 0.10

    # Step 3 Config
    bqml_retrain_min_new_rows: int = 50
    pattern_min_sample_size: int = 5
    pattern_min_sharpe_ratio: float = 1.5
    pattern_freshness_hours: int = 48

    # Step 4 Config
    sft_min_training_examples: int = 100
    sft_lora_rank: int = 4
    sft_epochs: int = 3
    sft_max_job_duration_seconds: int = 3600

    class Config:
        env_file = ".env"
        case_sensitive = False

@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

### 2. `shared/logger.py`
Structlog configured once. All modules call `get_logger(__name__)`.

```python
import structlog
import logging
import sys

def configure_logging(log_level: str = "INFO"):
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),    # Machine-parseable for Cloud Logging
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

def get_logger(module_name: str) -> structlog.BoundLogger:
    return structlog.get_logger(module_name)
```

### 3. `shared/exceptions.py`
A clear exception hierarchy means error handlers never need to inspect error messages to route failures.

```python
class AgentBaseError(Exception):
    """Root exception for all pipeline errors."""

# Step 1
class IngestionError(AgentBaseError): pass
class MetaAPIError(IngestionError): pass
class ApifyError(IngestionError): pass

# Step 2
class ExtractionError(AgentBaseError): pass
class StageError(ExtractionError):
    def __init__(self, stage_name: str, message: str, original: Exception = None):
        self.stage_name = stage_name
        self.original = original
        super().__init__(f"[{stage_name}] {message}")

class ScraperTimeoutError(ExtractionError): pass
class CognitiveParseError(ExtractionError): pass
class CorruptedImageError(ExtractionError): pass

# Step 3
class PatternDiscoveryError(AgentBaseError): pass
class ModelTrainingError(PatternDiscoveryError): pass
class StalePatternError(PatternDiscoveryError): pass

# Step 4
class SFTError(AgentBaseError): pass
class DatasetQualityError(SFTError): pass
class ModelEvaluationError(SFTError): pass

# Step 5
class UIClientError(AgentBaseError): pass
class EndpointError(UIClientError): pass
```

---

## LOGGING STANDARDS (ALL MODULES)

Every module uses `structlog`. These log event names are standardised across all steps:

| Event Key | Level | Meaning |
|---|---|---|
| `{step}_started` | INFO | Step / stage entry point |
| `{step}_completed` | INFO | Successful exit, always include `duration_ms` |
| `{step}_skipped` | WARNING | Skipped due to upstream null / condition not met |
| `{step}_failed` | ERROR | Exception caught; include `error_type`, `error_msg` |
| `api_call_attempted` | DEBUG | Before any external API |
| `api_call_succeeded` | DEBUG | After response, include `latency_ms` |
| `api_call_failed` | ERROR | After failed API; include `status_code`, `attempt` |
| `fallback_applied` | WARNING | A default/null substituted for a failed field |
| `dlq_dispatched` | ERROR | Asset routed to dead letter queue |
| `quality_gate_passed` | INFO | A data quality check passed |
| `quality_gate_failed` | ERROR | A data quality check failed; include gate name |
| `cost_threshold_warning` | WARNING | Approaching a quota or budget limit |

---

## TESTING STANDARDS (ALL MODULES)

**Framework:** `pytest` + `pytest-asyncio` + `pytest-mock`
**Rule:** Every test must run fully offline — zero GCP credentials, zero live API calls.
**Coverage target:** ≥ 90% line coverage per module.

### Mandatory test cases per module:
1. **Happy path** — All inputs valid, expected output returned
2. **Partial failure** — One upstream field is null/missing, module degrades gracefully
3. **Total failure** — Module raises the correct typed exception, DLQ/fallback is triggered
4. **Retry behaviour** (API modules only) — Mock 2 failures + 1 success, assert retry fires

### Fixture pattern:
All test fixtures live in `tests/fixtures/`. Mock GCP clients at the Python client library level using `unittest.mock.patch` — do not mock at the HTTP level.

```python
# Fixture pattern for GCP clients
@pytest.fixture
def mock_bigquery_client(mocker):
    mock = mocker.patch("google.cloud.bigquery.Client")
    mock.return_value.query.return_value.result.return_value = iter([
        {"ad_id": "test_001", "roas": 3.5}
    ])
    return mock
```

---

## BUILD ORDER

Build and validate each module independently. The contract between steps is BigQuery tables and GCS paths — validate these interfaces with integration tests before connecting live.

```
Phase 0: Shared Foundation
  [ ] shared/config.py           + unit test (env var validation)
  [ ] shared/logger.py           + smoke test
  [ ] shared/exceptions.py       + hierarchy test
  [ ] shared/models/             + Pydantic schema tests

Phase 1: Step 1 Ingestion
  [ ] meta_api_client.py         + unit tests (mocked HTTP)
  [ ] apify_client.py            + unit tests (mocked webhook)
  [ ] bigquery_writer.py         + unit tests (mocked BQ client)
  [ ] gcs_image_downloader.py    + unit tests
  [ ] cloud_function_entry.py    + integration test (mock all clients)

Phase 2: Step 2 Extraction
  (Full detail in step2 claude_code_prompt.md — build in that order)

Phase 3: Step 3 Pattern Discovery
  [ ] sql/*.sql                  + test each SQL file with BQ dry run
  [ ] bqml_runner.py             + unit tests (mocked BQ jobs API)
  [ ] pattern_evaluator.py       + unit tests (freshness + quality gates)
  [ ] cloud_function_entry.py    + integration test

Phase 4: Step 4 SFT
  [ ] dataset_builder.py         + unit tests (mocked BQ + GCS)
  [ ] quality_gates.py           + unit tests (edge cases: 0 rows, no diversity)
  [ ] vertex_sft_client.py       + unit tests (mocked Vertex AI jobs API)
  [ ] model_evaluator.py         + unit tests (mocked endpoint)
  [ ] registry_client.py         + unit tests
  [ ] cloud_function_entry.py    + integration test

Phase 5: Step 5 UI
  [ ] clients/vertex_client.py   + unit tests (mocked endpoint + JSON parse failures)
  [ ] clients/bigquery_client.py + unit tests (mocked BQ)
  [ ] pages/*.py                 + Streamlit component tests
  [ ] Dockerfile                 + build smoke test

Phase 6: Integration
  [ ] workflows/main_pipeline.yaml  + Cloud Workflow syntax validation
  [ ] End-to-end test: fixture image → full pipeline → mock BigQuery assert
  [ ] Makefile deploy targets for each Cloud Run service + Cloud Function
```

---

## CRITICAL QUESTIONS — ASK BEFORE STARTING ANY PHASE

**Phase 0 / Auth:**
1. Will local development use Application Default Credentials (`gcloud auth application-default login`) or a Service Account JSON key file?
2. Will Cloud Run / Cloud Functions use Workload Identity or a Service Account key stored in Secret Manager?

**Phase 1 / Meta API:**
3. Is the Meta access token a long-lived System User token, or does it need periodic refresh? Does the refresh logic belong in this codebase?
4. Which Meta API fields are actually available on your ad account (some require special permissions — e.g., `roas` requires the pixel to be configured)?

**Phase 2 / Step 2:**
5. See the dedicated Step 2 Claude Code prompt for all Step 2 questions.

**Phase 3 / BigQuery ML:**
6. Is BigQuery ML already enabled on the project, or does it need to be activated?
7. Is there a preference for running BQML training on a schedule vs. event-driven (i.e., triggered by new row count)?

**Phase 4 / Vertex AI SFT:**
8. Has Vertex AI been used in this project before (i.e., are quotas / APIs already enabled)?
9. Which Gemma model variant to start with — `gemma-3-1b-it` (faster/cheaper) or `gemma-3-4b-it` (higher quality)?
10. Is there a requirement for the SFT model to output in a specific language other than English?

**Phase 5 / Streamlit:**
11. Should the Streamlit app authenticate users via Google IAP (recommended), or is an internal-only deployment without auth acceptable for the initial version?
12. Is there a preference for the Cloud Run service URL — custom domain or the default `run.app` URL?

**General:**
13. What Python version should be targeted across all modules? (Recommend 3.12)
14. Should all Cloud Functions / Cloud Run services share a single `requirements.txt`, or maintain independent dependency files per step?
15. Is there a preference for the GCS bucket naming convention (single bucket with prefixes vs. one bucket per step)?
