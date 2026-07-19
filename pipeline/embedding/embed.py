"""Embed a creative's copy + imagery for retrieval-grounded suggestions (ADR-008 #4).

Two separate vectors — copy and imagery — so retrieval can match on either axis. These
supplement (not replace) statistical pattern-mining; they feed the Step-3 suggestion
retrieval. Empty text yields an empty vector (no wasted call).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pipeline.clients.replicate_client import EmbeddingClient


class CreativeEmbeddings(BaseModel):
    """Per-ad retrieval vectors. Stored as a separate artifact (768 floats × 2 is large)."""

    ad_id: str = ""
    dim: int = 0
    copy_embedding: list[float] = Field(default_factory=list)
    imagery_embedding: list[float] = Field(default_factory=list)


def embed_creative(
    client: EmbeddingClient,
    copy_text: str,
    imagery_text: str,
    ad_id: str = "",
    task: str = "retrieval_document",
) -> CreativeEmbeddings:
    """Embed the copy and imagery strings into separate vectors."""
    copy_vec = client.embed(copy_text, task=task) if copy_text.strip() else []
    imagery_vec = client.embed(imagery_text, task=task) if imagery_text.strip() else []
    dim = len(copy_vec) or len(imagery_vec)
    return CreativeEmbeddings(
        ad_id=ad_id,
        dim=dim,
        copy_embedding=copy_vec,
        imagery_embedding=imagery_vec,
    )
