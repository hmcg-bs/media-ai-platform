"""Local CLI for Generation v1's cold-start path (per the map's delivery
decision: local script for v1, no UI).

    uv run python -m pipeline.generation.cli \\
        --product-photo path/to/product.jpg \\
        --intention "Energizing pre-workout for young, active adults" \\
        --product-name "Surge Pre-Workout" \\
        --out out/generated_ad.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.generation.guide import extract_generation_guide, print_guide
from pipeline.generation.pipeline import generate_cold_start_ad


def main() -> None:
    parser = argparse.ArgumentParser(description="Generation v1: cold-start ad synthesis.")
    parser.add_argument("--product-photo", type=Path, required=True)
    parser.add_argument("--intention", type=str, required=True)
    parser.add_argument("--product-name", type=str, required=True)
    parser.add_argument("--out", type=Path, default=Path("out/generated_ad.png"))
    parser.add_argument("--max-passes", type=int, default=2)
    parser.add_argument(
        "--print-guide", action="store_true", help="Print the extracted guide and exit."
    )
    args = parser.parse_args()

    if args.print_guide:
        print_guide(extract_generation_guide())
        return

    product_photo_bytes = args.product_photo.read_bytes()
    result = generate_cold_start_ad(
        product_photo_bytes,
        intention=args.intention,
        product_name=args.product_name,
        max_passes=args.max_passes,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result.final_image_bytes)

    report_path = args.out.with_suffix(".report.json")
    report_path.write_text(json.dumps({
        "ad_copy": result.ad_copy.model_dump(),
        "style_brief": result.style_brief.model_dump(),
        "passes_used": result.passes_used,
        "ai_generated_disclosure": result.ai_generated_disclosure,
        "review_history": [r.model_dump() for r in result.review_history],
        "blend_review_history": [r.model_dump() for r in result.blend_review_history],
    }, indent=2))

    print(f"Wrote {args.out}")
    print(f"Wrote {report_path}")
    print(f"Style brief: background={result.style_brief.background_treatment!r} "
          f"font={result.style_brief.font_personality}")
    print(f"Passes used: {result.passes_used}")
    print(f"Final review: overall_pass={result.review_history[-1].overall_pass}")
    print(f"Final blend check: blends_well={result.blend_review_history[-1].blends_well}")


if __name__ == "__main__":
    main()
