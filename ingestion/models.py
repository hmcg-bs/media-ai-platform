"""Normalized competitor-ad record (mirrors the blueprint's ``competitor_ads_raw``).

One ``CompetitorAd`` per scraped Meta Ad Library item. Captures the copy + creative handles
plus the raw performance-proxy signals (``days_active`` = Longevity, ``is_active`` +
``collation_count`` = Variant); the composite proxy is computed later in Step 3, never here.

Stage 4 adds ``product_page`` analysis from landing-page scraping (product categorization).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ingestion.product_page import ProductPage


class CompetitorAd(BaseModel):
    ad_archive_id: str = ""
    page_id: str = ""
    page_name: str = ""                 # the brand
    start_date: str | None = None       # ISO date (YYYY-MM-DD)
    end_date: str | None = None         # ISO date; None = still active
    is_active: bool = False
    days_active: int = 0                # Longevity proxy (computed)
    collation_count: int = 0            # active-variant count (Variant proxy)

    # Copy
    body: str = ""                      # primary ad text
    title: str = ""
    caption: str = ""
    link_url: str = ""                  # landing page (Product Type comes from here later)
    cta_text: str = ""

    # Creative handles
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    publisher_platforms: list[str] = Field(default_factory=list)
    snapshot_url: str = ""              # Ad Library preview page

    # Filled after download / at ingest time
    local_image_path: str | None = None
    ingested_at: str = ""

    # Stage 4: Landing-page analysis (product categorization)
    product_page: ProductPage | None = None
