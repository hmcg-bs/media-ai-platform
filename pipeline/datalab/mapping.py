"""Map a DatalabDocument (plain convert) into the Master Schema copy/positioning fields.

Built on **plain convert** (not Style Preserver): plain convert keeps the full text —
including the main headline that Style Preserver drops — at the cost of inline text-align
(which we don't need; text colour comes from pixels).

Description handling: for an image-container block (Figure/Picture) whose description was
NOT captured by the parser (plain convert doesn't hide it with display:none), the block's
first run is that leaked AI description and is excluded from copy. Blocks whose description
*was* captured (Style Preserver's display:none) keep all their runs.

Text-derived copywriting features come from convert; the role-dependent ones (hook_type,
cta_present, has_price/badge/legal) and the marketing hook + value proposition come from the
Datalab *extract* step, passed in when available.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from pipeline.datalab.blocks import bbox_metrics, zone_of
from pipeline.datalab.models import DatalabBlock, DatalabDocument, ParsedTextRun
from pipeline.models.output_schema import (
    CopywritingFeatures,
    ElementPlacement,
    HookFramework,
    MarketingPsychology,
    Placement,
    TextBlock,
    TypographyHierarchy,
)

# Best-effort map from the Datalab extract schema's hook_type to the (legacy) HookFramework
# enum. The raw hook_type is kept verbatim in CopywritingFeatures.hook_type; this enum is a
# lossy view — the two taxonomies don't fully align.
_HOOK_MAP = {
    "problem_solution": HookFramework.PAS,
    "social_proof": HookFramework.SOCIAL_PROOF,
    "authority": HookFramework.TESTIMONIAL,
    "benefit_led": HookFramework.DIRECT_OFFER,
    "urgency": HookFramework.DIRECT_OFFER,
    # fact_education, curiosity → UNKNOWN
}


def _message(extract: dict[str, Any] | None) -> dict[str, Any]:
    return (extract or {}).get("message") or {}

_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿←-⇿⬀-⯿]")
_WORD = re.compile(r"[A-Za-z0-9']+")


# ── copy-run collection (drops leaked image descriptions) ──────────────


def copy_runs(doc: DatalabDocument) -> list[tuple[ParsedTextRun, DatalabBlock]]:
    """(run, block) pairs of real copy, excluding leaked image descriptions."""
    pairs: list[tuple[ParsedTextRun, DatalabBlock]] = []
    for block in doc.blocks():
        runs = block.text_runs
        if not runs:
            continue
        if block.is_image_container and not block.image_description:
            runs = runs[1:]  # first run is the (non-hidden) AI description → drop
        pairs.extend((r, block) for r in runs)
    return pairs


def _run_size(run: ParsedTextRun, block: DatalabBlock) -> float:
    """Per-line height (canvas px) of a run — a size proxy for ranking."""
    total_lines = sum(r.text.count("\n") + 1 for r in block.text_runs) or 1
    return block.height / total_lines if block.height else 0.0


# ── typography hierarchy ───────────────────────────────────────────────


def to_typography_hierarchy(doc: DatalabDocument) -> TypographyHierarchy:
    """Rank copy runs by size: largest = headline, rest = secondary (desc by size)."""
    pairs = copy_runs(doc)
    if not pairs:
        return TypographyHierarchy()
    canvas = doc.canvas
    canvas_area = (canvas[0] * canvas[1]) if canvas else 0.0

    scored = []
    for run, block in pairs:
        size = _run_size(run, block)
        lines = run.text.count("\n") + 1
        area = block.width * size * lines
        scored.append((run.text, size, area))
    scored.sort(key=lambda s: s[1], reverse=True)

    def _block(text: str, area: float) -> TextBlock:
        cov = round(area / canvas_area * 100, 2) if canvas_area else 0.0
        return TextBlock(text=text, canvas_coverage_percentage=cov)

    headline = scored[0]
    secondary = scored[1:]
    scale = (
        round(headline[1] / secondary[0][1], 3)
        if secondary and secondary[0][1]
        else 0.0
    )
    return TypographyHierarchy(
        primary_headline=_block(headline[0], headline[2]),
        secondary_copy=[_block(t, a) for t, _, a in secondary],
        headline_to_subtext_scale_ratio=scale,
    )


# ── copywriting features (text-derived only) ───────────────────────────


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
    words = _WORD.findall(text)
    if not words:
        return 0.0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    syllables = sum(_syllables(w) for w in words)
    grade = 0.39 * (len(words) / sentences) + 11.8 * (syllables / len(words)) - 15.59
    return round(grade, 1)


def to_copywriting_features(
    doc: DatalabDocument, extract: dict[str, Any] | None = None
) -> CopywritingFeatures:
    """Text-derived copy features from convert; role-derived fields from extract (if given)."""
    pairs = copy_runs(doc)
    if not pairs:
        return CopywritingFeatures()
    all_text = " ".join(r.text.replace("\n", " ") for r, _ in pairs)
    words = _WORD.findall(all_text)
    letters = [c for c in all_text if c.isalpha()]
    uppercase = [c for c in letters if c.isupper()]
    headline = to_typography_hierarchy(doc).primary_headline.text
    cw = CopywritingFeatures(
        copy_block_count=len(pairs),
        total_word_count=len(words),
        total_char_count=len(all_text),
        headline_word_count=len(_WORD.findall(headline)),
        headline_char_count=len(headline),
        avg_words_per_block=round(len(words) / len(pairs), 2),
        uppercase_ratio=round(len(uppercase) / len(letters), 3) if letters else 0.0,
        exclamation_count=all_text.count("!"),
        question_count=all_text.count("?"),
        emoji_count=len(_EMOJI.findall(all_text)),
        reading_grade_level=_flesch_kincaid_grade(all_text),
    )
    if extract:
        msg = _message(extract)
        cw.hook_type = msg.get("hook_type") or ""
        cw.cta_present = bool(msg.get("cta_present"))
        cw.claimed_benefits_count = len(msg.get("claimed_benefits") or [])
        roles = {
            (el.get("semantic_role") or "").lower()
            for el in (extract.get("text_elements") or [])
        }
        cw.has_price = "price" in roles
        cw.has_badge = "badge" in roles
        cw.has_legal = "legal_disclaimer" in roles
    return cw


def to_marketing_psychology(extract: dict[str, Any] | None) -> MarketingPsychology:
    """Marketing hook + value proposition from the Datalab extract message."""
    msg = _message(extract)
    hook = (msg.get("hook_type") or "").lower()
    value_prop = msg.get("primary_value_proposition") or ""
    return MarketingPsychology(
        hook_framework=_HOOK_MAP.get(hook, HookFramework.UNKNOWN),
        primary_value_proposition=value_prop,
        emoji_count=len(_EMOJI.findall(value_prop)),
    )


# ── placement (block-level: copy blocks + asset blocks) ────────────────


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


def to_placement(doc: DatalabDocument) -> Placement:
    """Block-level placement: each non-image text block = copy; each figure/picture = asset.

    Individual labels sharing a diagram's bbox aren't placed separately — their position is
    the diagram's (same single-run limitation as the size/colour proxies).
    """
    canvas = doc.canvas
    if not canvas:
        return Placement()
    cw, ch = canvas
    headline = to_typography_hierarchy(doc).primary_headline.text

    elements: list[ElementPlacement] = []
    copy_cov = asset_cov = 0.0
    x_centers: list[float] = []
    zones = {"top": 0, "middle": 0, "bottom": 0}
    hx = hy = 0.0
    hzone = ""

    for block in doc.blocks():
        if block.block_type == "Page" or len(block.bbox) != 4:
            continue
        is_asset = block.is_image_container
        is_copy = not is_asset and bool(block.text_runs)
        if not (is_asset or is_copy):
            continue
        m = bbox_metrics(block.bbox, cw, ch)
        zone = zone_of(m["y_center"])
        elements.append(
            ElementPlacement(
                role=block.block_type.lower() if is_asset else "",
                kind="asset" if is_asset else "copy",
                x_center=m["x_center"], y_center=m["y_center"],
                width=m["width"], height=m["height"],
                coverage_percentage=round(m["coverage"] * 100, 2), zone=zone,
            )
        )
        if is_copy:
            copy_cov += m["coverage"]
            x_centers.append(m["x_center"])
            zones[zone] += 1
            if any(r.text == headline for r in block.text_runs):
                hx, hy, hzone = m["x_center"], m["y_center"], zone
        else:
            asset_cov += m["coverage"]

    total = copy_cov + asset_cov
    return Placement(
        elements=elements,
        copy_canvas_coverage=round(copy_cov, 4),
        asset_canvas_coverage=round(asset_cov, 4),
        whitespace_ratio=round(1 - min(total, 1.0), 4),
        copy_vs_image_balance=round(copy_cov / total, 3) if total else 0.0,
        text_alignment=_alignment(x_centers),
        headline_x_center=hx, headline_y_center=hy, headline_zone=hzone,
        n_blocks_top=zones["top"], n_blocks_middle=zones["middle"],
        n_blocks_bottom=zones["bottom"],
    )


# ── ocr_boxes for the colour stage (image-space vertices) ──────────────


def to_ocr_boxes(
    doc: DatalabDocument, image_w: int, image_h: int
) -> list[list[tuple[int, int]]]:
    """Text-block bboxes scaled from canvas space to original-image pixels.

    Fed to stage_03 (colour) so it can mask text before K-Means, exactly like the Cloud
    Vision OCR stage did.
    """
    canvas = doc.canvas
    if not canvas or image_w <= 0 or image_h <= 0:
        return []
    sx, sy = image_w / canvas[0], image_h / canvas[1]
    boxes: list[list[tuple[int, int]]] = []
    for block in doc.blocks():
        if block.block_type == "Page" or block.is_image_container:
            continue
        if not block.text_runs or len(block.bbox) != 4:
            continue
        x0, y0, x1, y1 = block.bbox
        px0, py0 = int(x0 * sx), int(y0 * sy)
        px1, py1 = int(x1 * sx), int(y1 * sy)
        boxes.append([(px0, py0), (px1, py0), (px1, py1), (px0, py1)])
    return boxes
