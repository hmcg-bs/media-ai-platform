"""Parse a Datalab block's inline HTML into structured text runs + image metadata.

Datalab (Style Preserver) returns each block's content as an HTML string carrying the
real per-element information: visible copy in ``<p>`` tags with inline ``color`` and
``text-align``, bold via ``<b>``, line breaks via ``<br/>``, and the AI-generated image
description as a hidden ``<p style="display:none">`` inside a ``div.img-description``.

This module turns that string into typed pieces so the models never store opaque HTML:

    parse_block_html(html) -> ParsedBlockContent(runs, image_alt, image_description)

Stdlib-only (``html.parser``) — no third-party HTML dependency. Runs are paragraph-level:
a paragraph containing any ``<b>``/``<strong>`` is marked ``bold`` (sub-paragraph span
splitting is intentionally out of scope). Font size is NOT in the HTML and is filled in
later from bbox geometry, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

# Tags that begin a visible text paragraph (a run boundary).
_PARAGRAPH_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6"})
_BOLD_TAGS = frozenset({"b", "strong"})
_ITALIC_TAGS = frozenset({"i", "em"})


@dataclass
class RawRun:
    """A parsed paragraph before it becomes a Pydantic ``ParsedTextRun``."""

    text: str
    tag: str = "p"
    color: str | None = None
    text_align: str | None = None
    bold: bool = False
    italic: bool = False


@dataclass
class ParsedBlockContent:
    """Everything recovered from one block's HTML."""

    runs: list[RawRun] = field(default_factory=list)
    image_alt: str | None = None
    image_description: str | None = None


def parse_style(style: str | None) -> dict[str, str]:
    """Parse an inline ``style="a: b; c: d"`` attribute into a lowercased dict."""
    result: dict[str, str] = {}
    if not style:
        return result
    for decl in style.split(";"):
        if ":" in decl:
            key, _, value = decl.partition(":")
            result[key.strip().lower()] = value.strip()
    return result


class _BlockHTMLParser(HTMLParser):
    """Walks a block's HTML, emitting one paragraph run per visible ``<p>``/heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.content = ParsedBlockContent()
        # Current paragraph being accumulated (None when not inside one).
        self._tag: str | None = None
        self._buf: list[str] = []
        self._color: str | None = None
        self._align: str | None = None
        self._hidden: bool = False
        self._bold: bool = False
        self._italic: bool = False

    # ── tag handling ──────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt and not self.content.image_alt:
                self.content.image_alt = alt
            return
        if tag == "br":
            if self._tag is not None:
                self._buf.append("\n")
            return
        if tag in _PARAGRAPH_TAGS:
            self._open_paragraph(tag, parse_style(a.get("style")))
            return
        if tag in _BOLD_TAGS and self._tag is not None:
            self._bold = True
        elif tag in _ITALIC_TAGS and self._tag is not None:
            self._italic = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags like <br/> and <img/>.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _PARAGRAPH_TAGS and self._tag == tag:
            self._close_paragraph()

    def handle_data(self, data: str) -> None:
        if self._tag is not None:
            self._buf.append(data)

    # ── paragraph lifecycle ───────────────────────────────────────────

    def _open_paragraph(self, tag: str, style: dict[str, str]) -> None:
        self._tag = tag
        self._buf = []
        self._color = style.get("color")
        self._align = style.get("text-align")
        self._hidden = style.get("display") == "none"
        self._bold = False
        self._italic = False

    def _close_paragraph(self) -> None:
        text = "".join(self._buf)
        # Collapse spaces/tabs but preserve intentional newlines from <br/>.
        text = "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()
        if text:
            if self._hidden:
                # Hidden text = the AI image description, not ad copy.
                self.content.image_description = (
                    text if not self.content.image_description
                    else f"{self.content.image_description} {text}"
                )
            else:
                self.content.runs.append(
                    RawRun(
                        text=text,
                        tag=self._tag or "p",
                        color=self._color,
                        text_align=self._align,
                        bold=self._bold,
                        italic=self._italic,
                    )
                )
        self._tag = None
        self._buf = []


def parse_block_html(html: str) -> ParsedBlockContent:
    """Parse one block's HTML into visible runs + image alt/description."""
    parser = _BlockHTMLParser()
    parser.feed(html or "")
    parser.close()
    return parser.content
