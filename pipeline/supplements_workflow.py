"""Restartable Supplements scrape → extract → train → SHAP-guide workflow.

Paid/API stages remain separate subprocesses so each keeps its existing
checkpoint, logging, and output-lock behavior. Tokens are read only by
``pipeline.config.get_settings`` inside those stages and never appear in the
command line or workflow artifacts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.generation.guide import extract_generation_guide
from pipeline.validation.phase0_validator import apply_manual_taxonomy_gate


def _run_module(module: str, *args: str) -> None:
    subprocess.run([sys.executable, "-m", module, *args], check=True)  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Supplements model-data workflow.")
    parser.add_argument("--ads", type=Path, default=Path("data/supplements_fresh.json"))
    parser.add_argument("--step2-out", type=Path, default=Path("out/step2-supplements"))
    parser.add_argument("--matrix", type=Path, default=Path("data/supplements_feature_matrix.json"))
    parser.add_argument(
        "--report", type=Path, default=Path("data/supplements_training_report.json")
    )
    parser.add_argument(
        "--guide", type=Path, default=Path("data/supplements_generation_guide.json")
    )
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--skip-scrape", action="store_true")
    parser.add_argument("--skip-extraction", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-embeddings", action="store_true")
    parser.add_argument(
        "--taxonomy-labels",
        type=Path,
        help="Manual labels keyed by ad_id; when set, unlabeled/non-supplement ads fail closed.",
    )
    parser.add_argument(
        "--taxonomy-approved-ads",
        type=Path,
        default=Path("data/supplements_taxonomy_approved.json"),
    )
    parser.add_argument("--sync-gcp", action="store_true")
    parser.add_argument("--bigquery-dataset")
    parser.add_argument(
        "--reprocess-ocr",
        action="store_true",
        help="Backfill OCR for existing Step 2 artifacts without repeating cognitive calls.",
    )
    args = parser.parse_args()

    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.skip_ocr and args.reprocess_ocr:
        parser.error("--skip-ocr and --reprocess-ocr cannot be used together")
    lock_target = args.report.with_suffix(args.report.suffix + ".workflow")
    with exclusive_output(lock_target):
        if not args.skip_scrape:
            _run_module("ingestion.fresh_corpus_scrape", "--out", str(args.ads))
        if not args.ads.exists():
            parser.error(f"ads corpus not found: {args.ads}")

        workflow_ads = args.ads
        if args.taxonomy_labels:
            if not args.taxonomy_labels.exists():
                parser.error(f"taxonomy labels not found: {args.taxonomy_labels}")
            ads = json.loads(args.ads.read_text())
            labels = json.loads(args.taxonomy_labels.read_text())
            approved, counts = apply_manual_taxonomy_gate(ads, labels)
            if not approved:
                parser.error(
                    "manual taxonomy gate approved zero ads; fill is_supplement labels first"
                )
            atomic_write_json(args.taxonomy_approved_ads, approved)
            workflow_ads = args.taxonomy_approved_ads
            print(f"Manual taxonomy gate: {counts}; approved={workflow_ads}")

        sample_args = ["--sample-size", str(args.sample_size)] if args.sample_size else []
        if not args.skip_extraction:
            ocr_args = ["--skip-ocr"] if args.skip_ocr else []
            repair_ocr_args = ["--reprocess-ocr"] if args.reprocess_ocr else []
            _run_module(
                "ingestion.run_step2_pipeline",
                "--ads",
                str(workflow_ads),
                "--out",
                str(args.step2_out),
                "--cognitive-provider",
                "replicate",
                *ocr_args,
                "--reprocess-incomplete-cognitive",
                *repair_ocr_args,
                "--concurrency",
                str(args.concurrency),
                *sample_args,
            )
        _run_module(
            "pipeline.feature_engineering.build_matrix",
            "--ads",
            str(workflow_ads),
            "--step2-out",
            str(args.step2_out),
            "--out",
            str(args.matrix),
            *(["--skip-embeddings"] if args.skip_embeddings else []),
            *sample_args,
        )
        _run_module(
            "pipeline.model_training.run_training",
            "--matrix",
            str(args.matrix),
            "--ads",
            str(workflow_ads),
            "--report",
            str(args.report),
            "--workers-per-model",
            "1",
        )
        guide = extract_generation_guide(args.report)
        atomic_write_json(args.guide, guide.model_dump(mode="json"))
        if args.sync_gcp:
            dataset_args = ["--dataset", args.bigquery_dataset] if args.bigquery_dataset else []
            _run_module(
                "ingestion.sync_gcp",
                "--ads",
                str(args.ads),
                "--step2-out",
                str(args.step2_out),
                "--matrix",
                str(args.matrix),
                *dataset_args,
            )
        print(f"Workflow complete: report={args.report} guide={args.guide}")


if __name__ == "__main__":
    main()
