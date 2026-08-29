"""Tests for pipeline/clients/vision_client.py — retry/reconnect behavior and
paragraph-level text grouping, fully offline (fake client, no real Vision API
calls)."""

from __future__ import annotations

from unittest.mock import patch

from google.cloud import vision

from pipeline.clients.vision_client import VisionClient, _paragraph_text
from pipeline.config import Settings

_BREAK = vision.TextAnnotation.DetectedBreak.BreakType


def _symbol(text: str, break_type=_BREAK.SPACE):
    prop = type("Prop", (), {"detected_break": type("DB", (), {"type_": break_type})()})()
    return type("Symbol", (), {"text": text, "property": prop})()


def _word(*chars: str, break_type=_BREAK.SPACE):
    """A word built from individual symbol characters, with the given break
    type on its final symbol (mirrors how Google attaches breaks per-symbol,
    not per-word)."""
    symbols = [_symbol(c) for c in chars[:-1]] + [_symbol(chars[-1], break_type)]
    return type("Word", (), {"symbols": symbols})()


def _vertex(x: int, y: int):
    return type("Vertex", (), {"x": x, "y": y})()


def _paragraph(words, vertices=None):
    verts = [_vertex(x, y) for x, y in (vertices or [])]
    bbox = type("BB", (), {"vertices": verts})()
    return type("Paragraph", (), {"words": words, "bounding_box": bbox})()


def _block(paragraphs):
    return type("Block", (), {"paragraphs": paragraphs})()


def _page(blocks):
    return type("Page", (), {"blocks": blocks})()


class _FakeResponse:
    def __init__(self, pages=None, error_message=""):
        self.full_text_annotation = type("FTA", (), {"pages": pages or []})()
        self.error = type("Err", (), {"message": error_message})()


def _word_from_string(text: str, break_type=_BREAK.SPACE):
    return _word(*list(text), break_type=break_type)


class TestParagraphText:
    """Regression: Cloud Vision's text_detection only returns word-level
    annotations (confirmed against Google's own docs) — every "headline"
    this pipeline picked was really just the single largest detected word.
    document_text_detection's Paragraph level must be reconstructed
    correctly from its word/symbol hierarchy."""

    def test_joins_words_with_spaces(self):
        para = _paragraph([
            _word_from_string("SHOP", break_type=_BREAK.SPACE),
            _word_from_string("NOW", break_type=_BREAK.EOL_SURE_SPACE),
        ])
        assert _paragraph_text(para) == "SHOP NOW"

    def test_line_break_becomes_newline(self):
        para = _paragraph([
            _word_from_string("Line1", break_type=_BREAK.LINE_BREAK),
            _word_from_string("Line2", break_type=_BREAK.EOL_SURE_SPACE),
        ])
        assert _paragraph_text(para) == "Line1\nLine2"

    def test_hyphen_break(self):
        para = _paragraph([
            _word_from_string("multi", break_type=_BREAK.HYPHEN),
            _word_from_string("word", break_type=_BREAK.EOL_SURE_SPACE),
        ])
        assert _paragraph_text(para) == "multi-word"

    def test_empty_paragraph_returns_empty_string(self):
        assert _paragraph_text(_paragraph([])) == ""

    def test_strips_trailing_whitespace(self):
        para = _paragraph([_word_from_string("SOLO", break_type=_BREAK.EOL_SURE_SPACE)])
        assert _paragraph_text(para) == "SOLO"


class _FlakyThenHealthyClient:
    """Simulates a stale gRPC channel: fails every call until replaced."""

    def __init__(self):
        self.call_count = 0

    def document_text_detection(self, image):
        self.call_count += 1
        raise ConnectionError("channel is stale")


class _HealthyClient:
    def __init__(self):
        self.call_count = 0

    def document_text_detection(self, image):
        self.call_count += 1
        para = _paragraph(
            [_word_from_string("HELLO", break_type=_BREAK.EOL_SURE_SPACE)],
            vertices=[(0, 0), (10, 0), (10, 5), (0, 5)],
        )
        return _FakeResponse(pages=[_page([_block([para])])])


