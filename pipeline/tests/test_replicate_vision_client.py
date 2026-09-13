from __future__ import annotations

from pydantic import BaseModel

from pipeline.clients.replicate_client import ReplicateVisionClient
from pipeline.config import Settings


class _Nested(BaseModel):
    value: str = ""


class _Response(BaseModel):
    nested: _Nested


def test_structured_vision_supplies_json_schema_to_model() -> None:
    captured: dict = {}

    def run(model: str, inputs: dict):
        captured.update(inputs)
        return '{"nested":{"value":"ok"}}'

    client = ReplicateVisionClient(run=run, settings=Settings(replicate_api_token="test"))
    response = client.extract_structured("Analyze", b"image", _Response)

    assert response.nested.value == "ok"
    assert "JSON Schema" in captured["prompt"]
    assert '"nested"' in captured["prompt"]
    assert captured["images"][0].startswith("data:image/jpeg;base64,")
