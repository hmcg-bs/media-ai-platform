"""Stage 2 — OCR + typography hierarchy (Cloud Vision + deterministic math).

We use Cloud Vision purely for *what the text is and where it sits*. The
hierarchy (which block is the headline) is decided by bounding-box area, not by
asking an LLM to guess.

Also derives CopywritingFeatures and Placement — fields that, before this,
only pipeline/datalab/mapping.py's Datalab-based stage populated (Datalab
isn't used in this pipeline: it needs a real file path, incompatible with the
bytes-only design). Confirmed live: 24 of ExtractionResult.flatten_features()'s
~28 fields were structurally always-default as a result. Three tiers here:

1. Pure text statistics (word/char counts, uppercase ratio, punctuation
   counts, emoji count, Flesch-Kincaid grade) — zero semantic gap, same math
   pipeline/datalab/mapping.py already uses, just fed Cloud Vision's text
   instead of Datalab's.
2. Geometry (canvas coverage, alignment, headline zone, block-zone counts) —
   zero semantic gap, pure bounding-box math over Cloud Vision's boxes.
3. Role-dependent fields (cta_present, has_price, has_legal) — Datalab gets
   these from real semantic role classification (its extract step labels
   each element); Cloud Vision has no such classification, so these are
   content-pattern heuristics instead — lower confidence than Datalab's,
   flagged as such below.

Two fields stay honestly at their schema default rather than being faked:
asset_canvas_coverage and copy_vs_image_balance need real image/object-region
detection (distinguishing "this bbox is a product photo" from background),
which text-only OCR structurally cannot provide. Also left at default:
has_badge (a visual icon/seal, not distinctive text), hook_type (redundant
with Stage 5's real hook_framework), and claimed_benefits_count (would need
real semantic understanding to do well).
"""

from __future__ import annotations

import re
import statistics
import time

from pipeline.clients.vision_client import OcrBlock, VisionClient
from pipeline.logger import get_logger
from pipeline.models.output_schema import (
    CopywritingFeatures,
    ElementPlacement,
    PipelineContext,
    Placement,
    TextBlock,
    TypographyHierarchy,
)
from pipeline.stages.base_stage import BaseStage, StageError

logger = get_logger(__name__)

_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_PRICE_RE = re.compile(r"\$\s?\d+(\.\d{2})?|\b\d+(\.\d{2})?\s?USD\b", re.IGNORECASE)
_CTA_PHRASES = (
    "shop now", "buy now", "add to cart", "learn more", "get started",
    "order now", "sign up", "subscribe", "try now", "claim your", "get yours",
    "start now", "join now", "shop today", "buy today", "click here",
)
_LEGAL_RE = re.compile(
    r"terms (and|&) conditions|results may vary|individual results|"
    r"fda has not evaluated|not intended to diagnose|"
    r"consult (a|your) (doctor|physician)|\*see|see store for details|"
    r"while supplies last",
    re.IGNORECASE,
)


def _syllables(word: str) -> int:
    word = word.lower()
    count, prev_vowel = 0, False
    for ch in word:
        is_vowel = ch in "aeiouy"
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e"):
        count = max(1, count - 1)
    return max(1, count)


def _flesch_kincaid_grade(text: str) -> float:
    words = _WORD_RE.findall(text)
    if not words:
        return 0.0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    syllables = sum(_syllables(w) for w in words)
    grade = 0.39 * (len(words) / sentences) + 11.8 * (syllables / len(words)) - 15.59
    return round(grade, 1)


def derive_copywriting_features(
    blocks: list[OcrBlock], headline_text: str
) -> CopywritingFeatures:
    """Text-derived copy features from Cloud Vision's OCR blocks — see module
    docstring for which fields are exact vs. heuristic vs. left at default."""
    if not blocks:
        return CopywritingFeatures()
    all_text = " ".join(b.text for b in blocks)
    words = _WORD_RE.findall(all_text)
    letters = [c for c in all_text if c.isalpha()]
    uppercase = [c for c in letters if c.isupper()]
    lower_text = all_text.lower()
    return CopywritingFeatures(
        copy_block_count=len(blocks),
        total_word_count=len(words),
        total_char_count=len(all_text),
        headline_word_count=len(_WORD_RE.findall(headline_text)),
        headline_char_count=len(headline_text),
        avg_words_per_block=round(len(words) / len(blocks), 2),
        uppercase_ratio=round(len(uppercase) / len(letters), 3) if letters else 0.0,
        exclamation_count=all_text.count("!"),
        question_count=all_text.count("?"),
        emoji_count=len(_EMOJI_RE.findall(all_text)),
        reading_grade_level=_flesch_kincaid_grade(all_text),
        # Heuristic (content pattern match, not Datalab's semantic role classification).
        cta_present=any(phrase in lower_text for phrase in _CTA_PHRASES),
        has_price=bool(_PRICE_RE.search(all_text)),
        has_legal=bool(_LEGAL_RE.search(all_text)),
    )


def _zone_of(y_center: float) -> str:
    """Vertical band of a normalized y-centre: top / middle / bottom (thirds)."""
    if y_center < 1 / 3:
        return "top"
    if y_center < 2 / 3:
        return "middle"
    return "bottom"


