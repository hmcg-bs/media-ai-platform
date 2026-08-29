"""Thin, mockable clients for the Replicate-hosted models (ADR-008, extended
by wayfinder map #36 for Generation v1).

Five models power the colour-scheme, imagery, retrieval, cognitive, and
generation components:

- ``QwenLayersClient``    — Qwen-Image-Layered: image → list of RGBA layer PNGs.
- ``QwenVLClient``        — Qwen3-VL: image + prompt → description string.
- ``EmbeddingClient``     — embedding-gemma: text → embedding vector.
- ``ReplicateVisionClient`` — google/gemini-3-flash: image + prompt → structured JSON.
- ``FluxKontextClient``   — Flux Kontext Pro: image + prompt → faithfully-edited image
  (product-render element for Generation v1 — see pipeline/generation/).
- ``BackgroundRemoverClient`` — 851-labs/background-remover: image → RGBA cutout,
  the alpha-matte source for Round 6's inpaint mask (see pipeline/generation/masking.py).
- ``FluxFillClient``      — Flux Fill Pro: image + mask + prompt → masked inpaint,
  replacing FluxKontextClient for the background step so the product's own pixels
  (including label text) are never touched, never regenerated, never garbled.

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
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
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


class FluxKontextClient(_ReplicateBase):
    """Flux Kontext Pro (Generation v1, wayfinder map #36): image-conditioned
    editing that preserves the reference image's subject rather than warping
    it -- the faithful-product-fidelity tool per docs/meta-ad-image-model-stack.md,
    a 4th scoped Replicate exception alongside Qwen/embedding-gemma (ADR-008).
    Real input schema confirmed against Replicate's own API (not assumed):
    ``prompt`` (required), ``input_image``, ``aspect_ratio``, ``output_format``."""

    def edit(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        aspect_ratio: str = "match_input_image",
        output_format: str = "png",
    ) -> bytes:
        logger.debug("replicate_call", model=self.settings.flux_kontext_model)
        out = self._execute(
            self.settings.flux_kontext_model,
            {
                "prompt": prompt,
                "input_image": io.BytesIO(image_bytes),
                "aspect_ratio": aspect_ratio,
                "output_format": output_format,
            },
        )
        # A single-image edit model returns one file, not a list -- but
        # normalize defensively in case a future version starts batching.
        item = out[0] if isinstance(out, list) else out
        return _to_bytes(item)


class BackgroundRemoverClient(_ReplicateBase):
    """851-labs/background-remover: image -> RGBA cutout. Round 6's source of
    the alpha matte that pipeline.generation.masking builds an inpaint mask
    from -- real input schema confirmed live against Replicate's own API:
    ``image``, ``format``, ``reverse``, ``threshold``, ``background_type``."""

    def remove_background(self, image_bytes: bytes) -> bytes:
        logger.debug("replicate_call", model=self.settings.background_remover_model)
        out = self._execute(
            self.settings.background_remover_model,
            {"image": io.BytesIO(image_bytes), "format": "png", "background_type": "rgba"},
        )
        item = out[0] if isinstance(out, list) else out
        return _to_bytes(item)


class FluxFillClient(_ReplicateBase):
    """Flux Fill Pro: masked inpainting -- the mask's black areas are
    preserved pixel-for-pixel, white areas are regenerated (confirmed via
    Replicate's own field description, not assumed). Round 6's fix for Flux
    Kontext's whole-image re-render silently garbling the product's own
    label text: masking the product region out of the edit means those
    pixels are never touched, never regenerated, so they can't be garbled."""

    def inpaint(
        self,
        image_bytes: bytes,
        mask_bytes: bytes,
        prompt: str,
        *,
        output_format: str = "png",
    ) -> bytes:
        logger.debug("replicate_call", model=self.settings.flux_fill_model)
        out = self._execute(
            self.settings.flux_fill_model,
            {
                "prompt": prompt,
                "image": io.BytesIO(image_bytes),
                "mask": io.BytesIO(mask_bytes),
                "output_format": output_format,
            },
        )
        item = out[0] if isinstance(out, list) else out
        return _to_bytes(item)


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
    """Extract structured JSON from images or text using google/gemini-3-flash via Replicate."""

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
        # Encode image as base64 data URL for Replicate.
        # The model's input schema takes `images` (a plural array, max 10) —
        # not a singular `image` field. Sending `image` is silently dropped by
        # the API, so the model receives zero images despite one being sent.
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64_img}"

        out = self._execute(
            self.settings.replicate_gemini_model,
            {
                "images": [data_url],
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

    def extract_structured_text(
        self,
        prompt: str,
        schema: type[T],
    ) -> T:
        """Send text-only prompt to Gemini; return the parsed Pydantic model (no image).

        Args:
            prompt: Text instruction for the model.
            schema: Pydantic model to parse the JSON response into.

        Returns:
            Instance of ``schema`` with the extracted data.
        """
        logger.debug("replicate_call_text_only", model=self.settings.replicate_gemini_model)

        out = self._execute(
            self.settings.replicate_gemini_model,
            {
                "prompt": prompt,
            },
        )
        # Output may be a list of strings (streaming) or a single string.
        if isinstance(out, list):
            json_str = "".join(str(item) for item in out)
        else:
            json_str = str(out)

        # Strip markdown fences
        json_str = json_str.strip()
        if json_str.startswith("```json"):
            json_str = json_str[7:]
        elif json_str.startswith("```"):
            json_str = json_str[3:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()

        logger.debug("replicate_raw_json", raw_json=json_str[:200])
        return schema.model_validate_json(json_str)
