from __future__ import annotations

import json

import pytest

from pipeline.artifacts import (
    OutputLockedError,
    atomic_write_json,
    atomic_write_text,
    exclusive_output,
)


def test_atomic_write_json_replaces_complete_document(tmp_path):
    output = tmp_path / "report.json"
    output.write_text('{"old": true}')
    atomic_write_json(output, {"rows": [1, 2, 3]})
    assert json.loads(output.read_text()) == {"rows": [1, 2, 3]}


def test_atomic_write_text_replaces_complete_document(tmp_path):
    output = tmp_path / "diagnostics.csv"
    atomic_write_text(output, "url,status\nhttps://example.com,ok\n")
    assert output.read_text() == "url,status\nhttps://example.com,ok\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_second_writer_to_same_output_fails_fast(tmp_path):
    output = tmp_path / "report.json"
    with exclusive_output(output):
        with pytest.raises(OutputLockedError, match="another job"):
            with exclusive_output(output):
                pass


def test_distinct_outputs_can_be_owned_concurrently(tmp_path):
    with exclusive_output(tmp_path / "a.json"):
        with exclusive_output(tmp_path / "b.json"):
            pass
