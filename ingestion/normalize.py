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
    urls: list[str] = []
    for item in snapshot.get("videos") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("video_hd_url") or item.get("video_sd_url")
        if url:
            urls.append(url)
    return urls


def normalize_ad(raw: dict[str, Any]) -> CompetitorAd:
    """Normalize one raw scraper item into a ``CompetitorAd``."""
    snapshot = raw.get("snapshot") or {}
    ad_id = str(raw.get("ad_archive_id") or raw.get("adArchiveID") or raw.get("id") or "")

    start_iso = _to_iso_date(raw.get("start_date") or raw.get("startDate"))
    end_iso = _to_iso_date(raw.get("end_date") or raw.get("endDate"))

    platforms = raw.get("publisher_platform") or raw.get("publisherPlatform") or []
    if isinstance(platforms, str):
        platforms = [platforms]

    snapshot_url = (
        raw.get("ad_snapshot_url")
        or raw.get("url")
        or (f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else "")
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
        image_urls=_image_urls(snapshot),
        video_urls=_video_urls(snapshot),
        publisher_platforms=[str(p) for p in platforms],
        snapshot_url=snapshot_url,
        ingested_at=datetime.now(tz=UTC).isoformat(),
    )
