"""Thin wrapper around the Cloud Vision API for OCR.

Returns text blocks with their bounding-box vertices so the OCR stage can do
its own (deterministic) typography-hierarchy math — we never ask an LLM to
guess headline structure.

Uses ``document_text_detection`` (not ``text_detection``). Confirmed live
(and against Google's own docs) that ``text_detection``'s ``text_annotations``
is word-level only — every "headline" this pipeline picked was really just
the single largest detected *word* ("EVOLUTION", "Melatonin"), never a real
multi-word phrase, and every "secondary copy" block was a fragment ('#',
'oz', ':'). ``document_text_detection``'s hierarchical response (Pages ->
Blocks -> Paragraphs -> Words -> Symbols) lets us group at the paragraph
level instead, which is what a human would actually call a text block.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.config import Settings, get_settings
from pipeline.logger import get_logger

logger = get_logger(__name__)


def _paragraph_text(paragraph: object) -> str:
    """Reconstructs a paragraph's text from its word/symbol hierarchy,
    respecting Google's documented detected-break convention (the
    recommended way to reinsert spaces/newlines/hyphens that the symbol
    stream itself doesn't carry)."""
    from google.cloud import vision

    break_type = vision.TextAnnotation.DetectedBreak.BreakType
    parts: list[str] = []
    for word in paragraph.words:
        parts.append("".join(s.text for s in word.symbols))
        if not word.symbols:
            continue
        detected = word.symbols[-1].property.detected_break.type_
        if detected in (break_type.SPACE, break_type.SURE_SPACE):
            parts.append(" ")
        elif detected in (break_type.EOL_SURE_SPACE, break_type.LINE_BREAK):
            parts.append("\n")
        elif detected == break_type.HYPHEN:
            parts.append("-")
    return "".join(parts).strip()


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

    def __init__(self, client: object | None = None, settings: Settings | None = None):
        self._client = client
        self._settings = settings or get_settings()

    def _ensure_client(self) -> object:
        if self._client is None:
            from google.cloud import vision  # imported lazily; needs ADC

            from pipeline.clients.gcp_auth import resolve_credentials

            self._client = vision.ImageAnnotatorClient(
                credentials=resolve_credentials(self._settings)
            )
        return self._client

    def _detect_text_once(self, image_bytes: bytes) -> list[OcrBlock]:
        """One attempt at document text detection, no retry — kept separate
        from detect_text() so tests can exercise the reconnect-on-failure
        logic directly without paying real tenacity backoff delays.

        Returns one OcrBlock per detected paragraph (see module docstring
        for why paragraph-level, not word-level)."""
        from google.cloud import vision

        client = self._ensure_client()
        image = vision.Image(content=image_bytes)
        logger.debug("api_call_attempted", api="vision.document_text_detection")
        try:
            response = client.document_text_detection(image=image)
        except Exception:
            # A long-lived process's cached gRPC channel can go stale after a
            # network interruption (confirmed live: a laptop sleeping ~7h mid-run
            # made every subsequent call fail identically with ServiceUnavailable,
            # since every retry kept reusing the same broken channel). Drop the
            # cache so the next tenacity retry rebuilds the client/channel fresh.
            self._client = None
            raise

        if response.error.message:
            raise RuntimeError(f"Vision API error: {response.error.message}")

        blocks: list[OcrBlock] = []
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    text = _paragraph_text(paragraph)
                    if not text:
                        continue
                    verts = [(v.x, v.y) for v in paragraph.bounding_box.vertices]
                    blocks.append(OcrBlock(text=text, vertices=verts))

        logger.debug(
            "api_call_succeeded", api="vision.document_text_detection", blocks=len(blocks)
        )
        return blocks

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
        """Run document text detection with retry; return per-block text + vertices."""
        return self._detect_text_once(image_bytes)