class TestServiceAccountCredentials:
    """Regression: user ADC (`gcloud auth application-default login`) is
    subject to Google's periodic interactive-reauth policy -- a service
    account key is the permanent fix, since service accounts aren't gated by
    that policy. When configured, _ensure_client must build explicit
    credentials from the key file rather than falling back to ADC."""

    def test_uses_explicit_credentials_when_key_path_configured(self) -> None:
        settings = Settings(google_application_credentials_path="/tmp/fake-key.json")
        client = VisionClient(settings=settings)

        with (
            patch(
                "google.oauth2.service_account.Credentials.from_service_account_file"
            ) as from_file,
            patch("google.cloud.vision.ImageAnnotatorClient") as annotator_cls,
        ):
            from_file.return_value = "fake-credentials-object"
            client._ensure_client()

            from_file.assert_called_once_with(
                "/tmp/fake-key.json",
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            annotator_cls.assert_called_once_with(credentials="fake-credentials-object")

    def test_falls_back_to_adc_when_no_key_path_configured(self) -> None:
        settings = Settings(google_application_credentials_path="", impersonate_service_account="")
        client = VisionClient(settings=settings)

        with patch("google.cloud.vision.ImageAnnotatorClient") as annotator_cls:
            client._ensure_client()
            annotator_cls.assert_called_once_with(credentials=None)


class TestVisionClientReconnectOnFailure:
    """Regression: a long-lived process's cached gRPC channel went stale after
    a multi-hour machine sleep mid-run — every retry kept reusing the same
    broken channel and failed identically (confirmed live: 127/127 OCR calls
    failed with ServiceUnavailable after a ~7h gap). detect_text must drop the
    cached client on any failure so the next attempt rebuilds it fresh."""

    def test_client_is_dropped_after_a_failed_call(self) -> None:
        # Exercises _detect_text_once directly (no @retry) so this stays a
        # fast, deterministic unit test instead of paying real tenacity
        # backoff delays across 3 attempts (~6s+) for what is purely a
        # single-attempt behavior — that real-retry path is covered
        # separately, via detect_text() in the next test.
        stale = _FlakyThenHealthyClient()
        client = VisionClient(client=stale)
        assert client._client is stale

        try:
            client._detect_text_once(b"fake-bytes")
        except Exception:
            pass

        assert client._client is None

    def test_detect_text_rebuilds_and_succeeds_on_retry(self) -> None:
        """End-to-end through the real @retry-decorated detect_text(): the
        first attempt fails against a stale client, drops the cache, and the
        retry rebuilds against a fresh (healthy) one — proving the reset
        actually unblocks recovery, not just that the attribute gets cleared."""
        stale = _FlakyThenHealthyClient()
        healthy = _HealthyClient()
        client = VisionClient(client=stale)

        # _ensure_client only reconstructs a real client when self._client is
        # None; inject the "fresh channel" a real reconnect would produce.
        original_ensure = client._ensure_client

        def _ensure_client_swap():
            if client._client is None:
                client._client = healthy
            return original_ensure()

        client._ensure_client = _ensure_client_swap

        blocks = client.detect_text(b"fake-bytes")
        assert [b.text for b in blocks] == ["HELLO"]
        assert stale.call_count == 1
        assert healthy.call_count == 1


class TestDetectTextParagraphGrouping:
    def test_returns_one_block_per_paragraph_not_per_word(self) -> None:
        para1 = _paragraph(
            [
                _word_from_string("SHOP", break_type=_BREAK.SPACE),
                _word_from_string("NOW", break_type=_BREAK.EOL_SURE_SPACE),
            ],
            vertices=[(0, 0), (100, 0), (100, 30), (0, 30)],
        )
        para2 = _paragraph(
            [_word_from_string("Details", break_type=_BREAK.EOL_SURE_SPACE)],
            vertices=[(0, 40), (60, 40), (60, 55), (0, 55)],
        )
        response = _FakeResponse(pages=[_page([_block([para1, para2])])])
        client = VisionClient(client=type("C", (), {
            "document_text_detection": lambda self, image: response,
        })())

        blocks = client.detect_text(b"fake-bytes")
        assert [b.text for b in blocks] == ["SHOP NOW", "Details"]

    def test_empty_paragraphs_are_skipped(self) -> None:
        empty_para = _paragraph([])
        real_para = _paragraph([_word_from_string("REAL", break_type=_BREAK.EOL_SURE_SPACE)])
        response = _FakeResponse(pages=[_page([_block([empty_para, real_para])])])
        client = VisionClient(client=type("C", (), {
            "document_text_detection": lambda self, image: response,
        })())

        blocks = client.detect_text(b"fake-bytes")
        assert [b.text for b in blocks] == ["REAL"]

    def test_no_pages_returns_empty_list(self) -> None:
        response = _FakeResponse(pages=[])
        client = VisionClient(client=type("C", (), {
            "document_text_detection": lambda self, image: response,
        })())
        assert client.detect_text(b"fake-bytes") == []
