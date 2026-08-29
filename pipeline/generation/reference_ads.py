"""Retrieval of real, currently-successful ads (Generation v1). Round 7
(2026-08-29) reframes what these ads are *for*: not to guide generation --
that's the guide's (guide.py) job, since it already has a rigorous,
larger-sample statistical answer for every dimension Step 2 measures -- but
to serve as ground-truth exemplars for checking, after the fact, whether a
generated ad actually replicates the feature pattern the model found (see
feature_fidelity.py). A reference ad is only useful for that job if it's a
genuine embodiment of the guide's own top directives, not merely a
high-scoring ad that happens to be an outlier -- so selection here is
directive-alignment-first, composite-score second, never composite-score
alone.

Round 5's original finding is still the grounding fact that motivated this
whole module: confirmed live (2026-08-28) that every one of the top 5 real
ads by composite success score has `background_style=Busy`, zero use a
plain studio background -- i.e. real winners really do embody the guide's
own statistical findings, which is exactly what makes them usable as
fidelity-check exemplars rather than arbitrary anecdotes.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pipeline.generation.guide import GenerationGuide
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
    alignment_score: int
    image_bytes: bytes
    image_mime_type: str
    dominant_color: str | None
    background_style: str | None
    hook_framework: str | None


def _directive_alignment_score(row: dict, guide: GenerationGuide) -> int:
    """+1 for every guide categorical directive whose good ('higher_is_better')
    value this ad's own extracted feature actually has; -1 for every
    directive whose *discouraged* ('lower_is_better') value it has. Only
    categorical directives with a real `value` are checkable against a
    single ad this way -- numeric directives (value=None) describe a
    distribution, not a single ad's yes/no trait, so they're skipped here.
    A row with no real feature data for a dimension (NaN, missing) never
    contributes either way -- absence of data is not evidence of misalignment."""
    score = 0
    for s in guide.visual_directives:
        if s.value is None:
            continue
        row_value = row.get(s.dimension)
        if not isinstance(row_value, str):
            continue
        matches = row_value == s.value
        if matches and s.direction == "higher_is_better":
            score += 1
        elif matches and s.direction == "lower_is_better":
            score -= 1
    return score


def get_top_reference_ads(
    guide: GenerationGuide,
    n: int = 3,
    min_alignment: int = 1,
    matrix_file: Path = DEFAULT_MATRIX_FILE,
    ads_file: Path = DEFAULT_ADS_FILE,
) -> list[ReferenceAd]:
    """Returns up to `n` real ads, ranked by directive alignment first and
    composite success score second -- so a returned ad is both a genuine
    embodiment of what the guide's statistics found (not an unrelated-reasons
    outlier) and among the more successful examples of it. Restricted to ads
    with real creative_features (so alignment/style fields reflect genuine
    extracted data, not defaults) and a still-fetchable image.

    `min_alignment` requires at least this many confirmed good-trait matches
    (default 1 -- "can't just be a random ad," per the reason this scoring
    exists); relaxed to 0 automatically (logged) if too few ads clear the
    bar, since a real corpus-coverage gap must degrade gracefully rather than
    silently returning nothing to compare against. Skips a stale/failed
    image rather than raising -- matches this project's established
    per-ad-failure-tolerant convention; may still return fewer than `n`
    (possibly zero) if too many candidates are stale, which callers
    (feature_fidelity.py) must handle."""
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
    for r in scored:
        r["_alignment_score"] = _directive_alignment_score(r, guide)

    candidates = [r for r in scored if r["_alignment_score"] >= min_alignment]
    if len(candidates) < n and min_alignment > 0:
        logger.warning(
            "reference_ad_alignment_bar_relaxed",
            found=len(candidates), requested=n, min_alignment=min_alignment,
        )
        candidates = scored
    candidates.sort(key=lambda r: (-r["_alignment_score"], -r["composite_success_score"]))

    results: list[ReferenceAd] = []
    for row in candidates:
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
            alignment_score=row["_alignment_score"],
            image_bytes=image_bytes,
            image_mime_type="image/jpeg",
            dominant_color=row.get("dominant_color"),
            background_style=row.get("background_style"),
            hook_framework=row.get("creative_hook_framework"),
        ))
    return results
