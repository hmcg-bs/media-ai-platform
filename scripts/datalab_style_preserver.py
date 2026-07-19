"""Run the 'Style Preserver' custom processor (cp_FQN-x-6oKRoZ) on the ad image and diff
its block JSON against the plain convert output, to see what styling it actually adds.

A custom processor is invoked via run_custom_processor with its cp_ id passed as the
(misleadingly named) `pipeline_id` field. It returns a ConversionResult like convert().

Run:  uv run python scripts/datalab_style_preserver.py            (needs DATALAB_API_KEY)
      uv run python scripts/datalab_style_preserver.py --diff-only  (diff saved outputs)
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from datalab_sdk import DatalabClient
from datalab_sdk.models import CustomProcessorOptions

ROOT = Path(__file__).parent.parent
DEFAULT_IMAGE = ROOT / "example_creatives" / "meta_ad_exp.jpg"
OUT_DIR = Path(__file__).parent / "datalab_out"
PLAIN_CONVERT = OUT_DIR / "convert.json"      # baseline from datalab_ad_pipeline.py
STYLE_OUT = OUT_DIR / "styleprocessor.json"
PROCESSOR_ID = "cp_FQN-x-6oKRoZ"              # "Style Preserver"

STYLE_ATTR = re.compile(r'style="([^"]*)"')
COLOR_HINT = re.compile(
    r"#[0-9a-fA-F]{3,6}\b|rgb\(|color\s*:|font-size\s*:|font-family\s*:|font-weight\s*:"
)
EMPHASIS = re.compile(r"<(b|strong|i|em)\b")


def run_style_preserver(client: DatalabClient, image: Path) -> dict:
    """Execute the custom processor and save its block JSON."""
    options = CustomProcessorOptions(
        pipeline_id=PROCESSOR_ID,
        output_format="json",
        mode="accurate",
        add_block_ids=True,
        disable_image_extraction=True,
    )
    result = client.run_custom_processor(str(image), options=options)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STYLE_OUT.write_text(json.dumps(result.json, indent=2))
    print(f"style processor: status={result.status} -> {STYLE_OUT.name}")
    if getattr(result, "cost_breakdown", None):
        print(f"  cost: {result.cost_breakdown}")
    return result.json


def _iter_blocks(node: Any):
    if isinstance(node, dict):
        if "block_type" in node and node.get("id"):
            yield node
        for child in node.get("children") or []:
            yield from _iter_blocks(child)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_blocks(item)


def _profile(convert_json: Any) -> dict:
    """Summarize a convert JSON: block-type counts, inline styles, emphasis, style hints."""
    types: Counter[str] = Counter()
    styles: Counter[str] = Counter()
    emphasis = 0
    color_hits = 0
    for b in _iter_blocks(convert_json):
        types[b.get("block_type", "?")] += 1
        html = b.get("html") or ""
        for m in STYLE_ATTR.findall(html):
            styles[m.strip()] += 1
        emphasis += len(EMPHASIS.findall(html))
        color_hits += len(COLOR_HINT.findall(html))
    return {"types": types, "styles": styles, "emphasis": emphasis, "color_hits": color_hits}


def diff(plain_json: Any, style_json: Any) -> None:
    p, s = _profile(plain_json), _profile(style_json)
    print("\n=== Style Preserver vs plain convert ===")
    print(f"blocks:        plain={sum(p['types'].values())}  style={sum(s['types'].values())}")
    print(f"block types:   plain={dict(p['types'])}")
    print(f"               style={dict(s['types'])}")
    print(f"emphasis tags: plain={p['emphasis']}  style={s['emphasis']}  (<b>/<strong>/<i>/<em>)")
    print(f"style/color hints (hex/rgb/color:/font-*): plain={p['color_hits']}  "
          f"style={s['color_hits']}")

    new_styles = {k: v for k, v in s["styles"].items() if k not in p["styles"]}
    print(f"\ninline style= attrs unique to Style Preserver ({len(new_styles)}):")
    for k, v in list(new_styles.items())[:20]:
        print(f"    ({v}x) {k}")
    if not new_styles:
        print("    (none — Style Preserver added no new inline style attributes)")

    verdict = (
        "adds real inline styling (fonts/colors)"
        if s["color_hits"] > p["color_hits"] or new_styles
        else "no new font/color info — same structural output as plain convert"
    )
    print(f"\nverdict: Style Preserver {verdict}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--diff-only", action="store_true", help="Diff saved outputs; no API call.")
    args = parser.parse_args()

    if args.diff_only:
        if not STYLE_OUT.exists():
            parser.error(f"no {STYLE_OUT} — run once without --diff-only first")
        style_json = json.loads(STYLE_OUT.read_text())
    else:
        style_json = run_style_preserver(DatalabClient(), args.image)

    if not PLAIN_CONVERT.exists():
        print(f"\n(no {PLAIN_CONVERT} to diff against — run datalab_ad_pipeline.py first)")
        return
    diff(json.loads(PLAIN_CONVERT.read_text()), style_json)


if __name__ == "__main__":
    main()
