"""Replicate client tests — injected runner, no network/paid calls."""

from __future__ import annotations

import io

import httpx
from replicate.exceptions import ReplicateError

from pipeline.clients.replicate_client import (
    BackgroundRemoverClient,
    EmbeddingClient,
    FluxFillClient,
    FluxKontextClient,
    QwenLayersClient,
    QwenVLClient,
    _is_retryable,
    _to_bytes,
)
from pipeline.config import Settings

# Fast backoff so retry tests don't actually sleep.
_FAST = Settings(api_max_attempts=3, api_backoff_min_seconds=0.01, api_backoff_max_seconds=0.02)


class _FakeError(ReplicateError):
    def __init__(self, status: int):
        self.status = status


class _FileOutput:
    """Stand-in for a replicate FileOutput (file-like)."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_qwen_layers_passes_input_and_reads_layers():
    calls: list[tuple[str, dict]] = []

    def run(model, inputs):
        calls.append((model, inputs))
        return [_FileOutput(b"layer0"), _FileOutput(b"layer1")]

    layers = QwenLayersClient(run=run).decompose(b"\x89PNG-fake")
    assert layers == [b"layer0", b"layer1"]
    model, inputs = calls[0]
    assert model == "qwen/qwen-image-layered"
    assert isinstance(inputs["image"], io.BytesIO)
    assert inputs["num_layers"] == 4
    assert inputs["output_format"] == "png"


def test_flux_kontext_passes_real_schema_fields_and_reads_edited_image():
    calls: list[tuple[str, dict]] = []

    def run(model, inputs):
        calls.append((model, inputs))
        return _FileOutput(b"edited-image-bytes")

    edited = FluxKontextClient(run=run).edit(
        b"\xff\xd8fake-jpeg", "swap the background to a beach scene"
    )
    assert edited == b"edited-image-bytes"
    model, inputs = calls[0]
    assert model == "black-forest-labs/flux-kontext-pro"
    assert inputs["prompt"] == "swap the background to a beach scene"
    assert isinstance(inputs["input_image"], io.BytesIO)
    assert inputs["aspect_ratio"] == "match_input_image"
    assert inputs["output_format"] == "png"


def test_flux_kontext_normalizes_list_output_to_single_image():
    def run(model, inputs):
        return [_FileOutput(b"first-of-batch")]

    edited = FluxKontextClient(run=run).edit(b"\xff\xd8fake-jpeg", "prompt")
    assert edited == b"first-of-batch"


def test_background_remover_passes_rgba_and_reads_cutout():
    calls: list[tuple[str, dict]] = []

    def run(model, inputs):
        calls.append((model, inputs))
        return _FileOutput(b"rgba-cutout-bytes")

    cutout = BackgroundRemoverClient(run=run).remove_background(b"\xff\xd8fake-jpeg")
    assert cutout == b"rgba-cutout-bytes"
    model, inputs = calls[0]
    assert model.startswith("851-labs/background-remover")
    assert isinstance(inputs["image"], io.BytesIO)
    assert inputs["background_type"] == "rgba"


def test_flux_fill_passes_mask_and_default_guidance_from_settings():
    """Round 8: guidance defaults from settings.flux_fill_guidance (raised
    to 85 after live evidence the default let no-text/no-duplicate rules get
    violated) unless explicitly overridden per call."""
    calls: list[tuple[str, dict]] = []

    def run(model, inputs):
        calls.append((model, inputs))
        return _FileOutput(b"filled-image-bytes")

    filled = FluxFillClient(run=run).inpaint(b"\xff\xd8photo", b"mask-bytes", "fill the scene")
    assert filled == b"filled-image-bytes"
    model, inputs = calls[0]
    assert model == "black-forest-labs/flux-fill-pro"
    assert inputs["prompt"] == "fill the scene"
    assert isinstance(inputs["image"], io.BytesIO)
    assert isinstance(inputs["mask"], io.BytesIO)
    assert inputs["guidance"] == 85.0


def test_flux_fill_guidance_can_be_overridden_per_call():
    calls: list[tuple[str, dict]] = []

    def run(model, inputs):
        calls.append((model, inputs))
        return _FileOutput(b"filled-image-bytes")

    FluxFillClient(run=run).inpaint(b"photo", b"mask", "prompt", guidance=40.0)
    assert calls[0][1]["guidance"] == 40.0


def test_qwen_vl_returns_string():
    def run(model, inputs):
        assert model.startswith("lucataco/qwen3-vl-8b-instruct")
        assert inputs["prompt"] == "Describe the product."
        assert isinstance(inputs["media"], io.BytesIO)
        return "A hand holding a scoop of white powder."

    out = QwenVLClient(run=run).describe(b"img", prompt="Describe the product.")
    assert out == "A hand holding a scoop of white powder."


def test_qwen_vl_joins_streamed_chunks():
    def run(model, inputs):
        return ["A hand ", "holding ", "a scoop."]

    out = QwenVLClient(run=run).describe(b"img", prompt="x")
    assert out == "A hand holding a scoop."


def test_embedding_returns_float_vector():
    captured: dict = {}

    def run(model, inputs):
        captured.update(inputs)
        assert model.startswith("zsxkib/embedding-gemma-300m")
        return [0.1, 0.2, 0.3]

    vec = EmbeddingClient(run=run).embed("4 scoops a day", task="retrieval_query")
    assert vec == [0.1, 0.2, 0.3]
    assert captured["text"] == "4 scoops a day"
    assert captured["task"] == "retrieval_query"
    assert captured["output_format"] == "array"
    assert captured["embedding_dim"] == 768


def test_is_retryable_classification():
    assert _is_retryable(httpx.ReadTimeout("x")) is True
    assert _is_retryable(_FakeError(429)) is True
    assert _is_retryable(_FakeError(503)) is True
    assert _is_retryable(_FakeError(404)) is False       # our bug, don't retry
    assert _is_retryable(ValueError("x")) is False


def test_is_retryable_covers_transient_httpx_transport_errors():
    # Regression: a real full-corpus build_matrix.py run crashed on an
    # unretried httpx.RemoteProtocolError ("Server disconnected without
    # sending a response") after ~935 ads' worth of paid API calls, since
    # only httpx.TimeoutException was covered. httpx.TransportError is the
    # shared base for these transient connection failures.
    assert _is_retryable(httpx.RemoteProtocolError("Server disconnected")) is True
    assert _is_retryable(httpx.ConnectError("x")) is True
    assert _is_retryable(httpx.ReadError("x")) is True


def test_retries_on_remote_protocol_error_then_succeeds():
    attempts = {"n": 0}

    def run(model, inputs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return [0.4, 0.5]

    vec = EmbeddingClient(run=run, settings=_FAST).embed("hi")
    assert vec == [0.4, 0.5]
    assert attempts["n"] == 2                        # retried once


def test_retries_on_throttle_then_succeeds():
    attempts = {"n": 0}

    def run(model, inputs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _FakeError(429)                          # throttled once
        return [0.4, 0.5]

    vec = EmbeddingClient(run=run, settings=_FAST).embed("hi")
    assert vec == [0.4, 0.5]
    assert attempts["n"] == 2                        # retried once


def test_does_not_retry_non_retryable():
    attempts = {"n": 0}

    def run(model, inputs):
        attempts["n"] += 1
        raise _FakeError(404)                              # 404 → give up immediately

    import pytest

    with pytest.raises(ReplicateError):
        QwenVLClient(run=run, settings=_FAST).describe(b"x", prompt="p")
    assert attempts["n"] == 1                        # no retry


def test_to_bytes_handles_str_url(monkeypatch):
    import pipeline.clients.replicate_client as mod

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"downloaded"

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda url: _Resp())
    assert _to_bytes("https://replicate.delivery/x.png") == b"downloaded"
    assert _to_bytes(b"raw") == b"raw"
    assert _to_bytes(_FileOutput(b"fileout")) == b"fileout"
