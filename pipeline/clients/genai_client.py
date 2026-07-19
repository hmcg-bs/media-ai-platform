"""Wrapper around the google-genai SDK for Vertex-hosted Gemini calls.

Used by the cognitive stage for structured (JSON) extraction. The caller passes
a Pydantic model as ``response_schema``; the SDK enforces it and we parse the
response back into that model.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class GenAIClient:
    """Thin wrapper over ``genai.Client`` configured for Vertex AI."""

    def __init__(self, client: object | None = None):
        self._client = client
        self._settings = get_settings()

    def _ensure_client(self) -> object:
        if self._client is None:
            from google import genai  # lazy; needs ADC + project

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.gcp_project_id,
                location=self._settings.vertex_location,
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def extract_structured(
        self,
        *,
        model: str,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        schema: type[T],
    ) -> T:
        """Send image + prompt to Gemini; return the parsed Pydantic model.

        ``response_mime_type="application/json"`` + ``response_schema`` make the
        model emit schema-conformant JSON, which the SDK can hand back as a
        parsed instance (``response.parsed``).
        """
        from google.genai import types

        client = self._ensure_client()
        logger.debug("api_call_attempted", api="genai.generate_content", model=model)

        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            logger.debug("api_call_succeeded", api="genai.generate_content", model=model)
            return parsed

        # Fall back to parsing raw text if the SDK didn't pre-parse.
        return schema.model_validate_json(response.text)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def extract_structured_text(
        self,
        *,
        model: str,
        prompt: str,
        schema: type[T],
    ) -> T:
        """Send text prompt to Gemini; return the parsed Pydantic model (no image)."""
        from google.genai import types

        client = self._ensure_client()
        logger.debug("api_call_attempted", api="genai.generate_content", model=model)

        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            logger.debug("api_call_succeeded", api="genai.generate_content", model=model)
            return parsed

        return schema.model_validate_json(response.text)
