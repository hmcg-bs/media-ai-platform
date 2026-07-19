"""Ideal Datalab pipeline for a single ad creative: convert -> extract (balanced).

Design (see plan): Datalab's `extract` reads the *converted text*, not pixels, so this
pipeline maximizes what `convert` captures (text + per-block bounding boxes + image
captions) and asks `extract` only for text-recoverable features via a per-element schema.
Pixel-only features (font/color) are intentionally NOT in the schema.

Workflow — convert-once, extract-many:
  1. `convert` the image with save_checkpoint=True -> checkpoint_id + block JSON (bboxes).
  2. `extract` against the checkpoint with the schema. Re-run extract alone while tuning
     the schema; convert is not re-billed (pass --checkpoint or reuse out/checkpoint.txt).
  3. Merge: attach each text_element's bbox from the convert blocks (by text match).
  4. Enrich: fill the pixel-only features Datalab can't — font-size proxy (bbox height)
     and sampled text color — by cropping the original image at each bbox (numpy + PIL).

Run:  uv run python scripts/datalab_ad_pipeline.py            (needs DATALAB_API_KEY)
      uv run python scripts/datalab_ad_pipeline.py --extract-only   (reuse saved checkpoint)
"""

import argparse
import json
import math
import re
from html import unescape
from pathlib import Path
from typing import Any

import numpy as np
from datalab_sdk import DatalabClient
from datalab_sdk.models import ConvertOptions, ExtractOptions
from PIL import Image

# --- Paths ---
ROOT = Path(__file__).parent.parent
DEFAULT_IMAGE = ROOT / "example_creatives" / "meta_ad_exp.jpg"
SCHEMA_PATH = Path(__file__).parent / "schemas" / "ad_creative_schema.json"
OUT_DIR = Path(__file__).parent / "datalab_out"

# --- Options that shape the output (see plan's options catalog) ---
CONVERT_OPTIONS = ConvertOptions(
    output_format="json",          # REQUIRED for per-block bbox/polygon
    mode="accurate",               # best parsing fidelity for dense ad layouts
    disable_image_captions=False,  # keep captions on — the only "vision" extract gets
    disable_image_extraction=True,  # drop base64 crops (huge bloat); flip to False if
    #                                 captions vanish or you need the extracted images
    add_block_ids=True,            # enable citations back to block IDs
    save_checkpoint=True,          # returns checkpoint_id for extract to reuse
)
EXTRACTION_MODE = "balanced"       # balanced = +verification +reasoning +citations ($25/1k)


def run_convert(client: DatalabClient, image: Path) -> Any:
    """Parse the image to block JSON and persist the checkpoint for reuse."""
    result = client.convert(str(image), options=CONVERT_OPTIONS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "convert.json").write_text(json.dumps(result.json, indent=2))
    if result.checkpoint_id:
        (OUT_DIR / "checkpoint.txt").write_text(result.checkpoint_id)
    print(f"convert: status={result.status} checkpoint={result.checkpoint_id}")
    if result.cost_breakdown:
        print(f"  cost: {result.cost_breakdown}")
    return result


def run_extract(client: DatalabClient, checkpoint_id: str, schema_str: str) -> dict:
    """Run structured extraction against an existing convert checkpoint (no re-parse)."""
    options = ExtractOptions(
        checkpoint_id=checkpoint_id,
        page_schema=schema_str,
        mode=EXTRACTION_MODE,
        output_format="json",
    )
    result = client.extract(options=options)
    data = result.extraction_schema_json or {}
    if isinstance(data, str):  # the SDK returns the schema result as a JSON string
        data = json.loads(data)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "extract.json").write_text(json.dumps(data, indent=2))
    print(f"extract: status={result.status}")
    if result.cost_breakdown:
        print(f"  cost: {result.cost_breakdown}")
    return data


