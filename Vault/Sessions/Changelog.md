# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffold and directory structure
- CLAUDE.md with comprehensive instructions
- Environment variable templates (.env.example)
- Obsidian vault reorganization (Structure/ vs Sessions/)
- Amp orb lifecycle scripts that install the locked Python 3.12 development environment
- Resumable Meta Ad Library ingestion checkpoints with atomic, collision-safe artifact writes
- Reproducible Meta Performance-proxy reports with advertiser-group evaluation and input hashes
- Meta Ads training and data-pipeline operations guide
- Restartable Supplements scrape-to-SHAP workflow with nested advertiser/time tuning and segment evaluation
- Censoring-aware Kaplan–Meier maturity benchmarks and supplement-subcategory provenance
- Deep visual product, prop, relationship, texture, and human feature representation
- Cloud Vision OCR/color backfill that preserves completed cognitive extraction
- Capacity-safe no-embedding evaluation mode
- Keyless Amp OIDC → Google Workload Identity Federation authentication for orbs
- Atomic, idempotent external-account ADC lifecycle generation and contract tests

### Changed
- Aligned the competitor-ad Performance proxy to Longevity weighted by page-level Scaling with a Meta collation Variant boost
- Preserved extraction missingness and active ads as right-censored observations during model preparation
- Generation guidance now consumes the integrated training report and excludes unevaluable survival coefficients
- Expanded the Supplements advertiser holdout from 35 to 101 ads
- GCP auth health reporting now distinguishes renewable WIF from user ADC
- Rare cognitive category levels are pooled using training-fold frequencies,
  reducing brittle singleton features without leaking holdout vocabulary
- Generation guidance now excludes pooled rare-category levels
- Validated keyless WIF against live Cloud Vision and backfilled OCR for the
  complete seeded 600-ad Supplements sample

### Fixed
- Prevented same-advertiser train/test leakage and same-output concurrent job corruption
- URL-encoded Meta Ad Library search queries and rejected incompatible ingestion resumes
- Sent Replicate the required nested Pydantic schema and added targeted repair for incomplete cognitive artifacts
- Honored Replicate low-credit rate-limit reset hints and kept seeded sample resumes exact
- Corrected the documented WIF provider to the deployed `amp-provider` resource

### Removed
- Target-derived cluster trend features from the default training CLI because their in-sample encoding leaked outcomes
