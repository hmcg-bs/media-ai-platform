from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from pipeline import supplements_workflow


def test_product_enrichment_runs_between_taxonomy_and_matrix(monkeypatch, tmp_path: Path) -> None:
    ads_path = tmp_path / "ads.json"
    labels_path = tmp_path / "labels.json"
    taxonomy_report_path = tmp_path / "taxonomy-report.json"
    approved_path = tmp_path / "approved.json"
    enriched_path = tmp_path / "enriched.json"
    enrichment_report_path = tmp_path / "enrichment-report.json"
    diagnostics_path = tmp_path / "diagnostics.csv"
    matrix_path = tmp_path / "matrix.json"
    report_path = tmp_path / "training-report.json"
    guide_path = tmp_path / "guide.json"
    ads_path.write_text(json.dumps([{"ad_archive_id": "1", "link_url": "https://shop.test/p"}]))
    labels_path.write_text("{}")
    taxonomy_report_path.write_text("{}")
    approved = [{"ad_archive_id": "1", "link_url": "https://shop.test/p"}]

    monkeypatch.setattr(supplements_workflow, "verify_approved_taxonomy", lambda *_: None)
    monkeypatch.setattr(
        supplements_workflow,
        "apply_manual_taxonomy_gate",
        lambda *_: (approved, {"approved": 1}),
    )
    monkeypatch.setattr(
        supplements_workflow,
        "extract_generation_guide",
        lambda *_: SimpleNamespace(model_dump=lambda **_: {"visual_directives": []}),
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(module: str, *args: str) -> None:
        calls.append((module, args))
        if module == "ingestion.enrich_with_product_pages" and "--tiered" in args:
            enriched = [
                {
                    **approved[0],
                    "page_id": "brand-1",
                    "product_page": {
                        "price": 29.99,
                        "marketing_copy": "Daily support",
                        "extraction_method": "tier_1_json",
                    },
                }
            ]
            enriched_path.write_text(json.dumps(enriched))

    monkeypatch.setattr(supplements_workflow, "_run_module", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "supplements-workflow",
            "--ads",
            str(ads_path),
            "--taxonomy-labels",
            str(labels_path),
            "--taxonomy-report",
            str(taxonomy_report_path),
            "--taxonomy-approved-ads",
            str(approved_path),
            "--product-enriched-ads",
            str(enriched_path),
            "--product-enrichment-report",
            str(enrichment_report_path),
            "--product-enrichment-diagnostics",
            str(diagnostics_path),
            "--matrix",
            str(matrix_path),
            "--report",
            str(report_path),
            "--guide",
            str(guide_path),
            "--skip-scrape",
            "--skip-extraction",
            "--skip-embeddings",
        ],
    )

    supplements_workflow.main()

    modules = [module for module, _ in calls]
    assert modules[:3] == [
        "ingestion.enrich_with_product_pages",
        "ingestion.enrich_with_product_pages",
        "pipeline.feature_engineering.build_matrix",
    ]
    assert "--tiered" in calls[0][1]
    assert "--zenrows" in calls[1][1]
    assert json.loads(enrichment_report_path.read_text())["status"] == "passed"