def _alignment(x_centers: list[float]) -> str:
    if not x_centers:
        return ""
    if all(abs(x - 0.5) < 0.12 for x in x_centers):
        return "center"
    mean = statistics.fmean(x_centers)
    if mean < 0.4:
        return "left"
    if mean > 0.6:
        return "right"
    return "mixed"


def derive_placement(
    blocks: list[OcrBlock], headline_text: str, canvas_width: int, canvas_height: int
) -> Placement:
    """Block-level placement from Cloud Vision's OCR boxes — text/copy blocks
    only. asset_canvas_coverage/copy_vs_image_balance stay at their schema
    default (0.0): text-only OCR has no notion of image/product regions, so
    any value there would be fabricated rather than derived."""
    if not blocks or canvas_width <= 0 or canvas_height <= 0:
        return Placement()
    canvas_area = float(canvas_width * canvas_height)

    elements: list[ElementPlacement] = []
    copy_cov = 0.0
    x_centers: list[float] = []
    zones = {"top": 0, "middle": 0, "bottom": 0}
    hx = hy = 0.0
    hzone = ""

    for block in blocks:
        if not block.vertices:
            continue
        xs = [v[0] for v in block.vertices]
        ys = [v[1] for v in block.vertices]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        x_center = round((x0 + x1) / 2 / canvas_width, 4)
        y_center = round((y0 + y1) / 2 / canvas_height, 4)
        coverage = round(block.area / canvas_area, 4) if canvas_area else 0.0
        zone = _zone_of(y_center)

        elements.append(
            ElementPlacement(
                kind="copy",
                x_center=x_center,
                y_center=y_center,
                width=round((x1 - x0) / canvas_width, 4),
                height=round((y1 - y0) / canvas_height, 4),
                coverage_percentage=round(coverage * 100, 2),
                zone=zone,
            )
        )
        copy_cov += coverage
        x_centers.append(x_center)
        zones[zone] += 1
        if block.text == headline_text:
            hx, hy, hzone = x_center, y_center, zone

    return Placement(
        elements=elements,
        copy_canvas_coverage=round(copy_cov, 4),
        whitespace_ratio=round(1 - min(copy_cov, 1.0), 4),
        text_alignment=_alignment(x_centers),
        headline_x_center=hx,
        headline_y_center=hy,
        headline_zone=hzone,
        n_blocks_top=zones["top"],
        n_blocks_middle=zones["middle"],
        n_blocks_bottom=zones["bottom"],
    )


def build_hierarchy(blocks: list[OcrBlock], canvas_area: float) -> TypographyHierarchy:
    """Largest-area block = headline; the rest = secondary copy (desc by area)."""
    if not blocks:
        return TypographyHierarchy()

    ranked = sorted(blocks, key=lambda b: b.area, reverse=True)
    headline = ranked[0]
    secondary = ranked[1:]

    def coverage(block: OcrBlock) -> float:
        if canvas_area <= 0:
            return 0.0
        return round(block.area / canvas_area * 100, 2)

    headline_block = TextBlock(
        text=headline.text, canvas_coverage_percentage=coverage(headline)
    )
    secondary_blocks = [
        TextBlock(text=b.text, canvas_coverage_percentage=coverage(b))
        for b in secondary
    ]

    scale_ratio = 0.0
    if secondary and secondary[0].area > 0:
        scale_ratio = round(headline.area / secondary[0].area, 3)

    return TypographyHierarchy(
        primary_headline=headline_block,
        secondary_copy=secondary_blocks,
        headline_to_subtext_scale_ratio=scale_ratio,
    )


class OCRStage(BaseStage):
    name = "stage_02_ocr"

    def __init__(self, vision_client: VisionClient | None = None):
        self.vision_client = vision_client or VisionClient()

    def process(self, context: PipelineContext) -> PipelineContext:
        start = time.monotonic()
        logger.info("stage_started", stage=self.name, ad_id=context.ad_id)
        try:
            assert context.image_bytes is not None, "image_bytes must be loaded"
            blocks = self.vision_client.detect_text(context.image_bytes)

            meta = context.result.technical_metadata
            canvas_area = float(meta.width * meta.height)
            hierarchy = build_hierarchy(blocks, canvas_area)
            context.result.typography_hierarchy = hierarchy

            headline_text = hierarchy.primary_headline.text
            context.result.copywriting_features = derive_copywriting_features(
                blocks, headline_text
            )
            context.result.placement = derive_placement(
                blocks, headline_text, meta.width, meta.height
            )

            # Hand the raw boxes to Stage 3 so it can mask text before clustering.
            context.ocr_boxes = [b.vertices for b in blocks if b.vertices]

            logger.info(
                "stage_completed",
                stage=self.name,
                ad_id=context.ad_id,
                duration_ms=int((time.monotonic() - start) * 1000),
                text_block_count=len(blocks),
                headline=context.result.typography_hierarchy.primary_headline.text[:50],
            )
            return context
        except Exception as exc:  # noqa: BLE001
            raise StageError(self.name, "OCR / typography extraction failed", exc) from exc
