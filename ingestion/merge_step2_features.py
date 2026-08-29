"""Merges Step 2 creative-pipeline output (out/step2/<ad_id>.json) back into
the ingestion corpus, as a new `creative_features` sub-key per ad.

Additive and non-destructive: writes to a new file rather than overwriting
the canonical corpus, so the merge can be verified (same ad count/order/ids,
zero regressions on existing product_page fields) before promoting it —
same discipline as every corpus mutation this session.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline.models.output_schema import ExtractionResult

# ColorProfile's fields aren't part of flatten_features() (that method is
# scoped to copywriting + placement only) — pulled in separately so
# extractor.py's color-feature wiring has real dominant_hex/palette/
# contrast/background_style data instead of the hardcoded placeholder.
_COLOR_PROFILE_KEYS = (
    "background_hex",
    "background_style",
    "dominant_hex_palette",
    "contrast_ratio_type",
)


def load_step2_results(step2_out_dir: Path) -> dict[str, dict[str, Any]]:
    """Reads every <ad_id>.json in step2_out_dir, returns {ad_id: creative_features_dict}."""
    results: dict[str, dict[str, Any]] = {}
    for path in sorted(step2_out_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            extraction = ExtractionResult.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            continue
        creative_features = extraction.flatten_features()
        color_profile_dict = extraction.color_profile.model_dump(mode="json")
        for key in _COLOR_PROFILE_KEYS:
            creative_features[key] = color_profile_dict.get(key)
        results[extraction.ad_id] = creative_features
    return results


def merge_step2_into_corpus(
    ads_file: Path, step2_out_dir: Path, output_file: Path
) -> tuple[int, int]:
    """Attaches creative_features to every ad with a matching Step 2 result.
    Returns (total_ads, ads_with_creative_features)."""
    ads = json.loads(ads_file.read_text())
    step2_results = load_step2_results(step2_out_dir)

    matched = 0
    for ad in ads:
        ad_id = ad.get("ad_archive_id")
        creative_features = step2_results.get(ad_id)
        if creative_features is not None:
            ad["creative_features"] = creative_features
            matched += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(ads, indent=2, default=str))
    return len(ads), matched


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge Step 2 creative-pipeline output into the ingestion corpus."
    )
    parser.add_argument("--ads", type=Path, default=Path("data/supplements_enriched.json"))
    parser.add_argument("--step2-out", type=Path, default=Path("out/step2"))
    parser.add_argument(
        "--out", type=Path, default=Path("data/supplements_enriched_with_creative.json")
    )
    args = parser.parse_args()

    total, matched = merge_step2_into_corpus(args.ads, args.step2_out, args.out)
    print(f"Merged creative_features into {matched}/{total} ads -> {args.out}")


if __name__ == "__main__":
    main()
