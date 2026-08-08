"""Generate text embeddings for ad copy."""

from __future__ import annotations

from typing import Any

import numpy as np


def get_sentence_bert_embedding(text: str | None) -> list[float]:
    """
    Get Sentence-BERT embedding for text.

    Returns a 384-dimensional embedding vector.
    In production, this uses Sentence-Transformers library.
    For testing, returns mock 384-dim vector.
    """
    if not text or len(text.strip()) == 0:
        # Return zero vector for empty text
        return [0.0] * 384

    # In production, use:
    # from sentence_transformers import SentenceTransformer
    # model = SentenceTransformer('all-MiniLM-L6-v2')
    # embedding = model.encode(text)
    # return embedding.tolist()

    # For now, return deterministic mock (will be replaced in WEEK 2)
    import hashlib

    hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
    rng = np.random.RandomState(hash_val % (2**32))
    embedding = rng.normal(0, 1, 384).astype(np.float32)

    # Normalize to unit length
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm

    return embedding.tolist()


def extract_embedding_features(
    title: str | None,
    body: str | None,
    usp: str | None,
) -> dict[str, list[float]]:
    """Extract Sentence-BERT embeddings for title, body, and USP."""
    return {
        "title_embedding": get_sentence_bert_embedding(title),
        "body_embedding": get_sentence_bert_embedding(body[:300] if body else None),
        "usp_embedding": get_sentence_bert_embedding(usp),
    }
