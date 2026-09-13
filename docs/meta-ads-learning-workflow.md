# Supplements Meta Ads: durable learning workflow

This is the operational contract for scraping, Extraction, model training, evaluation, SHAP
guidance, lifecycle polling, and the quality-reference corpus. It implements ADR-009 and keeps the
scope to **Supplements**. There is no ROAS in this workflow.

## Data flow and ownership

```diagram
Apify scrape ──▶ local atomic corpus ──▶ BigQuery ad snapshots
                       │                         │
                       ├──▶ manual taxonomy ─────┤ (false/unlabeled stop here)
                       │                         │
                       └──▶ Step 2 Extraction ──▶ BigQuery feature snapshots
                                  │
                                  └──▶ feature matrix ──▶ frozen holdout + tuned XGBoost/Cox
                                                               │
                                                               ├──▶ evaluation + SHAP guide
                                                               └──▶ benchmark candidate review

tracked ad IDs ──▶ repeated Apify polls ──▶ lifecycle observations ──▶ 30/60/90/180 KM report
```

The local writers use exclusive sidecar locks and atomic rename checkpoints. Model worker count is
one by default so separate jobs can coexist without each XGBoost fit consuming every CPU. BigQuery
is append-only; GCS objects and manifests are content-addressed. No API token is accepted as a CLI
argument or written to a report.

## GCP resources

Configuration is read only through `pipeline/config.py::get_settings()`:

- `GCP_PROJECT_ID` (currently `clean-patrol-496108-m9`)
- `BIGQUERY_DATASET` (default `ad_intelligence`)
- `GCS_AD_BUCKET` (no default; deliberately requires an explicit bucket)
- the repository's keyless WIF/ADC variables documented in `docs/gcp-orb-auth.md`

`uv run python -m ingestion.sync_gcp ...` creates these partitioned BigQuery tables when IAM
permits it:

| Table | Grain | Purpose |
|---|---|---|
| `competitor_ad_snapshots` | ad × observed content | normalized metadata, IDs, URLs, proxy inputs |
| `ad_feature_snapshots` | ad × feature content/schema | Master-schema Extraction and matrix row |
| `ad_lifecycle_observations` | ad × poll run | active, explicit inactive, miss, or unknown evidence |
| `survival_validation_reports` | report input hash/time | immutable horizon validation output |

The ad payload intentionally omits `local_image_path`; it retains `ad_id`, page metadata, delivery
dates, URLs, query provenance where available, and all other normalized fields. Query the latest
snapshot with `ROW_NUMBER() OVER (PARTITION BY ad_id ORDER BY observed_at DESC)`; never mutate old
rows.

## Runbook

### 1. Scrape and create a human label set

```bash
uv run python -m ingestion.fresh_corpus_scrape --out data/supplements_fresh.json
uv run python -m pipeline.validation.phase0_validator sample data/supplements_fresh.json
```

The sampler creates 120 rows with stable seed 42 and stratifies across recorded search queries,
contamination-risk terms (pets, topical products, equipment), and text-derived subcategory
*candidate* strata. Older corpora do not have per-ad query provenance; candidate strata recover
review coverage but are explicitly not labels. Convert the sample/template into the immutable human
review contract:

```bash
uv run python -m pipeline.validation.taxonomy_adjudication init \
  --sample .amp/in/artifacts/supplements_taxonomy_sample_120.json \
  --template .amp/in/artifacts/supplements_taxonomy_labels_120.json \
  --out data/supplements_taxonomy_review_v1.json
```

Humans fill the primary decisions for every row, independently double-review at least 20%, and add
an independent adjudicator plus rationale for every disagreement. The fixed Supplement
subcategories are defined in `taxonomy_adjudication.py`; do not invent or silently normalize labels.
Then validate and publish only explicitly approved Supplements rows:

```bash
uv run python -m pipeline.validation.taxonomy_adjudication validate \
  --sample .amp/in/artifacts/supplements_taxonomy_sample_120.json \
  --review data/supplements_taxonomy_review_v1.json \
  --report data/supplements_taxonomy_adjudication_report_v1.json \
  --approved-labels data/supplements_taxonomy_approved_v1.json
```

Validation fails closed unless every label has reviewer/time provenance, at least 20% is
double-reviewed, binary Cohen's κ is at least 0.80, and every binary or subcategory disagreement is
independently adjudicated. The report binds the approved labels by SHA-256. It is not a substitute
for the human review.

