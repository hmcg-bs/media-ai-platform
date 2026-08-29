"""Tests for Stage 4d enrichment utility."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ingestion.enrich_with_product_pages import (
    _merge_product_pages,
    enrich_corpus,
    enrich_corpus_advertorial_fallback,
    enrich_corpus_parallel_tiered,
)
from ingestion.models import CompetitorAd
from ingestion.product_page import ProductPage


def test_enrich_corpus_missing_file(tmp_path: Path) -> None:
    """Test handling of missing ads file."""
    ads_file = tmp_path / "nonexistent.json"
    out_file = tmp_path / "enriched.json"

    result = enrich_corpus(ads_file, out_file)
    assert result == 1


def test_enrich_corpus_invalid_json(tmp_path: Path) -> None:
    """Test handling of invalid JSON."""
    ads_file = tmp_path / "ads.json"
    ads_file.write_text("{invalid json}")

    out_file = tmp_path / "enriched.json"
    result = enrich_corpus(ads_file, out_file)
    assert result == 1


def test_enrich_corpus_skips_no_link_url(tmp_path: Path) -> None:
    """Test that ads without link_url are skipped."""
    ad = CompetitorAd(page_name="Test Page", link_url="")
    ads_file = tmp_path / "ads.json"
    ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

    out_file = tmp_path / "enriched.json"
    result = enrich_corpus(ads_file, out_file, use_llm=False)

    assert result == 0
    enriched = json.loads(out_file.read_text())
    assert len(enriched) == 1
    assert enriched[0]["product_page"] is None


def test_enrich_corpus_enriches_with_product_page(tmp_path: Path) -> None:
    """Test successful enrichment of ads with product data."""
    ad = CompetitorAd(page_name="Test Page", link_url="https://example.com/product")
    ads_file = tmp_path / "ads.json"
    ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

    mock_product = ProductPage(
        product_name="Test Product",
        brand_name="Test Brand",
        price=29.99,
        extraction_method="structured_data",
        confidence=0.9,
        url="https://example.com/product",
    )

    with patch("ingestion.enrich_with_product_pages.scrape_landing_page") as mock_scrape:
        with patch("ingestion.enrich_with_product_pages.extract_product_page") as mock_extract:
            mock_scrape.return_value = "<html>test</html>"
            mock_extract.return_value = mock_product

            out_file = tmp_path / "enriched.json"
            result = enrich_corpus(ads_file, out_file, use_llm=False)

    assert result == 0
    enriched = json.loads(out_file.read_text())
    assert len(enriched) == 1
    assert enriched[0]["product_page"] is not None
    assert enriched[0]["product_page"]["product_name"] == "Test Product"
    assert enriched[0]["product_page"]["brand_name"] == "Test Brand"


def test_enrich_corpus_continues_on_scrape_failure(tmp_path: Path) -> None:
    """Test that corpus enrichment continues if scraping fails for one ad."""
    ad1 = CompetitorAd(page_name="Page 1", link_url="https://example.com/1")
    ad2 = CompetitorAd(page_name="Page 2", link_url="https://example.com/2")
    ads_file = tmp_path / "ads.json"
    ads_file.write_text(
        json.dumps([ad1.model_dump(mode="json"), ad2.model_dump(mode="json")])
    )

    mock_product = ProductPage(
        product_name="Product 2",
        extraction_method="structured_data",
        url="https://example.com/2",
    )

    with patch("ingestion.enrich_with_product_pages.scrape_landing_page") as mock_scrape:
        with patch("ingestion.enrich_with_product_pages.extract_product_page") as mock_extract:
            # First ad fails to scrape, second succeeds
            mock_scrape.side_effect = [None, "<html>test</html>"]
            mock_extract.return_value = mock_product

            out_file = tmp_path / "enriched.json"
            result = enrich_corpus(ads_file, out_file, use_llm=False)

    assert result == 0
    enriched = json.loads(out_file.read_text())
    assert len(enriched) == 2
    # First ad not enriched
    assert enriched[0]["product_page"] is None
    # Second ad enriched
    assert enriched[1]["product_page"]["product_name"] == "Product 2"


class TestEnrichCorpusParallelTiered:
    """Tests for the tiered+rate-limited+URL-deduped parallel path, built
    after enrich_corpus_parallel's naive scraping tripped Shopify's shared
    rate limit at scale. scrape_and_extract is imported locally inside
    enrich_corpus_parallel_tiered, so it's patched at its source
    (ingestion.tiered_scraper) rather than at the caller's module — a local
    `from x import y` still does a fresh module-attribute lookup at call
    time, so patching the source module works correctly here."""

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "nonexistent.json"
        out_file = tmp_path / "enriched.json"
        result = enrich_corpus_parallel_tiered(ads_file, out_file)
        assert result == 1

    def test_dedup_single_fetch_for_shared_url(self, tmp_path: Path) -> None:
        """Two ads sharing one link_url should produce two independent
        enriched output entries but trigger only one underlying fetch —
        this is the core fix for the 2,736-ads-but-985-unique-URLs finding."""
        ad1 = CompetitorAd(page_name="Page A", link_url="https://store.com/products/shared")
        ad2 = CompetitorAd(page_name="Page B", link_url="https://store.com/products/shared")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad1.model_dump(mode="json"), ad2.model_dump(mode="json")]))

        mock_product = ProductPage(
            product_name="Shared Product",
            extraction_method="shopify_json",
            confidence=0.9,
            url="https://store.com/products/shared",
        )

        with patch("ingestion.tiered_scraper.scrape_and_extract") as mock_scrape_extract:
            mock_scrape_extract.return_value = mock_product
            out_file = tmp_path / "enriched.json"
            result = enrich_corpus_parallel_tiered(
                ads_file, out_file, use_llm=False, max_workers=2
            )

        assert result == 0
        assert mock_scrape_extract.call_count == 1  # deduped — one fetch for both ads
        enriched = json.loads(out_file.read_text())
        assert len(enriched) == 2
        assert enriched[0]["product_page"]["product_name"] == "Shared Product"
        assert enriched[1]["product_page"]["product_name"] == "Shared Product"
        # each ad keeps its own identity — dedup only shares the product_page
        assert enriched[0]["page_name"] == "Page A"
        assert enriched[1]["page_name"] == "Page B"

    def test_none_result_leaves_product_page_null(self, tmp_path: Path) -> None:
        ad = CompetitorAd(page_name="Dead Link", link_url="https://gone.example.com/products/x")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

        with patch("ingestion.tiered_scraper.scrape_and_extract") as mock_scrape_extract:
            mock_scrape_extract.return_value = None
            out_file = tmp_path / "enriched.json"
            result = enrich_corpus_parallel_tiered(ads_file, out_file, use_llm=False)

        assert result == 0
        enriched = json.loads(out_file.read_text())
        assert enriched[0]["product_page"] is None

    def test_ad_without_link_url_skipped_without_fetch(self, tmp_path: Path) -> None:
        ad = CompetitorAd(page_name="No Link", link_url="")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

        with patch("ingestion.tiered_scraper.scrape_and_extract") as mock_scrape_extract:
            out_file = tmp_path / "enriched.json"
            result = enrich_corpus_parallel_tiered(ads_file, out_file, use_llm=False)

        assert result == 0
        mock_scrape_extract.assert_not_called()
        enriched = json.loads(out_file.read_text())
        assert enriched[0]["product_page"] is None

    def test_resume_seeds_url_cache_for_ad_not_in_prior_run(self, tmp_path: Path) -> None:
        """An ad sharing a link_url with an already-enriched ad from a prior
        (interrupted) run should be served from the resume cache even if
        that specific ad wasn't itself in the prior run's output."""
        shared_url = "https://store.com/products/already-done"
        prior_ad = CompetitorAd(ad_archive_id="prior_1", page_name="Prior Ad", link_url=shared_url)
        prior_ad.product_page = ProductPage(
            product_name="Already Enriched",
            extraction_method="shopify_json",
            confidence=0.9,
            url=shared_url,
        )

        out_file = tmp_path / "enriched.json"
        out_file.write_text(json.dumps([prior_ad.model_dump(mode="json")]))

        new_ad = CompetitorAd(ad_archive_id="new_1", page_name="New Ad Same URL", link_url=shared_url)
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(
            json.dumps([prior_ad.model_dump(mode="json"), new_ad.model_dump(mode="json")])
        )

        with patch("ingestion.tiered_scraper.scrape_and_extract") as mock_scrape_extract:
            result = enrich_corpus_parallel_tiered(
                ads_file, out_file, use_llm=False, resume=True, checkpoint_path=out_file
            )

        assert result == 0
        mock_scrape_extract.assert_not_called()  # fully served from resume cache
        enriched = json.loads(out_file.read_text())
        assert len(enriched) == 2
        assert enriched[0]["product_page"]["product_name"] == "Already Enriched"
        assert enriched[1]["product_page"]["product_name"] == "Already Enriched"

    def test_checkpoint_written_during_run(self, tmp_path: Path) -> None:
        ad1 = CompetitorAd(page_name="A", link_url="https://store.com/products/a")
        ad2 = CompetitorAd(page_name="B", link_url="https://store.com/products/b")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad1.model_dump(mode="json"), ad2.model_dump(mode="json")]))

        mock_product = ProductPage(
            product_name="Checkpointed Product", extraction_method="shopify_json", confidence=0.9
        )

        checkpoint_file = tmp_path / "checkpoint.json"
        with patch("ingestion.tiered_scraper.scrape_and_extract") as mock_scrape_extract:
            mock_scrape_extract.return_value = mock_product
            result = enrich_corpus_parallel_tiered(
                ads_file,
                tmp_path / "final.json",
                use_llm=False,
                checkpoint_path=checkpoint_file,
                checkpoint_every=1,
            )

        assert result == 0
        assert checkpoint_file.exists()
        checkpointed = json.loads(checkpoint_file.read_text())
        assert len(checkpointed) == 2


_LLM_PATCH_TARGET = "ingestion.llm_fallback.ReplicateVisionClient.extract_structured_text"


class _FakeAdvertorialResponse:
    def __init__(self, html: str) -> None:
        self.status_code = 200
        self.text = html


class _FakeAdvertorialZenRowsClient:
    """Injected in place of the real zenrows.ZenRowsClient — never constructed
    for real here. Returns pre-scripted HTML per URL."""

    def __init__(self, html_by_url: dict[str, str]) -> None:
        self._html_by_url = html_by_url

    async def get_async(self, url: str, params: dict | None = None) -> _FakeAdvertorialResponse:
        return _FakeAdvertorialResponse(self._html_by_url.get(url, "<html></html>"))


class TestMergeProductPages:
    """Regression coverage for the near-dup full-overwrite bug: 22 fields
    across real ads (product_category/brand_name/product_name/
    variants_featured) were silently wiped in the Phase 0.5f Pass B run
    because dup_match replaced the existing page outright instead of
    filling only its gaps."""

    def test_base_fields_win_over_other(self) -> None:
        base = ProductPage(
            url="https://a.com",
            product_category="Supplements",
            brand_name="Uvora",
            product_name="Real Product Name",
            variants_featured=["Variant: A", "Variant: B"],
        )
        other = ProductPage(url="https://b.com", product_category="", brand_name="", price=9.99)

        merged = _merge_product_pages(base, other)

        assert merged.product_category == "Supplements"
        assert merged.brand_name == "Uvora"
        assert merged.product_name == "Real Product Name"
        assert merged.variants_featured == ["Variant: A", "Variant: B"]
        assert merged.price == 9.99  # base had none — filled from other

    def test_other_fills_gaps_base_left_empty(self) -> None:
        base = ProductPage(url="https://a.com")
        other = ProductPage(
            url="https://b.com", product_category="Supplements", rating=4.5, rating_count=88
        )

        merged = _merge_product_pages(base, other)

        assert merged.product_category == "Supplements"
        assert merged.rating == 4.5
        assert merged.rating_count == 88

    def test_shows_all_variants_true_never_regresses_to_false(self) -> None:
        base = ProductPage(url="https://a.com", shows_all_variants=True)
        other = ProductPage(url="https://b.com", shows_all_variants=False)
        assert _merge_product_pages(base, other).shows_all_variants is True

    def test_none_base_returns_other(self) -> None:
        other = ProductPage(url="https://b.com", price=9.99)
        assert _merge_product_pages(None, other) is other

    def test_none_other_returns_base(self) -> None:
        base = ProductPage(url="https://a.com", price=9.99)
        assert _merge_product_pages(base, None) is base


class TestEnrichCorpusAdvertorialFallback:
    """Phase 0.5e: targets exactly the URLs a prior --zenrows diagnostics CSV
    flagged as success=False, running Tier 4.5/5 against them. No real
    ZenRowsClient/Replicate calls — the zenrows.ZenRowsClient constructor and
    the LLM call are both patched."""

    _PAGE_A = (
        "<html><body><h1>Sleep Aid Drops</h1>"
        "<p>Buy now, 88 reviews, tracker AAA111. Great product for great "
        "people everywhere all around the whole entire world today.</p></body></html>"
    )
    # Near-duplicate of _PAGE_A: only the tracker token differs.
    _PAGE_B = (
        "<html><body><h1>Sleep Aid Drops</h1>"
        "<p>Buy now, 88 reviews, tracker BBB222. Great product for great "
        "people everywhere all around the whole entire world today.</p></body></html>"
    )

    def _write_diagnostics_csv(
        self, path: Path, failed_urls: list[str], success_urls: list[str]
    ) -> None:
        import pandas as pd

        rows = [{"url": u, "success": False} for u in failed_urls] + [
            {"url": u, "success": True} for u in success_urls
        ]
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_only_targets_urls_flagged_success_false(self, tmp_path: Path) -> None:
        ad_failed = CompetitorAd(page_name="Failed", link_url="https://a.tryrosabella.com/x")
        ad_ok = CompetitorAd(page_name="Already OK", link_url="https://a.tryrosabella.com/ok")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(
            json.dumps([ad_failed.model_dump(mode="json"), ad_ok.model_dump(mode="json")])
        )

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(
            diagnostics_csv,
            failed_urls=["https://a.tryrosabella.com/x"],
            success_urls=["https://a.tryrosabella.com/ok"],
        )

        fake_client = _FakeAdvertorialZenRowsClient({"https://a.tryrosabella.com/x": self._PAGE_A})

        with (
            patch("zenrows.ZenRowsClient", return_value=fake_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction(
                product_title="Sleep Aid Drops", review_count=88
            )
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv
            )

        assert result == 0
        assert mock_llm.call_count == 1  # only the failed URL was ever fetched/processed
        out = json.loads((tmp_path / "out.json").read_text())
        failed_entry = next(a for a in out if a["link_url"] == "https://a.tryrosabella.com/x")
        assert failed_entry["product_page"]["rating_count"] == 88
        ok_entry = next(a for a in out if a["link_url"] == "https://a.tryrosabella.com/ok")
        assert ok_entry["product_page"] is None  # untouched — not in the failed set

    def test_targets_success_true_rows_with_missing_price(self, tmp_path: Path) -> None:
        """Regression: success==False alone under-targets. A row can be
        marked successful off a description/rating match alone while price
        was never resolved (see zenrows_scraper.py::_has_min_fields) — those
        rows never got a price-specific fallback attempt in the original
        run, and previously were silently excluded from re-targeting
        entirely because the diagnostics CSV said success=True."""
        import pandas as pd

        ad_price_missing = CompetitorAd(
            page_name="Desc Only", link_url="https://a.tryrosabella.com/price-missing"
        )
        ad_fully_ok = CompetitorAd(
            page_name="Fully OK", link_url="https://a.tryrosabella.com/ok"
        )
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(
            json.dumps(
                [ad_price_missing.model_dump(mode="json"), ad_fully_ok.model_dump(mode="json")]
            )
        )

        diagnostics_csv = tmp_path / "diag.csv"
        pd.DataFrame(
            [
                # success=True but product_price is missing (NaN) — must
                # still be targeted.
                {
                    "url": "https://a.tryrosabella.com/price-missing",
                    "success": True,
                    "product_price": None,
                },
                # success=True and product_price present — must NOT be
                # re-targeted (untouched, as before this fix).
                {
                    "url": "https://a.tryrosabella.com/ok",
                    "success": True,
                    "product_price": 19.99,
                },
            ]
        ).to_csv(diagnostics_csv, index=False)

        fake_client = _FakeAdvertorialZenRowsClient(
            {"https://a.tryrosabella.com/price-missing": self._PAGE_A}
        )

        with (
            patch("zenrows.ZenRowsClient", return_value=fake_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction(
                product_title="Sleep Aid Drops", review_count=88
            )
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv
            )

        assert result == 0
        assert mock_llm.call_count == 1  # only the price-missing URL was fetched/processed
        out = json.loads((tmp_path / "out.json").read_text())
        price_missing_entry = next(
            a for a in out if a["link_url"] == "https://a.tryrosabella.com/price-missing"
        )
        assert price_missing_entry["product_page"]["rating_count"] == 88
        ok_entry = next(a for a in out if a["link_url"] == "https://a.tryrosabella.com/ok")
        assert ok_entry["product_page"] is None  # untouched — price already resolved

    def test_escalates_to_llm_when_cheap_tiers_succeed_but_price_missing(
        self, tmp_path: Path
    ) -> None:
        """Regression: the cheap-tiers-only fetch pass (enable_llm_fallback=
        False) can mark a URL success=True off a description/JSON-LD match
        alone, with no price. Previously that short-circuited straight to
        to_product_page_updates without ever spending the LLM call — even
        though price was still missing. Price-missing must now escalate to
        the LLM-enabled re-run just like the diagnostics-CSV targeting does."""
        desc_only_page = (
            "<html><body>"
            '<script type="application/ld+json">'
            '{"@type": "Product", "description": "A fine sleep aid with no price in the markup."}'
            "</script>"
            "<h1>Sleep Aid</h1>"
            "<p>Just 1 Item today for $19.99, get yours now while supplies last.</p>"
            "</body></html>"
        )
        ad = CompetitorAd(page_name="DescOnly", link_url="https://a.tryrosabella.com/desc-only")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(
            diagnostics_csv, failed_urls=["https://a.tryrosabella.com/desc-only"], success_urls=[]
        )

        fake_client = _FakeAdvertorialZenRowsClient(
            {"https://a.tryrosabella.com/desc-only": desc_only_page}
        )

        with (
            patch("zenrows.ZenRowsClient", return_value=fake_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction, _LLMOfferTier

            mock_llm.return_value = _DirectResponseLLMExtraction(
                product_title="Sleep Aid",
                offer_matrix=[
                    _LLMOfferTier(
                        tier_label="1 Item", quantity=1, total_price=19.99,
                        price_per_unit=19.99, currency="USD",
                    )
                ],
            )
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv
            )

        assert result == 0
        assert mock_llm.call_count == 1  # escalation happened despite the cheap-tier "success"
        out = json.loads((tmp_path / "out.json").read_text())
        entry = out[0]
        assert entry["product_page"]["price"] == 19.99
        assert entry["product_page"]["marketing_copy"]  # description from the cheap tier preserved

    def test_near_duplicate_pages_share_one_llm_call(self, tmp_path: Path) -> None:
        ad_a = CompetitorAd(page_name="A", link_url="https://track.tryrosabella.com/aaa111")
        ad_b = CompetitorAd(page_name="B", link_url="https://track.tryrosabella.com/bbb222")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(
            json.dumps([ad_a.model_dump(mode="json"), ad_b.model_dump(mode="json")])
        )

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(
            diagnostics_csv,
            failed_urls=[
                "https://track.tryrosabella.com/aaa111",
                "https://track.tryrosabella.com/bbb222",
            ],
            success_urls=[],
        )

        fake_client = _FakeAdvertorialZenRowsClient(
            {
                "https://track.tryrosabella.com/aaa111": self._PAGE_A,
                "https://track.tryrosabella.com/bbb222": self._PAGE_B,
            }
        )

        with (
            patch("zenrows.ZenRowsClient", return_value=fake_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction(
                product_title="Sleep Aid Drops", review_count=88
            )
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv,
                near_duplicate_threshold=0.5,
            )

        assert result == 0
        # Near-duplicate HTML -> the second URL's result is copied, not
        # independently re-extracted via a second LLM call.
        assert mock_llm.call_count == 1
        out = json.loads((tmp_path / "out.json").read_text())
        page_a = next(a for a in out if a["link_url"] == "https://track.tryrosabella.com/aaa111")["product_page"]
        page_b = next(a for a in out if a["link_url"] == "https://track.tryrosabella.com/bbb222")["product_page"]
        assert page_a["rating_count"] == 88
        assert page_b["rating_count"] == 88

    def test_near_duplicate_hit_preserves_existing_rich_product_page(
        self, tmp_path: Path
    ) -> None:
        """Regression: a near-dup hit previously replaced the matched ad's
        own pre-existing product_page outright with dup_match — discarding
        real data (product_category/brand_name/variants_featured) an
        earlier tier had already found for that ad's own URL. Confirmed
        live: 22 fields across 4 real ads were wiped this way in the
        Phase 0.5f Pass B run before this fix."""
        ad_a = CompetitorAd(page_name="A", link_url="https://track.tryrosabella.com/aaa111")
        ad_b = CompetitorAd(
            page_name="B",
            link_url="https://track.tryrosabella.com/bbb222",
            product_page=ProductPage(
                url="https://track.tryrosabella.com/bbb222",
                product_category="Supplements",
                brand_name="RealBrand",
                product_name="Real Product",
                extraction_method="structured_data+llm",
            ),
        )
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(
            json.dumps([ad_a.model_dump(mode="json"), ad_b.model_dump(mode="json")])
        )

        diagnostics_csv = tmp_path / "diag.csv"
        # ad_b's price is missing despite already having a product_page —
        # exactly the Fix 2 targeting condition that now also re-targets
        # success=True-but-price-missing rows.
        pd = __import__("pandas")
        pd.DataFrame(
            [
                {
                    "url": "https://track.tryrosabella.com/aaa111",
                    "success": False,
                    "product_price": None,
                },
                {
                    "url": "https://track.tryrosabella.com/bbb222",
                    "success": True,
                    "product_price": None,
                },
            ]
        ).to_csv(diagnostics_csv, index=False)

        fake_client = _FakeAdvertorialZenRowsClient(
            {
                "https://track.tryrosabella.com/aaa111": self._PAGE_A,
                "https://track.tryrosabella.com/bbb222": self._PAGE_B,
            }
        )

        with (
            patch("zenrows.ZenRowsClient", return_value=fake_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction(
                product_title="Sleep Aid Drops", review_count=88
            )
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv,
                near_duplicate_threshold=0.5,
            )

        assert result == 0
        out = json.loads((tmp_path / "out.json").read_text())
        page_b = next(a for a in out if a["link_url"] == "https://track.tryrosabella.com/bbb222")[
            "product_page"
        ]
        # ad_b's own pre-existing rich fields must survive the near-dup merge.
        assert page_b["product_category"] == "Supplements"
        assert page_b["brand_name"] == "RealBrand"
        assert page_b["product_name"] == "Real Product"
        # New data from the near-dup match still fills gaps ad_b left empty.
        assert page_b["rating_count"] == 88

    def test_missing_diagnostics_csv_returns_error(self, tmp_path: Path) -> None:
        ads_file = tmp_path / "ads.json"
        ads_file.write_text("[]")
        result = enrich_corpus_advertorial_fallback(
            ads_file, tmp_path / "out.json", diagnostics_csv=tmp_path / "missing.csv"
        )
        assert result == 1

    def test_resume_skips_already_advertorial_enriched_urls(self, tmp_path: Path) -> None:
        prior_ad = CompetitorAd(
            page_name="Prior",
            link_url="https://track.tryrosabella.com/aaa111",
            product_page=ProductPage(
                product_name="Prior Result",
                rating_count=88,
                extraction_method="tier_5_llm",
            ),
        )
        ads_file = tmp_path / "ads.json"
        out_file = tmp_path / "out.json"
        out_file.write_text(json.dumps([prior_ad.model_dump(mode="json")]))

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(
            diagnostics_csv, failed_urls=["https://track.tryrosabella.com/aaa111"], success_urls=[]
        )
        ads_file.write_text(json.dumps([prior_ad.model_dump(mode="json")]))

        with (
            patch("zenrows.ZenRowsClient") as mock_cls,
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            result = enrich_corpus_advertorial_fallback(
                ads_file, out_file, diagnostics_csv=diagnostics_csv,
                checkpoint_path=out_file, resume=True,
            )

        assert result == 0
        mock_cls.assert_not_called()  # fully served from resume — no fetch needed


class TestEnrichCorpusAdvertorialFallbackShopifyJsonPrePass:
    """Regression coverage for a real production gap found via continued
    extraction-gap sampling: enrich_corpus_advertorial_fallback only ever
    ran the ZenRows cascade (its own Tiers 1-4 = XHR/JSON-LD/window-objects/
    DOM, a separate numbering from tiered_scraper.py's Tier 1 = Shopify JSON
    API) — it never called shopify_json.py at all. rituallabs.shop/products/
    happy-liver13 (220 ads) had extraction_method
    "tier_5_llm+tier_5_llm+tier_5_llm+tier_5_llm" — reprocessed 4 times via
    this exact function, never once via the free Shopify .json endpoint,
    despite it trivially returning a real price. This pre-pass fixes that:
    any target URL with a literal /products/{handle} path gets a free
    (non-ZenRows) Shopify JSON attempt before the paid cascade runs."""

    _PAGE_LLM_FINDS_NOTHING = "<html><body><p>Nothing structured here.</p></body></html>"

    def _write_diagnostics_csv(self, path: Path, failed_urls: list[str]) -> None:
        import pandas as pd

        pd.DataFrame([{"url": u, "success": False} for u in failed_urls]).to_csv(path, index=False)

    def test_shopify_json_backfills_price_before_zenrows_cascade_runs(self, tmp_path: Path) -> None:
        ad = CompetitorAd(
            page_name="Ritual",
            link_url="https://rituallabs.shop/products/happy-liver13",
        )
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(
            diagnostics_csv, failed_urls=["https://rituallabs.shop/products/happy-liver13"]
        )

        shopify_payload = {
            "product": {
                "title": "Happy Liver",
                "vendor": "Ritual Labs B2",
                "tags": "",
                "body_html": "",
                "variants": [{"title": "Default Title", "price": "44.99"}],
            }
        }
        fake_zenrows_client = _FakeAdvertorialZenRowsClient(
            {"https://rituallabs.shop/products/happy-liver13": self._PAGE_LLM_FINDS_NOTHING}
        )

        with (
            patch(
                "ingestion.shopify_json.fetch_shopify_json", return_value=shopify_payload
            ) as mock_shopify,
            patch("zenrows.ZenRowsClient", return_value=fake_zenrows_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction()
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv
            )

        assert result == 0
        mock_shopify.assert_called_once()
        out = json.loads((tmp_path / "out.json").read_text())
        page = out[0]["product_page"]
        assert page["price"] == 44.99
        assert page["product_name"] == "Happy Liver"
        assert page["brand_name"] == "Ritual Labs B2"
        assert "shopify_json" in page["extraction_method"]

    def test_non_product_path_url_never_attempts_shopify_json(self, tmp_path: Path) -> None:
        ad = CompetitorAd(page_name="Advertorial", link_url="https://track.tryrosabella.com/abc123")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(
            diagnostics_csv, failed_urls=["https://track.tryrosabella.com/abc123"]
        )

        fake_zenrows_client = _FakeAdvertorialZenRowsClient(
            {"https://track.tryrosabella.com/abc123": "<html><body><h1>X</h1></body></html>"}
        )

        with (
            patch("ingestion.shopify_json.fetch_shopify_json") as mock_shopify,
            patch("zenrows.ZenRowsClient", return_value=fake_zenrows_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction()
            enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv
            )

        mock_shopify.assert_not_called()

    def test_shopify_json_failure_falls_through_to_zenrows_cascade(self, tmp_path: Path) -> None:
        ad = CompetitorAd(page_name="Ritual", link_url="https://store.com/products/gone")
        ads_file = tmp_path / "ads.json"
        ads_file.write_text(json.dumps([ad.model_dump(mode="json")]))

        diagnostics_csv = tmp_path / "diag.csv"
        self._write_diagnostics_csv(diagnostics_csv, failed_urls=["https://store.com/products/gone"])

        fake_zenrows_client = _FakeAdvertorialZenRowsClient(
            {
                "https://store.com/products/gone": (
                    "<html><body><h1>Sleep Aid Drops</h1></body></html>"
                )
            }
        )

        with (
            patch("ingestion.shopify_json.fetch_shopify_json", return_value=None),
            patch("zenrows.ZenRowsClient", return_value=fake_zenrows_client),
            patch(_LLM_PATCH_TARGET) as mock_llm,
        ):
            from ingestion.llm_fallback import _DirectResponseLLMExtraction

            mock_llm.return_value = _DirectResponseLLMExtraction(product_title="Sleep Aid Drops")
            result = enrich_corpus_advertorial_fallback(
                ads_file, tmp_path / "out.json", diagnostics_csv=diagnostics_csv
            )

        assert result == 0
        out = json.loads((tmp_path / "out.json").read_text())
        assert out[0]["product_page"]["product_name"] == "Sleep Aid Drops"
        mock_llm.assert_called_once()
