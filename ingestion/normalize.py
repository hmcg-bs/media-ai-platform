"""Map a raw Apify / Meta Ad Library item into a ``CompetitorAd`` — defensively.

Field names in the Ad Library snapshot vary and change; every access uses ``dict.get`` with a
safe default so an unexpected shape degrades to empty rather than crashing (blueprint §5.4).
Confirm the exact field names against a real Apify run before trusting edge cases.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ingestion.models import CompetitorAd


def _to_iso_date(value: Any) -> str | None:
    """Epoch seconds (int / numeric str) or an ISO string → 'YYYY-MM-DD'."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).date().isoformat()
    if isinstance(value, str):
        if value.isdigit():
            return datetime.fromtimestamp(int(value), tz=UTC).date().isoformat()
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return value[:10] or None
    return None


def _days_active(start_iso: str | None, end_iso: str | None) -> int:
    """Longevity: end (or today if still active) − start, in days."""
    if not start_iso:
        return 0
    try:
        start = date.fromisoformat(start_iso)
    except ValueError:
        return 0
    try:
        end = date.fromisoformat(end_iso) if end_iso else date.today()
    except ValueError:
        end = date.today()
    return max(0, (end - start).days)


def _body_text(snapshot: dict[str, Any]) -> str:
    body = snapshot.get("body")
    if isinstance(body, dict):
        return body.get("text", "") or ""
    if isinstance(body, str):
        return body
    return ""


def _image_urls(snapshot: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for group in ("images", "cards"):
        for item in snapshot.get(group) or []:
            if not isinstance(item, dict):
                continue
            url = item.get("original_image_url") or item.get("resized_image_url")
            if url:
                urls.append(url)
    return urls


def _video_urls(snapshot: dict[str, Any]) -> list[str]:
    """Regression: previously only read snapshot.videos -- confirmed live
    (a fresh Apify scrape) that a video-carousel ad's video lives under a
    "cards" entry instead, with video_hd_url/video_sd_url exactly like a
    top-level videos entry. The old code silently dropped that ad's video
    entirely (video_urls == [] even though a real video existed)."""
    urls: list[str] = []
    for group in ("videos", "cards"):
        for item in snapshot.get(group) or []:
            if not isinstance(item, dict):
                continue
            url = item.get("video_hd_url") or item.get("video_sd_url")
            if url:
                urls.append(url)
    return urls


def _video_preview_image_urls(snapshot: dict[str, Any]) -> list[str]:
    """Video thumbnail/preview frames -- confirmed live these often carry
    real overlay text baked into the frame, making them a legitimate
    fallback creative image for a video-only ad with no static image at
    all (otherwise that ad has zero usable image data for Step 2's
    OCR/color/cognitive pipeline, even though a real, analyzable frame
    exists)."""
    urls: list[str] = []
    for group in ("videos", "cards"):
        for item in snapshot.get(group) or []:
            if not isinstance(item, dict):
                continue
            url = item.get("video_preview_image_url")
            if url:
                urls.append(url)
    return urls


def _impressions(raw: dict[str, Any]) -> tuple[str | None, int | None]:
    """(impressions_text, impressions_index) from raw's impressions_with_index
    dict. Meta uses -1 as an explicit "not disclosed" sentinel (confirmed
    live) -- normalized to None here so callers get a real null instead of
    having to know about this specific magic number."""
    info = raw.get("impressions_with_index")
    if not isinstance(info, dict):
        return None, None
    text = info.get("impressions_text")
    index = info.get("impressions_index")
    if index == -1:
        index = None
    return text, index


def normalize_ad(raw: dict[str, Any]) -> CompetitorAd:
    """Normalize one raw scraper item into a ``CompetitorAd``."""
    snapshot = raw.get("snapshot") or {}
    ad_id = str(raw.get("ad_archive_id") or raw.get("adArchiveID") or raw.get("id") or "")

    start_iso = _to_iso_date(raw.get("start_date") or raw.get("startDate"))
    end_iso = _to_iso_date(raw.get("end_date") or raw.get("endDate"))

    platforms = raw.get("publisher_platform") or raw.get("publisherPlatform") or []
    if isinstance(platforms, str):
        platforms = [platforms]

    impressions_text, impressions_index = _impressions(raw)
    regional_transparency = raw.get("transparency_by_location")
    if not isinstance(regional_transparency, dict):
        regional_transparency = None

    # `raw.get("url")` was checked *before* the constructed fallback, but it
    # is not a per-ad field at all — it's the Apify actor echoing back its
    # own scrape input (apify_client.py's constructed ad_library_url, the
    # same generic search-query URL for every item in a run). Confirmed
    # live: this actor's real output never populates `ad_snapshot_url`, so
    # every ad's snapshot_url ended up as that identical generic search URL
    # — useless as a per-ad link. The constructed `?id={ad_id}` form is
    # Meta's real, stable, always-correct per-ad Ad Library detail-page
    # URL (confirmed against the test fixture's own `ad_snapshot_url`
    # shape) — moved ahead of the `url` fallback so it actually fires.
    snapshot_url = (
        raw.get("ad_snapshot_url")
        or (f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else "")
        or raw.get("url")
        or ""
    )

    return CompetitorAd(
        ad_archive_id=ad_id,
        page_id=str(raw.get("page_id") or snapshot.get("page_id") or ""),
        page_name=raw.get("page_name") or snapshot.get("page_name") or "",
        start_date=start_iso,
        end_date=end_iso,
        is_active=bool(raw.get("is_active", end_iso is None)),
        days_active=_days_active(start_iso, end_iso),
        collation_count=int(raw.get("collation_count") or 0),
        body=_body_text(snapshot),
        title=snapshot.get("title") or "",
        caption=snapshot.get("caption") or "",
        link_url=snapshot.get("link_url") or "",
        cta_text=snapshot.get("cta_text") or "",
        image_urls=_image_urls(snapshot) or _video_preview_image_urls(snapshot),
        video_urls=_video_urls(snapshot),
        publisher_platforms=[str(p) for p in platforms],
        snapshot_url=snapshot_url,
        ingested_at=datetime.now(tz=UTC).isoformat(),
        impressions_text=impressions_text,
        impressions_index=impressions_index,
        reach_estimate=raw.get("reach_estimate"),
        spend=raw.get("spend"),
        gated_type=raw.get("gated_type"),
        regional_transparency=regional_transparency,
    )
