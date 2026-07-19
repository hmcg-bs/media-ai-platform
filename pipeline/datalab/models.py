"""Pydantic models for the raw Datalab convert / Style-Preserver output JSON.

These mirror the block-tree document Datalab returns (``output_format="json"``) so it
can be validated and stored as-is: ``DatalabDocument.model_validate(json_data)``. Feature
extraction and embedding happen in a later step and are NOT this file's concern.

Shape (top → down):
    DatalabDocument { children: [DatalabBlock], metadata }
      └ DatalabBlock (recursive)  — a Page block whose children are Text/Figure/Picture…
          fields: id, block_type, html, bbox, polygon, images (base64), …

Coordinate note: bboxes are in Datalab's rendered *canvas* space (the Page block bbox),
which may be larger than the original image. Scale before mapping to image pixels.
"""

from __future__ import annotations

import re
from html import unescape

from pydantic import BaseModel, Field, model_validator

from pipeline.datalab.html_parse import parse_block_html

# Block types whose bbox is a whole image/table *region*, not a per-line text box.
IMAGE_BLOCK_TYPES = frozenset(
    {"Figure", "Picture", "Diagram", "Image", "Table", "TableGroup"}
)

_TAG_RE = re.compile(r"<[^>]+>")


class TextStyle(BaseModel):
    """Styling for a paragraph.

    text_align/bold/italic come straight from the HTML and are reliable. Two values that
    are NOT reliably in the HTML get special handling:

    - ``color_reported`` — the colour Style Preserver wrote into the HTML. It is a
      generative VLM guess: non-deterministic between runs and NOT the true pixel colour.
      Treat it as an untrusted hint only.
    - ``color_measured`` — the authoritative colour, sampled from the actual image pixels
      at the block's bbox (filled by ``pipeline.datalab.color.measure_text_colors``, which
      needs the original image). None until that step runs, or when not attributable.

    Font size is likewise absent from the HTML; ``font_size_*`` is a bbox proxy filled by
    ``DatalabDocument`` and stays None when it can't be attributed (multiple runs sharing
    one block bbox).
    """

    color_reported: str | None = None   # hex from HTML `color:` — untrusted VLM hint
    color_measured: str | None = None   # hex sampled from real pixels — authoritative
    text_align: str | None = None       # left | center | right
    bold: bool = False                  # paragraph contains <b>/<strong>
    italic: bool = False                # paragraph contains <i>/<em>
    font_size_canvas_px: float | None = None
    font_size_pct_canvas: float | None = None


class ParsedTextRun(BaseModel):
    """One visible paragraph of copy parsed out of a block's HTML."""

    text: str = ""                      # visible text; <br/> rendered as "\n"
    tag: str = "p"                      # source tag (p / h1 / …)
    block_id: str = ""                  # id of the DatalabBlock this run came from
    style: TextStyle = Field(default_factory=TextStyle)


