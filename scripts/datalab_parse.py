"""Parse a saved Datalab Style-Preserver output into a DatalabDocument and persist it.

Reads the raw block JSON, validates it into the typed model (which parses each block's
HTML into text_runs + styles + bbox size proxy), and writes the parsed document to disk
for inspection. Base64 image blobs are replaced with a short placeholder so the output is
readable.

Run:  uv run python scripts/datalab_parse.py
      uv run python scripts/datalab_parse.py --input <file> --out <file>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make the project root importable when run as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.datalab.color import measure_text_colors  # noqa: E402
from pipeline.datalab.models import DatalabDocument  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).parent / "datalab_out"
DEFAULT_INPUT = OUT_DIR / "styleprocessor.json"
DEFAULT_OUTPUT = OUT_DIR / "parsed.json"
DEFAULT_IMAGE = ROOT / "example_creatives" / "meta_ad_exp.jpg"


def strip_base64_images(node: Any) -> Any:
    """Replace base64 image strings with a short placeholder, in place, recursively."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "images" and isinstance(value, dict):
                node[key] = {k: f"<base64 JPEG, {len(v)} chars>" for k, v in value.items()}
            else:
                strip_base64_images(value)
    elif isinstance(node, list):
        for item in node:
            strip_base64_images(item)
    return node


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    args = parser.parse_args()

    raw = json.loads(args.input.read_text())
    doc = DatalabDocument.model_validate(raw)

    # Fill color_measured from the real image pixels (authoritative colour).
    if args.image.exists():
        measure_text_colors(doc, args.image.read_bytes())
    else:
        print(f"warning: image not found at {args.image}; color_measured left null")

    dumped = strip_base64_images(doc.model_dump(mode="json"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dumped, indent=2))

    runs = doc.all_text_runs()
    print(f"parsed {args.input.name} -> {args.out}")
    print(f"  canvas={doc.canvas}  blocks={sum(1 for _ in doc.blocks())}  text_runs={len(runs)}")
    for r in runs:
        s = r.style
        size = f"{s.font_size_pct_canvas}%" if s.font_size_pct_canvas is not None else "—"
        print(
            f"    [{r.style.text_align or '?':<6}] size={size:<6} bold={int(s.bold)} "
            f"reported={s.color_reported} measured={s.color_measured or '—'} "
            f"{r.text.replace(chr(10), ' / ')!r}"
        )


if __name__ == "__main__":
    main()
