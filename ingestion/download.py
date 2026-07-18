"""Best-effort download of ad creatives from Meta Ad Library items.

Downloads the primary image per ad to ``media/<ad_archive_id>.<ext>``, sets
``local_image_path``, and logs failures without dropping the ad (mirrors the
facebook-ad-library skill's honesty).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from ingestion.models import CompetitorAd
from pipeline.logger import get_logger

logger = get_logger(__name__)


def _get_extension_from_url(url: str) -> str:
    """Extract file extension from URL, defaulting to 'jpg'."""
    path = urlparse(url).path.lower()
    for ext in (".webp", ".png", ".gif", ".jpg", ".jpeg"):
        if ext in path:
            return ext.lstrip(".")
    return "jpg"


def download_creatives(ads: list[CompetitorAd], media_dir: str | Path) -> None:
    """Download primary image per ad; set local_image_path; record failures without dropping ads.

    Args:
        ads: List of CompetitorAd objects (mutated in-place: local_image_path set).
        media_dir: Directory to write downloaded images (created if missing).
    """
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    for ad in ads:
        if not ad.image_urls:
            logger.info(
                "download_skip_no_images",
                ad_id=ad.ad_archive_id,
            )
            continue

        # Try the first image URL.
        url = ad.image_urls[0]
        ext = _get_extension_from_url(url)
        local_path = media_dir / f"{ad.ad_archive_id}.{ext}"

        try:
            with urlopen(url, timeout=10) as response:
                data = response.read()
            with open(local_path, "wb") as f:
                f.write(data)
            ad.local_image_path = str(local_path)
            logger.info(
                "download_success",
                ad_id=ad.ad_archive_id,
                url=url,
                local_path=str(local_path),
            )
        except Exception as e:
            logger.warning(
                "download_failed",
                ad_id=ad.ad_archive_id,
                url=url,
                exc_str=str(e),
            )
            # Do NOT drop the ad; record the snapshot_url as fallback.
