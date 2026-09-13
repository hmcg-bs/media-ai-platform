"""Restartable Supplements scrape → extract → train → SHAP-guide workflow.

Paid/API stages remain separate subprocesses so each keeps its existing
checkpoint, logging, and output-lock behavior. Tokens are read only by
``pipeline.config.get_settings`` inside those stages and never appear in the
command line or workflow artifacts.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from pipeline.artifacts import atomic_write_json, exclusive_output
from pipeline.generation.guide import extract_generation_guide


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
        "--reprocess-ocr", action="store_true",
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

        sample_args = ["--sample-size", str(args.sample_size)] if args.sample_size else []
        if not args.skip_extraction:
            ocr_args = ["--skip-ocr"] if args.skip_ocr else []
            repair_ocr_args = ["--reprocess-ocr"] if args.reprocess_ocr else []
            _run_module(
                "ingestion.run_step2_pipeline",
                "--ads", str(args.ads), "--out", str(args.step2_out),
                "--cognitive-provider", "replicate", *ocr_args,
                "--reprocess-incomplete-cognitive",
                *repair_ocr_args,
                "--concurrency", str(args.concurrency), *sample_args,
            )
        _run_module(
            "pipeline.feature_engineering.build_matrix",
            "--ads", str(args.ads), "--step2-out", str(args.step2_out),
            "--out", str(args.matrix),
            *(["--skip-embeddings"] if args.skip_embeddings else []),
            *sample_args,
        )
        _run_module(
            "pipeline.model_training.run_training",
            "--matrix", str(args.matrix), "--ads", str(args.ads),
            "--report", str(args.report), "--workers-per-model", "1",
        )
        guide = extract_generation_guide(args.report)
        atomic_write_json(args.guide, guide.model_dump(mode="json"))
        print(f"Workflow complete: report={args.report} guide={args.guide}")


if __name__ == "__main__":
    main()
