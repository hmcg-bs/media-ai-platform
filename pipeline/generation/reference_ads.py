"""Retrieval of real, currently-successful ads (Generation v1, round 5) --
grounds generation in what actually wins, not just abstract statistical
directives. Same retrieval-grounded principle already established for
Critique's suggestion engine (ADR-008 §4: embeddings power retrieval of
similar proven ads/patterns), applied here to Generation.

Confirmed live (2026-08-28): every one of the top 5 real ads by composite
success score has `background_style=Busy` -- zero use a plain studio
background. That single fact is why this module exists: the guide's
Cox/SHAP directives alone (guide.py) had already correctly found
`background_style=Studio` to be `lower_is_better`, but nothing consumed that
signal to generate anything *other than* a studio look (background.py's old
hardcoded fallback), and there was no way to check what winners actually
look like instead of arguing from statistics alone.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pipeline.logger import get_logger
from pipeline.model_training.success_score import compute_composite_success_score

logger = get_logger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_MATRIX_FILE = DATA_DIR / "feature_matrix_fresh.json"
DEFAULT_ADS_FILE = DATA_DIR / "supplements_fresh_final.json"


@dataclass
class ReferenceAd:
    ad_id: str
    composite_score: float
    image_bytes: bytes
    image_mime_type: str
    dominant_color: str | None
    background_style: str | None
    hook_framework: str | None


def get_top_reference_ads(
    n: int = 3,
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
) -> list[ReferenceAd]:
    """Returns up to `n` real ads, highest composite success score first,
    restricted to ads with real creative_features (so the style fields below
    reflect genuine extracted data, not defaults) and a still-fetchable
    image. Skips a stale/failed image rather than raising -- matches this
    project's established per-ad-failure-tolerant convention; returns fewer
    than `n` (possibly zero) if too many top candidates are stale, which
    callers must handle (style_reference.py degrades gracefully)."""
    rows = json.loads(matrix_file.read_text())
    ads_by_id = {a["ad_archive_id"]: a for a in json.loads(ads_file.read_text())}

    scored = compute_composite_success_score(rows)
    # compute_composite_success_score round-trips rows through a pandas
    # DataFrame -- any row missing a column present in *other* rows gets
    # NaN filled in for it, and NaN is truthy in Python, so a plain
    # `if r.get(...)` check silently lets NaN-valued rows through (confirmed
    # by this filter's own regression test). dominant_color/background_style
    # are always real strings when genuinely present -- never NaN itself.
    scored = [
        r for r in scored
        if isinstance(r.get("dominant_color"), str) and isinstance(r.get("background_style"), str)
    ]
    scored.sort(key=lambda r: -r["composite_success_score"])

    results: list[ReferenceAd] = []
    for row in scored:
        if len(results) >= n:
            break
        ad = ads_by_id.get(row["ad_id"])
        if not ad or not ad.get("image_urls"):
            continue
        try:
            req = urllib.request.Request(
                ad["image_urls"][0], headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 -- trusted CDN URL
                image_bytes = resp.read()
        except Exception as e:  # noqa: BLE001 -- one stale reference ad shouldn't break the batch
            logger.warning("reference_ad_fetch_failed", ad_id=row["ad_id"], error=str(e))
            continue
        results.append(ReferenceAd(
            ad_id=row["ad_id"],
            composite_score=row["composite_success_score"],
            image_bytes=image_bytes,
            image_mime_type="image/jpeg",
            dominant_color=row.get("dominant_color"),
            background_style=row.get("background_style"),
            hook_framework=row.get("creative_hook_framework"),
        ))
    return results