def _norm(text: str) -> str:
    """Normalize text for matching: lowercase alphanumerics + single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", unescape(text).lower())).strip()


def _iter_blocks(node: Any):
    """Yield every block dict in the convert JSON tree."""
    if isinstance(node, dict):
        if "block_type" in node and node.get("id"):
            yield node
        for child in node.get("children") or []:
            yield from _iter_blocks(child)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_blocks(item)


def _block_text(block: dict) -> str:
    """Strip HTML tags from a block to plain text."""
    return re.sub(r"<[^>]+>", " ", block.get("html") or "")


def merge_bboxes(extract_data: dict, convert_json: dict) -> dict:
    """Attach each text_element's bounding box from the convert blocks by text match.

    Coordinates come from `convert` (extract cannot produce them). We match against TEXT
    blocks only — image/table containers (Figure, Picture, Diagram, Table) carry a whole
    region bbox, not a per-line text bbox, so matching a label to them yields a nonsense
    box. Text nested inside a figure (e.g. diagram labels) therefore stays unmatched, which
    is the honest result: Datalab did not give it its own text bbox. Each block used once.
    """
    image_types = {"Figure", "Picture", "Diagram", "Image", "Table", "TableGroup"}
    blocks = [
        {"id": b["id"], "type": b.get("block_type"), "text": _block_text(b),
         "bbox": b.get("bbox"), "polygon": b.get("polygon")}
        for b in _iter_blocks(convert_json)
    ]
    for b in blocks:
        b["norm"] = _norm(b["text"])
    used: set[str] = set()

    def match(target: str) -> dict | None:
        cands = [
            b for b in blocks
            if b["id"] not in used and b["norm"] and b["type"] not in image_types
        ]
        exact = [b for b in cands if b["norm"] == target]
        if exact:
            return exact[0]
        # smallest text block that CONTAINS the target, within a length guard so a short
        # label can't bind to a big multi-line block
        contains = [b for b in cands if target in b["norm"] and len(b["norm"]) <= len(target) * 1.5]
        return min(contains, key=lambda b: len(b["norm"])) if contains else None

    merged = []
    for el in extract_data.get("text_elements") or []:
        target = _norm(el.get("text_content", ""))
        best = match(target) if target else None
        entry = {
            "text_content": el.get("text_content"),
            "semantic_role": el.get("semantic_role"),
            "block_id": best["id"] if best else None,
            "bbox": best["bbox"] if best else None,
            "polygon": best["polygon"] if best else None,
        }
        if best:
            used.add(best["id"])
        merged.append(entry)

    out = {
        "text_elements": merged,
        "visual_assets": extract_data.get("visual_assets"),
        "message": extract_data.get("message"),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "merged.json").write_text(json.dumps(out, indent=2))
    matched = sum(1 for e in merged if e["bbox"])
    print(f"merge: {matched}/{len(merged)} text elements matched to a bbox -> merged.json")
    return out


def _canvas_dims(convert_json: Any) -> tuple[float, float] | None:
    """Width/height of the convert canvas (the Page block bbox). Datalab may upscale the
    image, so bboxes are in this space, NOT the original image's pixel space."""
    for b in _iter_blocks(convert_json):
        if b.get("block_type") == "Page" and b.get("bbox"):
            x0, y0, x1, y1 = b["bbox"]
            return (x1 - x0, y1 - y0)
    return None


