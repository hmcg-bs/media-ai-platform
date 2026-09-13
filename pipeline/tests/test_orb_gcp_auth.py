"""Offline contract tests for the Amp-to-Google WIF lifecycle scripts."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _jwt(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_identity_helper_emits_google_executable_contract(tmp_path: Path) -> None:
    fake_amp = tmp_path / "amp"
    token = _jwt({"exp": 1_900_000_000})
    fake_amp.write_text(
        "#!/usr/bin/env bash\n"
        "[[ \"$*\" == \"orb id-token --audience urn:test:gcp\" ]] || exit 91\n"
        f"printf '%s\\n' '{token}'\n"
    )
    fake_amp.chmod(0o755)
    env = os.environ | {
        "AMP_GCP_AUDIENCE": "urn:test:gcp",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [REPO_ROOT / "scripts/amp-gcp-identity"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(result.stdout) == {
        "version": 1,
        "success": True,
        "token_type": "urn:ietf:params:oauth:token-type:id_token",
        "id_token": token,
        "expiration_time": 1_900_000_000,
    }


def test_resume_atomically_builds_external_account_config(tmp_path: Path) -> None:
    credential_path = tmp_path / "gcp-external-account.json"
    provider = (
        "projects/123456789/locations/global/"
        "workloadIdentityPools/amp-orbs/providers/amp"
    )
    env = os.environ | {
        "AMP_GCP_AUDIENCE": "urn:test:gcp",
        "GCP_WIF_PROVIDER": provider,
        "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
        "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES": "1",
        "GCP_WIF_SERVICE_ACCOUNT": "ocr@test-project.iam.gserviceaccount.com",
    }

    for _ in range(2):
        subprocess.run(
            [REPO_ROOT / ".agents/resume"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    config = json.loads(credential_path.read_text())
    assert config["type"] == "external_account"
    assert config["audience"] == f"//iam.googleapis.com/{provider}"
    assert config["credential_source"]["executable"] == {
        "command": str(REPO_ROOT / "scripts/amp-gcp-identity"),
        "timeout_millis": 30000,
    }
    assert config["service_account_impersonation_url"].endswith(
        "/ocr@test-project.iam.gserviceaccount.com:generateAccessToken"
    )
    assert credential_path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob("*.tmp.*")) == []

    direct_path = tmp_path / "direct-external-account.json"
    env["GOOGLE_APPLICATION_CREDENTIALS"] = str(direct_path)
    env.pop("GCP_WIF_SERVICE_ACCOUNT")
    subprocess.run(
        [REPO_ROOT / ".agents/resume"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "service_account_impersonation_url" not in json.loads(direct_path.read_text())


def test_resume_does_not_overwrite_credentials_when_wif_is_incomplete(tmp_path: Path) -> None:
    credential_path = tmp_path / "existing.json"
    credential_path.write_text('{"do_not_replace": true}\n')
    env = os.environ.copy()
    env.update(
        {
            "AMP_GCP_AUDIENCE": "urn:test:gcp",
            "GOOGLE_APPLICATION_CREDENTIALS": str(credential_path),
            "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES": "1",
        }
    )
    env.pop("GCP_WIF_PROVIDER", None)

    result = subprocess.run(
        [REPO_ROOT / ".agents/resume"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "GCP Workload Identity is incomplete" in result.stderr
    assert json.loads(credential_path.read_text()) == {"do_not_replace": True}
