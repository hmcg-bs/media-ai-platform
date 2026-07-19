"""Thin, mockable clients for the Replicate-hosted models (ADR-008).

Four models power the colour-scheme, imagery, retrieval, and cognitive components:

- ``QwenLayersClient``    — Qwen-Image-Layered: image → list of RGBA layer PNGs.
- ``QwenVLClient``        — Qwen3-VL: image + prompt → description string.
- ``EmbeddingClient``     — embedding-gemma: text → embedding vector.
- ``ReplicateVisionClient`` — google/gemini-3-flash: image + prompt → structured JSON.

Each is constructed lazily and accepts an injected ``run`` callable so tests can mock
it without network/paid calls. Model ids and the API token come from ``get_settings()``
(no ``os.environ`` access here). ADR-008: this is the Replicate *prototype*; the same
interfaces will later repoint to Vertex / Model Garden via ``settings.model_provider``.
"""

from __future__ import annotations

import io
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel
from replicate.exceptions import ReplicateError
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from pipeline.config import Settings, get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)

# A runner takes (model_ref, input_dict) and returns the model output.
Runner = Callable[[str, dict[str, Any]], Any]

# TypeVar for Pydantic models
T = TypeVar("T", bound=BaseModel)

# Transient failures worth retrying: throttling (429), server errors, and timeouts
# (community models cold-start). 404/401/422 are NOT retried — they're our bugs.
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, ReplicateError):
        return getattr(exc, "status", None) in _RETRY_STATUS
    return False


class _HasRead(Protocol):
    def read(self) -> bytes: ...


def _to_bytes(item: Any) -> bytes:
    """Normalize a Replicate file output (FileOutput / URL str / bytes) to bytes."""
    if isinstance(item, bytes):
        return item
    if hasattr(item, "read"):  # replicate FileOutput is file-like
        return item.read()
    if isinstance(item, str):  # a URL — fetch it
        with urllib.request.urlopen(item) as resp:  # noqa: S310 — trusted replicate URL
            return resp.read()
    raise TypeError(f"cannot read bytes from {type(item)!r}")


class _ReplicateBase:
    """Shares a lazily-built Replicate runner across the model clients."""

    def __init__(self, run: Runner | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._run = run

    def _runner(self) -> Runner:
        if self._run is None:
            import replicate  # imported lazily; needs REPLICATE_API_TOKEN

            self._run = replicate.Client(
                api_token=self.settings.replicate_api_token,
                timeout=self.settings.replicate_timeout_s,
            ).run
        return self._run

    def _execute(self, model: str, inputs: dict[str, Any]) -> Any:
        """Run a model with retry on transient failures (429 / 5xx / timeouts)."""
        retryer = Retrying(
            stop=stop_after_attempt(self.settings.api_max_attempts),
            wait=wait_exponential(
                min=self.settings.api_backoff_min_seconds,
                max=self.settings.api_backoff_max_seconds,
            ),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        return retryer(self._runner(), model, inputs)


class QwenLayersClient(_ReplicateBase):
    """Split a flat ad into RGBA layers (background / product / text / …)."""

    def decompose(self, image_bytes: bytes) -> list[bytes]:
        """Return the layer images (PNG bytes) in the model's output order."""
        logger.debug("replicate_call", model=self.settings.qwen_layers_model)
        out = self._execute(
            self.settings.qwen_layers_model,
            {
                "image": io.BytesIO(image_bytes),
                "num_layers": self.settings.qwen_num_layers,
                "output_format": "png",  # PNG preserves the alpha channel
            },
        )
        items = out if isinstance(out, list) else [out]
        return [_to_bytes(i) for i in items]


class QwenVLClient(_ReplicateBase):
    """Describe imagery with the Qwen3-VL vision-language model."""

    def describe(self, image_bytes: bytes, prompt: str) -> str:
        logger.debug("replicate_call", model=self.settings.qwen_vl_model)
        out = self._execute(
            self.settings.qwen_vl_model,
            {"media": io.BytesIO(image_bytes), "prompt": prompt},
        )
        # Qwen3-VL streams string chunks; join if a list/iterator comes back.
        if isinstance(out, str):
            return out
        return "".join(str(chunk) for chunk in out)


class EmbeddingClient(_ReplicateBase):
    """Embed text with embedding-gemma for retrieval-grounded suggestions."""

    def embed(self, text: str, task: str = "retrieval_document") -> list[float]:
        logger.debug("replicate_call", model=self.settings.embedding_model)
        out = self._execute(
            self.settings.embedding_model,
            {
                "text": text,
                "task": task,
                "embedding_dim": self.settings.embedding_dim,
                "normalize": True,
                "output_format": "array",  # skip base64 → get list[float] directly
            },
        )
        return [float(x) for x in out]


class ReplicateVisionClient(_ReplicateBase):
    """Extract structured JSON from images using google/gemini-3-flash via Replicate."""

    def extract_structured(
        self,
        prompt: str,
        image_bytes: bytes,
        schema: type[T],
    ) -> T:
        """Send image + prompt to Gemini; return the parsed Pydantic model.

        Args:
            prompt: Text instruction for the model.
            image_bytes: Image data (PNG, JPEG, etc.).
            schema: Pydantic model to parse the JSON response into.

        Returns:
            Instance of ``schema`` with the extracted data.
        """
        import base64

        logger.debug("replicate_call", model=self.settings.replicate_gemini_model)
        # Encode image as base64 data URL for Replicate
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64_img}"

        out = self._execute(
            self.settings.replicate_gemini_model,
            {
                "image": data_url,
                "prompt": prompt,
            },
        )
        # Output may be a list of strings (streaming) or a single string.
        # Join if list, then strip markdown fences.
        if isinstance(out, list):
            json_str = "".join(str(item) for item in out)
        else:
            json_str = str(out)

        # Strip markdown fences (```json...```)
        json_str = json_str.strip()
        if json_str.startswith("```json"):
            json_str = json_str[7:]  # len("```json")
        elif json_str.startswith("```"):
            json_str = json_str[3:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()

        logger.debug("replicate_raw_json", raw_json=json_str[:200])
        return schema.model_validate_json(json_str)