### 2. Extract, train, evaluate, generate guidance, and sync

```bash
uv run python -m pipeline.supplements_workflow \
  --skip-scrape \
  --taxonomy-labels data/supplements_taxonomy_approved_v1.json \
  --taxonomy-report data/supplements_taxonomy_adjudication_report_v1.json \
  --skip-embeddings \
  --sync-gcp
```

The workflow uses Cloud Vision `document_text_detection` for OCR, Replicate for configured
cognitive Extraction, a temporal advertiser holdout with zero advertiser overlap, inner temporal
parameter selection, baseline comparisons, subcategory/calendar-cohort metrics, and Tree SHAP.
Raw embeddings are ablated and never turned into directives. Generation guidance excludes missing,
pooled, non-directional, operational, and unevaluable Cox signals. Reports include input hashes,
schema version, implementation hash, random seed, worker count, and Git provenance.

Training reports now include paired bootstrap 95% intervals for challenger-minus-baseline MAE,
calibration gap, and fixed predicted-top-20% precision; descriptive raw-feature drift; segment
advertiser counts; and a fail-closed promotion decision. Promotion requires the entire taxonomy
gate, clean Git provenance, zero advertiser overlap, point MAE improvement, an MAE-difference
interval below zero, and top-20% precision's lower interval above the 20% prevalence baseline.
The untouched holdout must also contain at least 100 rows and 10 advertisers.
Segment generalization additionally requires at least 100 rows and 10 advertisers; smaller segments
remain descriptive.

For future trend tests, preserve each new scrape as a new observation window:

```bash
# Freeze the current baseline once.
uv run python -m pipeline.model_training.observation_windows \
  --ads data/supplements_fresh.json \
  --out data/windows/supplements-baseline-v1.json

# On a genuinely later scrape, chain a new immutable window.
uv run python -m pipeline.model_training.observation_windows \
  --ads data/supplements_future.json \
  --previous-window data/windows/supplements-baseline-v1.json \
  --out data/windows/supplements-future-v1.json

# Evaluate the old report/specification before any retraining on future rows.
uv run python -m pipeline.model_training.evaluate_future_window \
  --baseline-report data/model_training_report_fresh.json \
  --baseline-matrix data/feature_matrix_fresh.json \
  --baseline-ads data/supplements_fresh.json \
  --baseline-window data/windows/supplements-baseline-v1.json \
  --future-matrix data/supplements_future_matrix.json \
  --future-ads data/supplements_future.json \
  --future-window data/windows/supplements-future-v1.json \
  --evaluation-as-of 2026-11-30T00:00:00Z \
  --out data/supplements_future_evaluation_v1.json
```

The evaluator refits only the frozen parameters on the old training rows; future rows cannot affect
calibration, preprocessing, fitting, or parameter selection. It rejects stale `days_active` values
that do not match the immutable future snapshot age, active targets younger than 30 days, unreviewed
taxonomy, broken hashes/window chains, irreproducible splits, and advertiser overlap. Merely waiting
30 days after a stale snapshot does not mature its label. Only after this evaluation is persisted may
the future window enter a later training corpus. The current 2026-09-12 corpus is a single window, so
calendar trend claims remain unsupported.

### 2a. Freeze or promote the Generation handoff

V3 is frozen without changing the consumer's three fixed root files:

```bash
uv run python -m pipeline.model_training.handoff_bundle build \
  --guide .amp/in/artifacts/meta_model_handoff_supplements_generation_guide_v3.json \
  --training-report .amp/in/artifacts/meta_model_handoff_supplements_training_report_v3.json \
  --survival-report .amp/in/artifacts/supplements_survival_validation_v1.json \
  --out .amp/in/artifacts/handoffs/supplements-v3-bundle-v2 \
  --version supplements-v3-bundle-v2 --freeze-existing-v3
uv run python -m pipeline.model_training.handoff_bundle verify \
  --bundle .amp/in/artifacts/handoffs/supplements-v3-bundle-v2
```

The special freeze accepts only the three known v3 hashes and corrected zero-event Cox semantics.
Bundle v2 uses the Generation consumer's canonical root filenames while preserving identical v3
bytes; the first manifest remains immutable and superseded for automated path resolution only.
Any new immutable version requires an objective `promotion_decision.status=promoted`, consistent
leakage-safe split/model results, a worse embedding ablation, and a guide whose directional buckets
are independently re-derived from no-embedding SHAP. Generation currently does not discover bundle
manifests, so v4 also requires a coordinated loader change and shadow validation; never overwrite
v3 filenames for compatibility. Generated outputs are never Meta performance labels.

