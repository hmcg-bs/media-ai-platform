"""Smoke test: embed a sample copy string with embedding-gemma. Paid call.

Run:  uv run python scripts/embedding_smoke.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.clients.replicate_client import EmbeddingClient  # noqa: E402

SAMPLE_COPY = (
    "4 SCOOPS OF ARMRA COLOSTRUM A DAY UNLOCKS: STRENGTHENED IMMUNITY, "
    "VITALIZED HAIR, ENHANCED SKIN, COMBATTED BLOATING, AND THOUSANDS MORE "
    "WHOLE BODY BENEFITS"
)


def main() -> None:
    client = EmbeddingClient()
    vec = client.embed(SAMPLE_COPY, task="retrieval_document")
    print(f"copy embedding: dim={len(vec)}  first5={[round(x, 4) for x in vec[:5]]}")


if __name__ == "__main__":
    main()
