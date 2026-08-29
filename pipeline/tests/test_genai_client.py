"""Tests for GenAIClient.generate_image -- injected fake client, no real
Vertex/network calls. Response shape mirrors the real google-genai SDK
(confirmed via Context7 docs, not assumed): `response.parts[i].inline_data.data`."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import BaseModel

from pipeline.clients.genai_client import GenAIClient
from pipeline.config import Settings


class _FakeInlineData:
    def __init__(self, data: bytes):
        self.data = data


class _FakePart:
    def __init__(self, inline_data: _FakeInlineData | None):
        self.inline_data = inline_data


class _FakeResponse:
    def __init__(self, parts: list[_FakePart], text: str = ""):
        self.parts = parts
        self.text = text


class _FakeModels:
    def __init__(self, response: _FakeResponse, calls: list):
        self._response = response
        self._calls = calls

    def generate_content(self, **kwargs):
        self._calls.append(kwargs)
        return self._response


class _FakeGenAIClient:
    def __init__(self, response: _FakeResponse, calls: list):
        self.models = _FakeModels(response, calls)


class TestServiceAccountCredentials:
    """Same fix as VisionClient (see test_vision_client.py): a service
    account key sidesteps Google's periodic interactive-reauth policy on
    user ADC. When configured, _ensure_client must build explicit
    credentials from the key file rather than falling back to ADC."""

    def test_uses_explicit_credentials_when_key_path_configured(self) -> None:
        settings = Settings(
            google_application_credentials_path="/tmp/fake-key.json",
            gcp_project_id="proj", vertex_location="us-central1",
        )
        client = GenAIClient()
        client._settings = settings

        with (
            patch(
                "google.oauth2.service_account.Credentials.from_service_account_file"
            ) as from_file,
            patch("google.genai.Client") as genai_client_cls,
        ):
            from_file.return_value = "fake-credentials-object"
            client._ensure_client()

            from_file.assert_called_once_with(
                "/tmp/fake-key.json",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            genai_client_cls.assert_called_once_with(
                vertexai=True, project="proj", location="us-central1",
                credentials="fake-credentials-object",
            )

    def test_falls_back_to_adc_when_no_key_path_configured(self) -> None:
        settings = Settings(
            google_application_credentials_path="", impersonate_service_account="",
            gcp_project_id="proj", vertex_location="us-central1",
        )
        client = GenAIClient()
        client._settings = settings

        with patch("google.genai.Client") as genai_client_cls:
            client._ensure_client()
            genai_client_cls.assert_called_once_with(
                vertexai=True, project="proj", location="us-central1", credentials=None,
            )


class TestGenerateImage:
    def test_returns_first_inline_image_bytes(self):
        calls: list = []
        fake = _FakeGenAIClient(
            _FakeResponse([_FakePart(_FakeInlineData(b"png-bytes"))]), calls
        )
        client = GenAIClient(client=fake)

        result = client.generate_image(model="gemini-3.1-flash-image", prompt="a studio background")

        assert result == b"png-bytes"
        assert calls[0]["model"] == "gemini-3.1-flash-image"

    def test_passes_reference_images_before_prompt(self):
        calls: list = []
        fake = _FakeGenAIClient(
            _FakeResponse([_FakePart(_FakeInlineData(b"png-bytes"))]), calls
        )
        client = GenAIClient(client=fake)

        client.generate_image(
            model="gemini-3.1-flash-image",
            prompt="place this product on a beach",
            reference_images=[(b"ref-bytes", "image/png")],
        )

        contents = calls[0]["contents"]
        assert len(contents) == 2  # reference image part + prompt string
        assert contents[-1] == "place this product on a beach"

    def test_raises_when_no_image_part_in_response(self):
        calls: list = []
        fake = _FakeGenAIClient(
            _FakeResponse([_FakePart(None)], text="I can't generate that."), calls
        )
        client = GenAIClient(client=fake)

        with pytest.raises(ValueError, match="no image part"):
            client.generate_image(model="gemini-3.1-flash-image", prompt="anything")

    def test_skips_non_image_parts_and_returns_first_image(self):
        calls: list = []
        fake = _FakeGenAIClient(
            _FakeResponse([_FakePart(None), _FakePart(_FakeInlineData(b"real-image"))]), calls
        )
        client = GenAIClient(client=fake)

        result = client.generate_image(model="gemini-3.1-flash-image", prompt="anything")
        assert result == b"real-image"


class _FakeStyle(BaseModel):
    note: str


class _FakeStructuredResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = ""


class _FakeStructuredModels:
    def __init__(self, response, calls: list):
        self._response = response
        self._calls = calls

    def generate_content(self, **kwargs):
        self._calls.append(kwargs)
        return self._response


class _FakeStructuredGenAIClient:
    def __init__(self, response, calls: list):
        self.models = _FakeStructuredModels(response, calls)


class TestExtractStructuredMultiImage:
    def test_passes_all_images_before_prompt(self):
        calls: list = []
        expected = _FakeStyle(note="ok")
        fake = _FakeStructuredGenAIClient(_FakeStructuredResponse(expected), calls)
        client = GenAIClient(client=fake)

        result = client.extract_structured_multi_image(
            model="gemini-2.5-flash",
            prompt="describe the style",
            images=[(b"img1", "image/jpeg"), (b"img2", "image/jpeg")],
            schema=_FakeStyle,
        )

        assert result == expected
        contents = calls[0]["contents"]
        assert len(contents) == 3  # 2 image parts + prompt string
        assert contents[-1] == "describe the style"

    def test_falls_back_to_json_parse_when_sdk_does_not_preparse(self):
        calls: list = []
        response = _FakeStructuredResponse(parsed=None)
        response.text = '{"note": "from raw json"}'
        fake = _FakeStructuredGenAIClient(response, calls)
        client = GenAIClient(client=fake)

        result = client.extract_structured_multi_image(
            model="gemini-2.5-flash", prompt="x", images=[(b"img1", "image/jpeg")],
            schema=_FakeStyle,
        )

        assert result.note == "from raw json"
