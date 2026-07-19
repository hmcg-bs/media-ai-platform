"""End-to-end demo: real copy (Datalab) + imagery (Qwen3-VL) → retrieval embeddings.

Reads the saved Style-Preserver output for the copy, describes the imagery with Qwen3-VL,
embeds both with embedding-gemma, and writes <ad_id>.embeddings.json. Makes 3 paid calls;
uses higher retry + spacing to ride out Replicate's low-credit rate limit.

Run:  uv run python scripts/creative_embeddings_demo.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.clients.replicate_client import EmbeddingClient, QwenVLClient  # noqa: E402
from pipeline.config import get_settings  # noqa: E402
from pipeline.datalab.models import DatalabDocument  # noqa: E402
from pipeline.embedding.embed import embed_creative  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / "example_creatives" / "meta_ad_exp.jpg"
OUT_DIR = Path(__file__).parent / "datalab_out"
STYLE_JSON = OUT_DIR / "styleprocessor.json"


def main() -> None:
    ad_id = IMAGE.stem
    # Generous retry so back-to-back paid calls survive the 6/min, burst-1 rate limit.
    settings = get_settings().model_copy(
        update={
            "api_max_attempts": 6,
            "api_backoff_min_seconds": 8.0,
            "api_backoff_max_seconds": 20.0,
        }
    )

    # 1. Copy — from the already-parsed Datalab document (no paid call).
    doc = DatalabDocument.model_validate(json.loads(STYLE_JSON.read_text()))
    copy_text = " ".join(r.text.replace("\n", " ") for r in doc.all_text_runs())
    print(f"copy ({len(copy_text)} chars): {copy_text[:90]}…")

    # 2. Imagery — Qwen3-VL (paid).
    imagery_text = QwenVLClient(settings=settings).describe(
        IMAGE.read_bytes(), settings.imagery_prompt
    )
    print(f"imagery ({len(imagery_text)} chars): {imagery_text[:90]}…")
    time.sleep(12)  # space out before the embedding calls

    # 3. Embeddings — copy + imagery (paid).
    emb = embed_creative(
        EmbeddingClient(settings=settings), copy_text, imagery_text, ad_id=ad_id
    )

    out_path = OUT_DIR / f"{ad_id}.embeddings.json"
    out_path.write_text(emb.model_dump_json(indent=2))
    print(
        f"\ndim={emb.dim}  copy_vec={len(emb.copy_embedding)}  "
        f"imagery_vec={len(emb.imagery_embedding)}  -> {out_path}"
    )


if __name__ == "__main__":
    main()
