"""One-off smoke test: run Qwen-Image-Layered on the example ad and inspect the output.

Learns the real output shape (how many layers, order, format, alpha coverage) so the
deterministic layer→colour stage can be built correctly. Paid call; uploads the image.

Run:  uv run python scripts/qwen_layers_smoke.py
"""

import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.clients.replicate_client import QwenLayersClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / "example_creatives" / "meta_ad_exp.jpg"
OUT_DIR = Path(__file__).parent / "layers_out"


def alpha_coverage(img: Image.Image) -> float:
    """Fraction of pixels with alpha > 0 (how much of the canvas this layer fills)."""
    if img.mode != "RGBA":
        return 1.0
    alpha = img.split()[-1]
    hist = alpha.histogram()
    opaque = sum(hist[1:])  # any alpha > 0
    total = img.width * img.height
    return round(opaque / total, 3) if total else 0.0


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"decomposing {IMAGE.name} …")
    layers = QwenLayersClient().decompose(IMAGE.read_bytes())
    print(f"got {len(layers)} layers\n")

    for i, data in enumerate(layers):
        path = OUT_DIR / f"layer_{i}.png"
        path.write_bytes(data)
        with Image.open(io.BytesIO(data)) as img:
            print(
                f"  layer_{i}: {img.size} mode={img.mode} "
                f"alpha_coverage={alpha_coverage(img)}  bytes={len(data)} -> {path.name}"
            )
    print(f"\nsaved to {OUT_DIR}")


if __name__ == "__main__":
    main()
