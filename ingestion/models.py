"""Normalized competitor-ad record (mirrors the blueprint's ``competitor_ads_raw``).

One ``CompetitorAd`` per scraped Meta Ad Library item. Captures the copy + creative handles
plus the raw performance-proxy signals (``days_active`` = Longevity, ``is_active`` +
``collation_count`` = Variant); the composite proxy is computed later in Step 3, never here.

Stage 4 adds ``product_page`` analysis from landing-page scraping (product categorization).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

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

    # Delivery/scale signals from Meta's own Ad Library transparency data --
    # populated only for political/social-issue/EU-regulated ads; confirmed
    # live (a real Apify run against this project's actual US-commercial-ad
    # search) that these come back None/-1 for ordinary commercial ads, the
    # kind this corpus is entirely made of. Captured anyway rather than
    # discarded: the raw scraper output already includes these fields on
    # every run (scrapeAdDetails=True is already set in apify_client.py),
    # so dropping them here would silently lose real data the one time an
    # ad *does* qualify for disclosure (e.g. a future EU-targeted scrape) --
    # the same bug class as several other fields found this session.
    impressions_text: str | None = None      # e.g. "10K-50K" when disclosed
    impressions_index: int | None = None     # Meta's internal bucket index; -1 normalized to None
    reach_estimate: str | None = None        # shape unconfirmed -- always None in every sample seen
    spend: str | None = None                 # e.g. "$1K-$5K" when disclosed; shape unconfirmed
    gated_type: str | None = None            # Meta's disclosure-eligibility flag ("ELIGIBLE" etc.)
    regional_transparency: dict[str, Any] | None = None  # raw transparency_by_location passthrough

    @field_validator("reach_estimate", "spend", mode="before")
    @classmethod
    def _coerce_unconfirmed_shape_to_str(cls, v: Any) -> str | None:
        """reach_estimate/spend have never been observed populated in this
        project's own live testing -- their real shape when Meta does
        disclose a value (numeric? a range string? a nested object?) is
        unconfirmed. Coerce anything non-None/non-str to a string rather
        than let an unexpected shape raise a validation error the first
        time a real value actually appears (same permissive-coercion
        pattern used for MarketingPsychology.reading_grade_level, which hit
        exactly this kind of surprise-shape issue)."""
        if v is None or isinstance(v, str):
            return v
        return str(v)

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
