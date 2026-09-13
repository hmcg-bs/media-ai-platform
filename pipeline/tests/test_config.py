from __future__ import annotations

from pipeline.config import Settings


def test_zenrows_accepts_owner_amp_variable_name(monkeypatch) -> None:
    monkeypatch.delenv("ZENROWS_API_KEY", raising=False)
    monkeypatch.setenv("ZENROWS_API", "owner-token")
    assert Settings(_env_file=None).zenrows_api_key == "owner-token"


def test_canonical_zenrows_variable_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("ZENROWS_API_KEY", "canonical-token")
    monkeypatch.setenv("ZENROWS_API", "owner-token")
    assert Settings(_env_file=None).zenrows_api_key == "canonical-token"
