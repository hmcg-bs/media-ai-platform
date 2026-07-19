"""Thin wrapper around the Cloud Vision API for OCR.

Returns text blocks with their bounding-box vertices so the OCR stage can do
its own (deterministic) typography-hierarchy math — we never ask an LLM to
guess headline structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.config import get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OcrBlock:
    """One detected text block with absolute pixel-bounding-box vertices."""

    text: str
    vertices: list[tuple[int, int]] = field(default_factory=list)

    @property
    def area(self) -> float:
        """Bounding-box area = (maxX-minX) * (maxY-minY)."""
        if not self.vertices:
            return 0.0
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        return float((max(xs) - min(xs)) * (max(ys) - min(ys)))


class VisionClient:
    """Wraps google-cloud-vision. Constructed lazily so tests can inject a mock."""

    def __init__(self, client: object | None = None):
        self._client = client

    def _ensure_client(self) -> object:
        if self._client is None:
            from google.cloud import vision  # imported lazily; needs ADC

            self._client = vision.ImageAnnotatorClient()
        return self._client

    @retry(
        stop=stop_after_attempt(get_settings().api_max_attempts),
        wait=wait_exponential(
            multiplier=1,
            min=get_settings().api_backoff_min_seconds,
            max=get_settings().api_backoff_max_seconds,
        ),
        reraise=True,
    )
    def detect_text(self, image_bytes: bytes) -> list[OcrBlock]:
        """Run document text detection; return per-block text + vertices.

        The first annotation from Vision is the full-image text dump and is
        skipped; the remaining annotations are individual blocks/words.
        """
        from google.cloud import vision

        client = self._ensure_client()
        image = vision.Image(content=image_bytes)
        logger.debug("api_call_attempted", api="vision.text_detection")
        response = client.text_detection(image=image)

        if response.error.message:
            raise RuntimeError(f"Vision API error: {response.error.message}")

        annotations = list(response.text_annotations)
        blocks: list[OcrBlock] = []
        for ann in annotations[1:]:  # skip [0] = whole-image aggregate
            verts = [(v.x, v.y) for v in ann.bounding_poly.vertices]
            blocks.append(OcrBlock(text=ann.description, vertices=verts))

        logger.debug("api_call_succeeded", api="vision.text_detection", blocks=len(blocks))
        return blocks
