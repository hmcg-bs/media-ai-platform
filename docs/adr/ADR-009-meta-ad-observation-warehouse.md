# ADR-009: Append-only Meta ad observation warehouse and evidence-gated references

**Status:** Accepted
**Date:** 2026-09-12
**Relates to:** ADR-005, ADR-006, ADR-007, ADR-008
**Amends:** ADR-006's local-only/BigQuery-deferred v1 boundary for Meta ad model data

## Context

Commercial ads can disappear from Meta's public library after delivery stops. A current
scrape cannot reconstruct when a missing ad stopped, and absence can also be caused by a
partial scraper result. The existing one-window corpus therefore right-censors almost every
Longevity observation and cannot evaluate 30/60/90/180-day survival on a future holdout.
Concurrent scrape, Extraction, polling, and training jobs must not overwrite one another.

The official [Meta Ad Library API](https://www.facebook.com/ads/library/api) exposes broad
commercial-ad transparency for ads delivered to the UK/EU, but it is not a complete API for
ordinary US competitor commercial ads. The existing Apify browser actor remains the US
collection path. An actor result is third-party evidence, not an official performance metric.

## Decision

1. **Persist immutable observations, not mutable current rows.** BigQuery tables hold ad
   snapshots, Extraction/feature snapshots, lifecycle observations, and survival reports.
   Content-derived IDs make retries idempotent; readers deduplicate because BigQuery streaming
   insert de-duplication is best effort.
2. **Treat absence conservatively.** A returned stop/inactive field is explicit evidence. One
   missing result is not inactivity. Two successful, consecutive actor-run misses create an
   interval-censored inferred stop at the first miss. A page poll counts as complete only when the
   actor reports that it exhausted all source URLs and returned another ad from that page as a
   positive control. Result-capped/empty polls neither advance nor reset misses. This remains weaker
   than an explicit stop because the actor has no per-ad completeness proof.
3. **Evaluate Longevity as survival data.** Kaplan–Meier reports cover 30, 60, 90, and 180 days,
   including confidence intervals, known-outcome coverage, observed endings, and an explicit
   insufficient-evidence status. A horizon is evaluable only with at least 20 known outcomes and
   five endings. These are evidence gates, not universal success cutoffs.
4. **Gate Supplements with manual taxonomy labels.** Sampling strata improve review coverage but
   never become labels. Only `is_supplement: true` passes; false and unlabeled records fail closed.
5. **Keep trend windows frozen.** Each scrape's immutable `ingested_at` snapshot is retained.
   Before promoting a newly trained model, evaluate the previous model against ads first observed
   in the next scrape window. Do not fold that window into training until its evaluation is saved.
6. **Separate model directives from visual references.** SHAP/Cox guidance must pass holdout and
   directionality gates. Successful-ad images are a separate craft benchmark: proxy-ranked
   candidates require human approval, at most eight are archived, and they may only be used for
   comparison across the six documented craft dimensions—not copying.
7. **Archive images efficiently and immutably.** Optional references are auto-oriented, bounded to
   1600 px, encoded as WebP quality 82, stripped of metadata, content-addressed, and uploaded with
   generation preconditions. Manifests are also immutable and content-addressed.

## Consequences

- The warehouse preserves historical evidence without repeatedly paying to rediscover every ad.
- Polling costs Apify compute and still cannot prove the exact stop instant for a disappeared US
  commercial ad; inferred stops are interval approximations.
- Current one-window model findings remain provisional until repeated polls produce ended ads in a
  future advertiser/time holdout.
- BigQuery and GCS IAM are operational prerequisites; tokens remain exclusively in
  `pipeline.config.Settings` and are never serialized into artifacts.
- Object storage is cheap at current scale, but operations and egress are separate charges.
