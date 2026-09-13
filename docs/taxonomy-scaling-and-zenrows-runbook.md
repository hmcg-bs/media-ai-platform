# Taxonomy scaling and ZenRows runbook

## Purpose and evidence boundary

The 120-ad human review is an untouched gold set for validating supplement/non-supplement
admission; it is not large enough to be the training corpus. A classifier may scale binary
admission only after passing the fixed gold-set gate. Human adjudication remains authoritative.
Classifier-admitted ads receive no supplement subcategory: category/segment evidence stays human
only. Taxonomy labels and product metadata are model inputs, never Meta performance outcomes.
Generated candidates and generation reviewer labels are prohibited from every step below.

The auto-admission threshold is confidence ≥0.80. On at least 100 gold rows with at least 10 rows
from each class and at least 20 auto-positive predictions, the one-sided admission safety check is
the 95% Wilson lower bound on high-confidence positive precision ≥0.90. These are conservative,
pre-registered engineering thresholds. The corresponding auto-admission recall lower bound must be
≥0.80 to limit severe selection bias from systematically excluding real supplement ads. These are
not literature-derived universal constants. Failure leaves all automated admission blocked.

## Sequential workflow

1. Complete `.amp/in/artifacts/supplements_taxonomy_review_v1.json`. Every row needs a primary
   decision; at least 20% need an independent second review; disagreements need an independent
   adjudicator. The existing κ ≥0.80 gate remains unchanged.
2. Validate the review. The report now retains a hash-bound full binary gold label set in addition
   to the positive-only approved-label file:

   ```bash
   uv run python -m pipeline.validation.taxonomy_adjudication validate \
     --sample .amp/in/artifacts/supplements_taxonomy_sample_120.json \
     --review .amp/in/artifacts/supplements_taxonomy_review_v1.json \
     --report data/supplements_taxonomy_adjudication_report.json \
     --approved-labels data/supplements_taxonomy_approved_labels.json
   ```

3. Classify the image-bearing corpus with the current provenance-emitting classifier. Its existing
   resume checkpoint contains all predictions, including negatives and ambiguous rows:

   ```bash
   uv run python -m pipeline.validation.phase0_validator apply \
     data/supplements_fresh.json --resume --workers=15
   ```

   The checkpoint is `data/supplements_filtered_classified_checkpoint.json`. Every new prediction
   binds classifier schema, provider/model, prompt SHA-256, and implementation SHA-256. Legacy
   checkpoints without that provenance fail closed and must not be silently upgraded.

4. Validate and scale taxonomy. This writes accepted ads, an explicit human review queue, and
   hash-bound validation/application reports:

   ```bash
   uv run python -m pipeline.validation.taxonomy_scaling \
     --ads data/supplements_fresh.json \
     --classified-ads data/supplements_filtered_classified_checkpoint.json \
     --adjudication-report data/supplements_taxonomy_adjudication_report.json \
     --validation-report data/supplements_taxonomy_classifier_validation.json \
     --accepted-ads data/supplements_taxonomy_approved.json \
     --review-queue data/supplements_taxonomy_review_queue.json \
     --application-report data/supplements_taxonomy_application_report.json
   ```

5. Review queued ambiguous/missing predictions and produce a new immutable human review version if
   greater coverage is needed. Never lower the fixed threshold after observing results.
6. Run product enrichment only after taxonomy passes. `pipeline.supplements_workflow` accepts the
   same full classifier checkpoint via `--taxonomy-classified-ads`, reruns the validation binding,
   then performs the free Shopify/structured pass followed by one resumable ZenRows backfill per
   unresolved unique URL. It stops before extraction/training if coverage fails.
7. Inspect extraction diagnostics and the field-specific row/advertiser support report. Persist to
   BigQuery/GCS only after those gates pass; absent price, rating, description, currency, or product
   identity remains missing—not zero and not inferred approval.

## Adoption and rollback

- No passing human report: no classifier validation.
- Gold precision lower bound below 0.90: no automated admissions and no ZenRows corpus run.
- Missing/mixed/tampered classifier provenance: reject the entire classifier artifact.
- Classifier-admitted rows: global analysis only; no taxonomy segment direction.
- Product coverage failure: quarantine the enriched corpus and do not train or persist it.
- Any later model promotion still requires advertiser/temporal isolation, clustered uncertainty,
  clean immutable provenance, and untouched future-window confirmation. Immutable v3 remains the
  selected fallback throughout; this workflow emits no generation handoff.