class DatalabBlock(BaseModel):
    """One node in the Datalab block tree (a page, or a text/figure/picture within it)."""

    id: str
    block_type: str
    html: str = ""
    page: int = 0
    # bbox = [x0, y0, x1, y1]; polygon = [[x, y], …] — both in canvas space.
    bbox: list[float] = Field(default_factory=list)
    polygon: list[list[float]] = Field(default_factory=list)
    section_hierarchy: dict[str, str] = Field(default_factory=dict)
    # Extracted sub-images keyed by filename → base64 JPEG (present on Figure/Picture).
    images: dict[str, str] = Field(default_factory=dict)
    markdown: str | None = None
    inference_failed: bool = False
    metadata: dict | None = None
    children: list[DatalabBlock] = Field(default_factory=list)

    # ── parsed from `html` (populated on validation) ──────────────────
    # Visible copy paragraphs. Font size is filled later by DatalabDocument.
    text_runs: list[ParsedTextRun] = Field(default_factory=list)
    image_alt: str | None = None            # <img alt="…"> caption
    image_description: str | None = None    # hidden display:none AI description

    @model_validator(mode="after")
    def _parse_html(self) -> DatalabBlock:
        """Parse this block's HTML into text runs + image metadata (size added later).

        Only leaf blocks are parsed: a container block (e.g. Page) carries the concatenated
        HTML of all its children, so parsing it too would duplicate every run.
        """
        if self.children:
            return self
        parsed = parse_block_html(self.html)
        self.text_runs = [
            ParsedTextRun(
                text=r.text,
                tag=r.tag,
                block_id=self.id,
                style=TextStyle(
                    color_reported=r.color,
                    text_align=r.text_align,
                    bold=r.bold,
                    italic=r.italic,
                ),
            )
            for r in parsed.runs
        ]
        self.image_alt = parsed.image_alt
        self.image_description = parsed.image_description
        return self

    # ── convenience accessors (computed, not stored) ──────────────────

    @property
    def x0(self) -> float:
        return self.bbox[0] if len(self.bbox) == 4 else 0.0

    @property
    def y0(self) -> float:
        return self.bbox[1] if len(self.bbox) == 4 else 0.0

    @property
    def x1(self) -> float:
        return self.bbox[2] if len(self.bbox) == 4 else 0.0

    @property
    def y1(self) -> float:
        return self.bbox[3] if len(self.bbox) == 4 else 0.0

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def is_image_container(self) -> bool:
        """True for Figure/Picture/Table blocks (region bbox, not a text bbox)."""
        return self.block_type in IMAGE_BLOCK_TYPES

    @property
    def plain_text(self) -> str:
        """The block's visible text with HTML tags stripped and entities unescaped."""
        return unescape(_TAG_RE.sub(" ", self.html)).strip()

    def walk(self):
        """Yield this block and every descendant (depth-first)."""
        yield self
        for child in self.children:
            yield from child.walk()


class DatalabPageStats(BaseModel):
    page_id: int = 0
    num_blocks: int = 0


class DatalabDocumentMetadata(BaseModel):
    page_stats: list[DatalabPageStats] = Field(default_factory=list)


class DatalabDocument(BaseModel):
    """Top-level Datalab convert output: a list of page blocks + document metadata."""

    children: list[DatalabBlock] = Field(default_factory=list)
    metadata: DatalabDocumentMetadata = Field(default_factory=DatalabDocumentMetadata)

    @model_validator(mode="after")
    def _fill_font_sizes(self) -> DatalabDocument:
        """Add a bbox-derived font-size proxy to each single-run block.

        Font size isn't in the HTML, so we approximate it from geometry:
        ``bbox_height / line_count``. Only done when a block has exactly one run — a
        block with several runs (e.g. a Figure holding many labels) shares one bbox, so
        per-run size can't be attributed and stays None.
        """
        canvas = self.canvas
        canvas_h = canvas[1] if canvas else 0.0
        for block in self.blocks():
            if len(block.text_runs) != 1 or block.height <= 0:
                continue
            run = block.text_runs[0]
            lines = run.text.count("\n") + 1
            size_px = block.height / lines
            run.style.font_size_canvas_px = round(size_px, 1)
            if canvas_h > 0:
                run.style.font_size_pct_canvas = round(size_px / canvas_h * 100, 2)
        return self

    def all_text_runs(self) -> list[ParsedTextRun]:
        """Every visible copy run across all blocks, in document (reading) order."""
        return [run for block in self.blocks() for run in block.text_runs]

    def blocks(self):
        """Yield every block in the document (all pages, depth-first)."""
        for page in self.children:
            yield from page.walk()

    def text_blocks(self) -> list[DatalabBlock]:
        """Leaf text blocks (excludes Page and image/table containers)."""
        return [
            b for b in self.blocks()
            if b.block_type not in IMAGE_BLOCK_TYPES and b.block_type != "Page"
        ]

    def image_blocks(self) -> list[DatalabBlock]:
        """Figure/Picture/Table region blocks."""
        return [b for b in self.blocks() if b.is_image_container]

    @property
    def canvas(self) -> tuple[float, float] | None:
        """(width, height) of the render canvas (the first Page block's bbox)."""
        for page in self.children:
            if page.block_type == "Page" and len(page.bbox) == 4:
                return (page.width, page.height)
        return None


# Resolve the self-referential ``children`` forward reference.
DatalabBlock.model_rebuild()
