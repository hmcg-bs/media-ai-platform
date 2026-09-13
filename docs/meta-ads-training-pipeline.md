# Meta Ads Feature Model and Local Training Pipeline

This is the implemented Step 1 → Step 3 local workflow. It is deterministic
except for the paid Apify scrape and optional Replicate embeddings. No ROAS or
public engagement signal is available for the current commercial-ad corpus.

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

Only one process may own a given output path. A second writer fails fast on the
sidecar lock rather than corrupting a checkpoint. Concurrent jobs are supported
by choosing different `--out` paths.

## Feature matrix

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

## Training and evaluation

```bash
uv run python -m pipeline.model_training.run_training \
  --matrix data/feature_matrix_fresh.json \
  --ads data/supplements_fresh.json \
  --report data/model_training_report_fresh.json \
  --random-state 42 \
  --workers-per-model 1
```

The primary XGBoost attribution model uses an advertiser-group holdout, so a
Meta page cannot appear in both train and test. Reports include R², MAE, a
median baseline comparison, top-20%-precision, advertiser overlap, SHAP
attribution, seed, worker count, and SHA-256 hashes of both inputs. Component
models use an expanding time-series CV. Ads with no `end_date` are retained as
right-censored observations in the Cox Longevity model.

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
- The repository records Apify's monthly quota as currently exhausted. Offline
  tests verify orchestration, but a fresh paid scrape and real-corpus training
  run remain required before interpreting feature rankings.

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
