"""Tests for pipeline/feature_engineering/embeddings.py — mocked
EmbeddingClient (real class, injected run callable), no network/paid calls.

Regression: this used to be a hash-seeded pseudo-random mock carrying zero
semantic signal (comment said "will be replaced in WEEK 2"). Now wired to
the real, already-proven Replicate embedding-gemma client (same one
pipeline/embedding/embed.py's embed_creative uses)."""

from __future__ import annotations

from pipeline.clients.replicate_client import EmbeddingClient
from pipeline.feature_engineering.embeddings import extract_embedding_features


def _client(vec: list[float]) -> EmbeddingClient:
    calls: list[dict] = []

    def run(model, inputs):
        calls.append(inputs)
        return vec

    client = EmbeddingClient(run=run)
    client.calls = calls  # expose for assertions
    return client


def test_embeds_title_body_and_usp():
    client = _client([0.1, 0.2, 0.3])
    result = extract_embedding_features(
        title="Buy Now", body="Great supplement for daily use", usp="Clinically proven",
        client=client,
    )
    assert result["title_embedding"] == [0.1, 0.2, 0.3]
    assert result["body_embedding"] == [0.1, 0.2, 0.3]
    assert result["usp_embedding"] == [0.1, 0.2, 0.3]
    assert len(client.calls) == 3


def test_none_fields_skip_the_call_entirely():
    client = _client([0.5])
    result = extract_embedding_features(title=None, body=None, usp=None, client=client)
    assert result["title_embedding"] == []
    assert result["body_embedding"] == []
    assert result["usp_embedding"] == []
    assert client.calls == []


def test_empty_string_fields_skip_the_call():
    client = _client([0.5])
    result = extract_embedding_features(title="", body="   ", usp="", client=client)
    assert result["title_embedding"] == []
    assert result["body_embedding"] == []
    assert client.calls == []


def test_body_is_truncated_to_300_chars_before_embedding():
    client = _client([1.0])
    long_body = "x" * 500
    extract_embedding_features(title=None, body=long_body, usp=None, client=client)
    assert len(client.calls[0]["text"]) == 300


def test_mixed_present_and_empty_fields():
    client = _client([1.0, 2.0])
    result = extract_embedding_features(title="Real Title", body=None, usp="", client=client)
    assert result["title_embedding"] == [1.0, 2.0]
    assert result["body_embedding"] == []
    assert result["usp_embedding"] == []
    assert len(client.calls) == 1
