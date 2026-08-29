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

            from pipeline.clients.gcp_auth import resolve_credentials

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.gcp_project_id,
                location=self._settings.vertex_location,
                credentials=resolve_credentials(self._settings),
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
    def extract_structured_multi_image(
        self,
        *,
        model: str,
        prompt: str,
        images: list[tuple[bytes, str]],
        schema: type[T],
    ) -> T:
        """Like extract_structured, but for multiple reference images in one
        call (e.g. several real example ads) -- Gemini accepts any number of
        image Parts ahead of the text prompt. Used for retrieval-grounded
        style analysis (style_reference.py), not per-ad extraction."""
        from google.genai import types

        client = self._ensure_client()
        logger.debug(
            "api_call_attempted", api="genai.generate_content", model=model, n_images=len(images)
        )

        contents: list[object] = [
            types.Part.from_bytes(data=data, mime_type=mime_type) for data, mime_type in images
        ]
        contents.append(prompt)

        response = client.models.generate_content(
            model=model,
            contents=contents,
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        reference_images: list[tuple[bytes, str]] | None = None,
        aspect_ratio: str = "1:1",
    ) -> bytes:
        """Generate an image via a Gemini image-output model (e.g.
        ``gemini-3.1-flash-image``, aka "Nano Banana Pro"). ``reference_images``
        is a list of ``(bytes, mime_type)`` pairs passed as multimodal context
        ahead of the prompt (e.g. a product photo) -- the model can use these
        for style/context but this is NOT the faithful masked-edit mechanism
        Flux Kontext provides (see ``ReplicateVisionClient`` for that); don't
        rely on this path when exact product-pixel fidelity matters.

        Returns the raw image bytes of the first IMAGE part in the response.
        Raises ``ValueError`` if the model returned no image part (e.g. it
        declined the prompt) -- never silently returns empty/placeholder bytes.
        """
        from google.genai import types

        client = self._ensure_client()
        logger.debug("api_call_attempted", api="genai.generate_content_image", model=model)

        contents: list[object] = []
        for data, mime_type in reference_images or []:
            contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        contents.append(prompt)

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )

        for part in response.parts:
            if part.inline_data:
                logger.debug("api_call_succeeded", api="genai.generate_content_image", model=model)
                return part.inline_data.data

        raise ValueError(
            f"generate_image: no image part in response for model={model!r} "
            f"(prompt may have been declined; check response.text for a refusal message)"
        )
