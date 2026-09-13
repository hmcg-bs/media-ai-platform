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

### Changed
- Aligned the competitor-ad Performance proxy to Longevity weighted by page-level Scaling with a Meta collation Variant boost
- Preserved extraction missingness and active ads as right-censored observations during model preparation

### Fixed
- Prevented same-advertiser train/test leakage and same-output concurrent job corruption
- URL-encoded Meta Ad Library search queries and rejected incompatible ingestion resumes

### Removed
- Target-derived cluster trend features from the default training CLI because their in-sample encoding leaked outcomes
