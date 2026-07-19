"""Embedding module tests — mocked EmbeddingClient, no network/paid calls."""

from __future__ import annotations

from pipeline.clients.replicate_client import EmbeddingClient
from pipeline.embedding.embed import embed_creative


def _client(vec: list[float]) -> EmbeddingClient:
    calls: list[dict] = []

    def run(model, inputs):
        calls.append(inputs)
        return vec

    client = EmbeddingClient(run=run)
    client.calls = calls  # expose for assertions
    return client


def test_embeds_copy_and_imagery_separately():
    client = _client([0.1, 0.2, 0.3])
    out = embed_creative(client, "4 scoops a day", "a hand holding a scoop", ad_id="ad1")
    assert out.ad_id == "ad1"
    assert out.dim == 3
    assert out.copy_embedding == [0.1, 0.2, 0.3]
    assert out.imagery_embedding == [0.1, 0.2, 0.3]
    assert len(client.calls) == 2                       # one call per non-empty text
    assert client.calls[0]["task"] == "retrieval_document"


def test_empty_text_skips_call():
    client = _client([0.5, 0.6])
    out = embed_creative(client, "", "some imagery", ad_id="ad2")
    assert out.copy_embedding == []                     # empty copy → no vector
    assert out.imagery_embedding == [0.5, 0.6]
    assert out.dim == 2
    assert len(client.calls) == 1                       # only imagery embedded


def test_query_task_passthrough():
    client = _client([1.0])
    embed_creative(client, "hello", "", task="retrieval_query")
    assert client.calls[0]["task"] == "retrieval_query"
