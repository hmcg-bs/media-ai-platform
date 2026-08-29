"""Generate text embeddings for ad copy.

Uses the same Replicate embedding-gemma client already proven elsewhere in
the pipeline (pipeline/embedding/embed.py's embed_creative, ADR-008) rather
than a separate model — one embedding provider for the whole project. Was
previously a hash-seeded pseudo-random mock (384-dim, explicitly marked "will
be replaced" in a comment) that carried zero semantic signal: two texts with
similar meaning produced uncorrelated vectors, since a hash doesn't preserve
similarity. Real embeddings are 768-dim (embedding-gemma's default, per
Settings.embedding_dim), not 384.
"""

from __future__ import annotations

from pipeline.clients.replicate_client import EmbeddingClient


def extract_embedding_features(
    title: str | None,
    body: str | None,
    usp: str | None,
    client: EmbeddingClient | None = None,
) -> dict[str, list[float]]:
    """Embed title, body (truncated to 300 chars), and USP via embedding-gemma.

    Empty/None text yields an empty vector rather than a wasted API call —
    same convention as embed_creative(). `client` is injectable for offline
    tests; defaults to a real (lazily-constructed) EmbeddingClient."""
    client = client or EmbeddingClient()

    def _embed(text: str | None) -> list[float]:
        return client.embed(text) if text and text.strip() else []

    return {
        "title_embedding": _embed(title),
        "body_embedding": _embed(body[:300] if body else None),
        "usp_embedding": _embed(usp),
    }
