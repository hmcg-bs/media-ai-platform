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
from pipeline.validation.product_enrichment import build_product_enrichment_report
from pipeline.validation.taxonomy_adjudication import verify_approved_taxonomy
from pipeline.validation.taxonomy_scaling import (
    apply_validated_classifier,
    build_classifier_validation_report,
)


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
        required=True,
        help="Human-adjudicated Supplements labels keyed by ad_id.",
    )
    parser.add_argument(
        "--taxonomy-report",
        type=Path,
        required=True,
        help="Passing adjudication report cryptographically binding --taxonomy-labels.",
    )
    parser.add_argument(
        "--taxonomy-approved-ads",
        type=Path,
        default=Path("data/supplements_taxonomy_approved.json"),
    )
    parser.add_argument(
        "--taxonomy-classified-ads",
        type=Path,
        help=(
            "Full classifier checkpoint. Required when human labels do not cover the full corpus; "
            "its gold-sample predictions must pass the fixed scaling gate."
        ),
    )
    parser.add_argument(
        "--taxonomy-classifier-validation-report",
        type=Path,
        default=Path("data/supplements_taxonomy_classifier_validation.json"),
    )
    parser.add_argument(
        "--taxonomy-review-queue",
        type=Path,
        default=Path("data/supplements_taxonomy_review_queue.json"),
    )
    parser.add_argument(
        "--taxonomy-application-report",
        type=Path,
        default=Path("data/supplements_taxonomy_application_report.json"),
    )
    parser.add_argument(
        "--product-enriched-ads",
        type=Path,
        default=Path("data/supplements_taxonomy_approved_product_enriched.json"),
        help="Restartable, taxonomy-approved corpus enriched from product sites.",
    )
    parser.add_argument(
        "--product-enrichment-report",
        type=Path,
        default=Path("data/supplements_product_enrichment_report.json"),
    )
    parser.add_argument(
        "--product-enrichment-diagnostics",
        type=Path,
        default=Path("data/supplements_product_enrichment_zenrows.csv"),
    )
    parser.add_argument("--product-enrichment-workers", type=int, default=4)
    parser.add_argument(
        "--skip-product-enrichment",
        action="store_true",
        help="Reuse and validate --product-enriched-ads without making product-site calls.",
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
    if args.product_enrichment_workers < 1:
        parser.error("--product-enrichment-workers must be at least 1")
    if args.skip_ocr and args.reprocess_ocr:
        parser.error("--skip-ocr and --reprocess-ocr cannot be used together")
    lock_target = args.report.with_suffix(args.report.suffix + ".workflow")
    with (
        exclusive_output(lock_target),
        exclusive_output(args.taxonomy_approved_ads),
        exclusive_output(args.taxonomy_classifier_validation_report),
        exclusive_output(args.taxonomy_review_queue),
        exclusive_output(args.taxonomy_application_report),
        exclusive_output(args.product_enrichment_report),
    ):
        if not args.skip_scrape:
            _run_module("ingestion.fresh_corpus_scrape", "--out", str(args.ads))
        if not args.ads.exists():
            parser.error(f"ads corpus not found: {args.ads}")

        if not args.taxonomy_labels.exists():
            parser.error(f"taxonomy labels not found: {args.taxonomy_labels}")
        if not args.taxonomy_report.exists():
            parser.error(f"taxonomy report not found: {args.taxonomy_report}")
        ads = json.loads(args.ads.read_text())
        labels = json.loads(args.taxonomy_labels.read_text())
        taxonomy_report = json.loads(args.taxonomy_report.read_text())
        try:
            verify_approved_taxonomy(taxonomy_report, labels)
        except ValueError as exc:
            parser.error(str(exc))
        labelled_ids = {
            str(row.get("ad_id") or "") for row in taxonomy_report.get("gold_labels", [])
        }
        corpus_ids = {str(ad.get("ad_archive_id") or "") for ad in ads}
        if corpus_ids <= labelled_ids:
            approved, counts = apply_manual_taxonomy_gate(ads, labels, taxonomy_report)
            atomic_write_json(args.taxonomy_classifier_validation_report, {
                "status": "not_required_full_human_coverage",
            })
            atomic_write_json(args.taxonomy_review_queue, [])
            atomic_write_json(args.taxonomy_application_report, {
                "status": "passed",
                "mode": "full_human_coverage",
                "counts": counts,
            })
        else:
            if args.taxonomy_classified_ads is None:
                parser.error(
                    "human taxonomy does not cover the full corpus; provide "
                    "--taxonomy-classified-ads so the gold-validated scaling gate can run"
                )
            if not args.taxonomy_classified_ads.exists():
                parser.error(
                    f"classified taxonomy corpus not found: {args.taxonomy_classified_ads}"
                )
            classified_ads = json.loads(args.taxonomy_classified_ads.read_text())
            classifier_report = build_classifier_validation_report(
                classified_ads, taxonomy_report
            )
            atomic_write_json(args.taxonomy_classifier_validation_report, classifier_report)
            if classifier_report["status"] != "passed":
                parser.error(
                    "taxonomy classifier validation failed: "
                    + "; ".join(classifier_report["failures"][:5])
                )
            approved, review_queue, application_report = apply_validated_classifier(
                ads,
                classified_ads,
                taxonomy_report,
                classifier_report,
            )
            counts = application_report["outcomes"]
            atomic_write_json(args.taxonomy_review_queue, review_queue)
            atomic_write_json(args.taxonomy_application_report, application_report)
        if not approved:
            parser.error("manual taxonomy gate approved zero ads")
        atomic_write_json(args.taxonomy_approved_ads, approved)
        workflow_ads = args.taxonomy_approved_ads
        print(f"Manual taxonomy gate: {counts}; approved={workflow_ads}")

        if not args.skip_product_enrichment:
            # First collect structured Shopify/HTML evidence without an LLM;
            # then use paid JS-rendered ZenRows only as the resilient backfill.
            # Each subprocess owns and atomically checkpoints the output.
            _run_module(
                "ingestion.enrich_with_product_pages",
                "--ads",
                str(workflow_ads),
                "--out",
                str(args.product_enriched_ads),
                "--workers",
                str(args.product_enrichment_workers),
                "--tiered",
                "--no-llm",
                "--resume",
            )
            _run_module(
                "ingestion.enrich_with_product_pages",
                "--ads",
                str(args.product_enriched_ads),
                "--out",
                str(args.product_enriched_ads),
                "--zenrows",
                "--resume",
                "--diagnostics-csv",
                str(args.product_enrichment_diagnostics),
            )
        if not args.product_enriched_ads.exists():
            parser.error(f"product-enriched corpus not found: {args.product_enriched_ads}")
        enriched_ads = json.loads(args.product_enriched_ads.read_text())
        approved_ids = {str(ad.get("ad_archive_id")) for ad in approved}
        enriched_ids = {str(ad.get("ad_archive_id")) for ad in enriched_ads}
        if len(enriched_ads) != len(approved) or enriched_ids != approved_ids:
            parser.error(
                "product-enriched corpus does not match the current taxonomy-approved corpus; "
                "rerun without --skip-product-enrichment"
            )
        enrichment_report = build_product_enrichment_report(enriched_ads)
        atomic_write_json(args.product_enrichment_report, enrichment_report)
        if enrichment_report["status"] != "passed":
            parser.error(
                "product enrichment coverage gate failed: "
                + "; ".join(enrichment_report["failures"][:5])
            )
        workflow_ads = args.product_enriched_ads
        print(
            "Product enrichment gate passed: "
            f"linked={enrichment_report['n_linked_ads']} "
            f"methods={enrichment_report['extraction_methods']}"
        )

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
                str(workflow_ads),
                "--step2-out",
                str(args.step2_out),
                "--matrix",
                str(args.matrix),
                *dataset_args,
            )
        print(f"Workflow complete: report={args.report} guide={args.guide}")


if __name__ == "__main__":
    main()
