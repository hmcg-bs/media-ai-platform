"""Smoke test: describe the ad imagery with Qwen3-VL. Paid call; uploads the image.

Run:  uv run python scripts/qwen_vl_smoke.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.clients.replicate_client import QwenVLClient  # noqa: E402
from pipeline.config import get_settings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / "example_creatives" / "meta_ad_exp.jpg"


def main() -> None:
    settings = get_settings()
    print(f"prompt: {settings.imagery_prompt}\n")
    desc = QwenVLClient().describe(IMAGE.read_bytes(), settings.imagery_prompt)
    print("=== imagery_description ===")
    print(desc)


if __name__ == "__main__":
    main()