### 3. Poll lifecycle and validate horizons

```bash
uv run python -m ingestion.poll_ad_status --batch-size 50
uv run python -m pipeline.model_training.validate_survival
```

Run polling on a recurring external scheduler (daily is appropriate for day-level Longevity; this
repository does not silently create production Cloud Scheduler resources). Polling fetches page
inventories and matches tracked ad IDs exactly. A page is complete only when the actor reports that
it exhausted source URLs and returned another ad from that page as a positive control; result-capped
or empty runs remain unknown. One complete miss remains unknown; two consecutive complete misses
infer an interval-censored stop at the first miss. A returned inactive/stop field is an immediate
explicit event. Paid poll output is checkpointed locally before its BigQuery append, so a warehouse
failure does not require repeating the paid call.

The horizon report's outcome denominator includes ads ending by the horizon plus ads observed
through it. Ads censored before it are unknown. `insufficient_evidence` means the configured minimum
of 20 known outcomes and five endings was not reached; it does **not** mean the survival estimate is
zero or that an ad failed.

### 4. Curate and archive benchmark images

After the taxonomy labels exist:

```bash
uv run python -m pipeline.model_training.benchmark_corpus \
  --ads data/supplements_fresh.json \
  --matrix data/supplements_feature_matrix.json \
  --taxonomy-labels data/supplements_ground_truth.json
# Human sets approved_for_craft_reference=true on at most eight candidates.
uv run python -m ingestion.archive_ad_images \
  --ads data/supplements_fresh.json \
  --benchmark-review data/supplements_benchmark_review.json \
  --collection supplements-quality-v1
```

Ranking uses the project's Longevity-led composite proxy and favors advertiser diversity. It does
not certify craft quality: no image is archived as a benchmark until a human explicitly approves
it. The manifest fixes `directive_aligned=false`. Generation may compare against references only
for art direction, imagery quality, typography, visual hierarchy, composition, and brand cohesion;
it may not copy style, pixels, products, wording, people, scenes, or layouts.

## Measured image storage footprint and cost

On a real 200-image sample from the 3,555-ad Supplements corpus:

| Measure | Original JPEG | 1600 px WebP q82 |
|---|---:|---:|
| Total | 141,826,984 bytes | 17,083,040 bytes |
| Mean per ad | 709,135 bytes | 85,415 bytes |
| Compression ratio | 100% | 12.0% |
| Quality check | — | median PSNR 40.04 dB; minimum 31.75 dB |

Linear estimate for all 3,555 images: about 2.35 GiB original or 0.283 GiB compressed. At the
published [Cloud Storage us-central1 Standard rate](https://cloud.google.com/storage/pricing) of
about $0.020/GiB-month, storage-only cost is roughly **$0.047/month original** or **$0.006/month
compressed**. API operations, retrieval, network egress, taxes, and future growth are excluded.
Eight compressed quality references at the measured mean are roughly 0.65 MiB and effectively
negligible in storage cost.

## Current evidence and known constraints

- Corpus: 3,555 unique image ads from 12 Supplements searches and 1,151 pages.
- Exact seeded Extraction sample: 600; Cloud Vision OCR 600/600; cognitive Extraction 598 success
  plus two explicit fallbacks; embeddings available for 600.
- Leakage-safe split: 499 train / 101 newest-advertiser holdout; advertiser overlap zero.
- Accepted no-embedding model: MAE 0.3550 vs 0.3731 median baseline; top-20% precision 0.3333.
  Embeddings did not improve the holdout and are rejected from the production guidance path.
- Survival remains unevaluable on the holdout: zero explicit train endings and zero test endings.
  A prior report's 19 apparent train events came from misinterpreting the actor's active-ad
  `end_date` delivery-window boundary and is superseded. The full warehouse currently has only two
  explicitly inactive ads. Continue
  polling and preserve future windows; do not turn current Cox coefficients into directives.
- Only `other supplements` currently clears the 10-row segment metric gate, and the newest Q3
  cohort is shifted. Cross-subcategory and trend claims are therefore unsupported.
- The official Meta API's broad commercial coverage is UK/EU-specific; ordinary US commercial
  collection/polling remains dependent on the Apify actor and its changing web-source behavior.