def _otsu(gray: np.ndarray) -> int:
    """Otsu's threshold on an 8-bit grayscale array (pure numpy, no cv2)."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_total = np.dot(np.arange(256), hist)
    w_b = 0.0
    sum_b = 0.0
    max_var = -1.0
    thresh = 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            thresh = t
    return thresh


def _text_color_hex(crop_rgb: np.ndarray) -> str | None:
    """Estimate the glyph (text) color in a text crop. Within a tight text bbox the
    background dominates, so the Otsu minority class is the text; return its mean color."""
    if crop_rgb.size == 0:
        return None
    gray = crop_rgb.mean(axis=2).astype(np.uint8)
    t = _otsu(gray)
    dark = gray <= t
    n_dark = int(dark.sum())
    text_mask = dark if n_dark <= (dark.size - n_dark) else ~dark
    text_px = crop_rgb[text_mask]
    if text_px.size == 0:
        return None
    r, g, b = (int(round(v)) for v in text_px.reshape(-1, 3).mean(axis=0))
    return f"#{r:02x}{g:02x}{b:02x}"


def enrich_typography(merged: dict, image_path: Path, convert_json: Any) -> dict:
    """Fill the pixel-only features Datalab can't: per-element font-size proxy (from bbox
    height) and sampled text color (from the original image), using Datalab's bbox as the
    crop region. Bboxes are scaled from convert-canvas space to original-image pixels."""
    canvas = _canvas_dims(convert_json)
    with Image.open(image_path) as im:
        img = np.asarray(im.convert("RGB"))
    img_h, img_w = img.shape[:2]
    cw, ch = canvas if canvas else (img_w, img_h)
    sx, sy = img_w / cw, img_h / ch

    enriched = 0
    for el in merged.get("text_elements") or []:
        bbox = el.get("bbox")
        el["line_count"] = (el.get("text_content") or "").count("\n") + 1
        if not bbox:
            el["font_size_pct_canvas"] = None
            el["font_size_px_image"] = None
            el["text_color_hex"] = None
            continue
        lines = max(el["line_count"], 1)
        block_h = bbox[3] - bbox[1]
        el["font_size_pct_canvas"] = round((block_h / lines) / ch * 100, 2)
        el["font_size_px_image"] = round((block_h / lines) * sy, 1)
        # crop original image using scaled bbox
        x0 = max(int(bbox[0] * sx), 0)
        y0 = max(int(bbox[1] * sy), 0)
        x1 = min(math.ceil(bbox[2] * sx), img_w)
        y1 = min(math.ceil(bbox[3] * sy), img_h)
        crop = img[y0:y1, x0:x1]
        el["text_color_hex"] = _text_color_hex(crop)
        enriched += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "enriched.json").write_text(json.dumps(merged, indent=2))
    print(f"enrich: added size+color to {enriched} text elements -> enriched.json")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Skip convert; reuse the checkpoint in out/checkpoint.txt (or --checkpoint).",
    )
    parser.add_argument("--checkpoint", help="Checkpoint id to reuse (implies --extract-only).")
    args = parser.parse_args()

    client = DatalabClient()  # reads DATALAB_API_KEY
    schema_str = args.schema.read_text()

    checkpoint_id = args.checkpoint
    convert_json: dict | None = None

    if not (args.extract_only or checkpoint_id):
        conv = run_convert(client, args.image)
        checkpoint_id = conv.checkpoint_id
        convert_json = conv.json
    else:
        if not checkpoint_id:
            cp_file = OUT_DIR / "checkpoint.txt"
            if not cp_file.exists():
                parser.error("no checkpoint: run once without --extract-only first")
            checkpoint_id = cp_file.read_text().strip()
        conv_file = OUT_DIR / "convert.json"
        if conv_file.exists():
            convert_json = json.loads(conv_file.read_text())
        print(f"reusing checkpoint {checkpoint_id}")

    if not checkpoint_id:
        raise SystemExit("convert did not return a checkpoint_id; cannot extract")

    extract_data = run_extract(client, checkpoint_id, schema_str)

    if convert_json:
        merged = merge_bboxes(extract_data, convert_json)
        if args.image.exists():
            enrich_typography(merged, args.image, convert_json)
        else:
            print(f"enrich: skipped (image not found at {args.image})")
    else:
        print("merge + enrich: skipped (no convert.json available)")

    print(f"\nAll outputs written to {OUT_DIR}")


if __name__ == "__main__":
    main()
