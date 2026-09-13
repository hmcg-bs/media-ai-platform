# Meta Ads Feature Model and Local Training Pipeline

This is the implemented Step 1 → Step 3 local workflow. It is deterministic
except for the paid Apify scrape and Replicate vision/embeddings. No ROAS or
public engagement signal is available for the current commercial-ad corpus.

The complete Supplements workflow is resumable and requires a passing human-taxonomy report bound
to the approved labels:

```bash
uv run python -m pipeline.supplements_workflow \
  --taxonomy-labels data/supplements_taxonomy_approved_v1.json \
  --taxonomy-report data/supplements_taxonomy_adjudication_report_v1.json
```

Use `--sample-size 20` for an end-to-end paid pilot. The workflow owns one
lock, and each corpus, Extraction directory, feature matrix, and report also
has its own atomic lock/atomic writes. API tokens come only from `get_settings()`
and are never command-line arguments or artifact fields.

## Performance proxy

The target is a bounded percentile score:

```
0.70 × longevity_rank
+ 0.20 × longevity_rank × scaling_rank
+ 0.10 × variant_rank
```

- **Longevity**: `days_active`.
- **Scaling**: ads captured for the same non-empty Meta `page_id` in the full
  active-search corpus. It is computed before sampling. Unknown Page IDs each
  receive the conservative fallback 1; they are never grouped together.
- **Variant**: Meta/Apify `collation_count`.

Ranks, with a log transform for Scaling and Variant, limit outlier influence
without pretending that public data contains spend or conversion precision.
Landing-page `variants_featured_count` describes SKUs/bundles and is a model
feature only; it is not a Performance ingredient.

## Ingestion and checkpoints

```bash
uv run python -m ingestion.fresh_corpus_scrape \
  --out data/supplements_fresh.json
```

The command uses `get_settings()` for `APIFY_API_TOKEN`; neither output nor
logs contain the token. It URL-encodes Meta search parameters, deduplicates by
`ad_archive_id`, atomically checkpoints after every successful query, and
records completed queries in `<out>.state.json`. A restart resumes without
re-running completed paid queries. One query failure is logged and left
incomplete so a later invocation retries it.

Each ad retains every discovery `search_queries` match. This is corpus provenance, not human product
truth or a model input. Only rows approved by the versioned two-reviewer/adjudication workflow enter
Extraction/training. The adjudicated subcategory is the evaluation segment; query and deterministic
copy heuristics are legacy fallbacks only.

Only one process may own a given output path. A second writer fails fast on the
sidecar lock rather than corrupting a checkpoint. Concurrent jobs are supported
by choosing different `--out` paths.

## Feature matrix

Step 2 runs deterministic metadata/color plus Replicate cognitive Extraction:

```bash
uv run python -m ingestion.run_step2_pipeline \
  --ads data/supplements_fresh.json \
  --out out/step2-supplements \
  --cognitive-provider replicate \
  --reprocess-incomplete-cognitive --concurrency 4
```

Stage 2 uses Cloud Vision `document_text_detection` through `vision_client.py`.
Use `--skip-ocr` only when GCP ADC is unavailable; it does not fabricate
typography/copy-layout values, and their missingness remains visible to the
model. After restoring ADC, add `--reprocess-ocr` to backfill OCR and
OCR-masked color on existing artifacts without repeating paid cognitive calls.
The output directory is locked to prevent concurrent jobs duplicating paid
calls, and each ad JSON is atomically published.
The repair flag preserves successful metadata/color/marketing fields and
reruns only the deep cognitive stage when an older artifact is structurally
empty.

```bash
uv run python -m pipeline.feature_engineering.build_matrix \
  --ads data/supplements_fresh.json \
  --step2-out out/step2-fresh \
  --out data/feature_matrix_fresh.json
```

The matrix carries `page_id` for grouped evaluation and
`brand_scaling_count` for the proxy, but both are excluded from model inputs.
`has_product_page` and `has_step2_features`, plus numeric missing-value
indicators, distinguish absent extraction stages from genuine zero values.
Existing checkpoints are schema-refreshed with page/scaling metadata on resume.
Use `--skip-embeddings` when Replicate embedding capacity is unavailable; the
matrix records empty embedding vectors, training omits the embedding ablation,
and the deterministic/cognitive feature model remains fully evaluable.

## Training and evaluation

```bash
uv run python -m pipeline.model_training.run_training \
  --matrix data/feature_matrix_fresh.json \
  --ads data/supplements_fresh.json \
  --report data/model_training_report_fresh.json \
  --random-state 42 \
  --workers-per-model 1
```

The primary XGBoost attribution model holds out advertisers first seen latest,
so a Meta page cannot appear in both train and test and the test set better
represents transfer to new brands under newer trends. The proxy percentile
calibrator is fitted on training rows only. A second such split within training
selects XGBoost parameters; the untouched outer test set is evaluated once.
Categorical levels occurring fewer than five times in the training fold are
pooled before one-hot encoding. This prevents near-unique cognitive descriptions
(for example free-text texture and product-state values) from creating thousands
of advertiser-specific columns; holdout values and frequencies are never used
to form the pool.
Reports include R², MAE, a median baseline comparison, top-20%-precision, paired bootstrap
uncertainty, calibration, descriptive feature drift, advertiser/segment support, SHAP attribution,
taxonomy status, an objective promotion decision, seed, worker count, and SHA-256 hashes of both
inputs. Component models use an expanding time-series CV. Ads with no observed lifecycle ending are
retained as right-censored observations in the Cox Longevity model; an active actor `end_date` is not
an ending.

The report also includes test MAE/calibration by start quarter and supplement
subcategory, with a minimum-sample gate, plus Kaplan–Meier survival estimates
at 30/60/90/180 days. `days_to_75pct_survival` and median survival remain null
when censoring makes them unidentifiable. These are maturity benchmarks, not a
universal fabricated definition of success.

The final report embeds interpretable Tree SHAP values. Generation reads that
same report directly and emits `supplements_generation_guide.json`; only
directionally reliable, renderable features become directives. Embedding
dimensions, missing-data/pooled-rare levels, and campaign-operation fields are
excluded.
Cox coefficients are also excluded whenever the survival holdout has no
admissible event pairs; a fitted but unevaluable longevity model must not steer
generation.

`--workers-per-model 1` is deliberate: separate jobs can run concurrently
without each XGBoost process claiming every CPU. Use distinct report paths for
concurrent experiments; same-path jobs are rejected by the output lock.

## Data and API constraints

- Meta's public commercial Ad Library does not expose ROAS, likes, shares, or
  reliable impression/spend ranges for this US corpus. The model must not claim
  those outcomes.
- Scaling is only meaningful when ingestion captures a page's active ads
  comprehensively. Keyword discovery is useful for corpus breadth but can
  undercount a brand; use verified Facebook page URLs/Page IDs as scrape inputs
  when comparing named brands.
- Facebook CDN creative URLs expire. Refreshing them requires a successful
  Apify run near use time.
- Search-query cohorts overlap and keyword discovery is not a taxonomy. Treat
  subcategory comparisons as sensitivity analysis until landing-page product
  categorization coverage is high.
- Trend evaluation can reveal calibration drift but cannot establish causality;
  preserve future collection windows as new untouched tests.

## Verification

Offline checks:

```bash
uv run pytest pipeline/tests ingestion/tests -q
uv run ruff check ingestion pipeline
```

Do not compare reports unless their input hashes and proxy definition match.
Feature importance is correlational and confounded by brand, category, and
collection coverage; the grouped holdout reduces brand leakage but cannot turn
the proxy into conversion ground truth.
