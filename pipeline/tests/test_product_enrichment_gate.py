from __future__ import annotations

from pipeline.validation.product_enrichment import build_product_enrichment_report


def _ads(n: int, enriched: int) -> list[dict]:
    rows = []
    for index in range(n):
        page = None
        if index < enriched:
            page = {
                "price": 29.99,
                "marketing_copy": "A complete product description",
                "rating": 4.7 if index < 20 else None,
                "rating_count": 100 if index < 20 else None,
                "variants_featured": ["Berry"] if index < 20 else [],
                "extraction_method": "tier_1_json+zenrows",
            }
        rows.append(
            {
                "ad_archive_id": str(index),
                "page_id": f"brand-{index % 20}",
                "link_url": f"https://shop.example/{index}",
                "product_page": page,
            }
        )
    return rows


def test_product_enrichment_gate_reports_field_support_and_provenance():
    report = build_product_enrichment_report(_ads(200, 120))
    assert report["status"] == "passed"
    assert report["fields"]["price"]["linked_ad_coverage"] == 0.6
    assert report["fields"]["price"]["model_support"] is True
    assert report["fields"]["rating"]["model_support"] is False
    assert len(report["ads_content_sha256"]) == 64


def test_product_enrichment_gate_fails_closed_on_sparse_pages():
    report = build_product_enrichment_report(_ads(200, 20))
    assert report["status"] == "failed"
    assert any("product_page coverage" in failure for failure in report["failures"])
    assert any("price coverage" in failure for failure in report["failures"])


def test_missing_values_are_not_counted_as_real_zeroes():
    ads = _ads(1, 1)
    ads[0]["product_page"].update({"price": None, "rating": None, "rating_count": None})
    report = build_product_enrichment_report(ads)
    assert report["fields"]["price"]["n_rows"] == 0
    assert report["fields"]["rating"]["n_rows"] == 0
