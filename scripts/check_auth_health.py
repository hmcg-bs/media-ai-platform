"""Report which GCP credential is actually in use and how long it will last.

Run this before leaving the crew unattended, and any time a run dies with
`RefreshError: Reauthentication is needed`.

    uv run python scripts/check_auth_health.py

Background: a *user* credential (`gcloud auth application-default login`) on a
Google Workspace domain is governed by "Google Cloud session control", capped
at a maximum of 24 hours — so it cannot survive a week. A *service-account
key* is not tied to a user session and does not expire. This script tells you
which one you have.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def _bad(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def main() -> int:
    from pipeline.config import get_settings

    settings = get_settings()

    print("\n=== GCP auth health ===\n")
    print(f"Project: {settings.gcp_project_id}   Location: {settings.vertex_location}\n")

    # ── Which credential will gcp_auth.resolve_credentials() actually pick? ──
    print("Credential resolution (pipeline/clients/gcp_auth.py order):")
    key_path = settings.google_application_credentials_path
    impersonate = settings.impersonate_service_account

    mode: str
    if key_path:
        exists = Path(key_path).exists()
        if exists:
            _ok(f"service-account KEY file  →  {key_path}")
            mode = "sa_key"
        else:
            _bad(f"key path set but FILE MISSING  →  {key_path}")
            print("\n    Fix: re-run scripts/setup_service_account_auth.sh\n")
            return 1
        if impersonate:
            _warn(f"IMPERSONATE_SERVICE_ACCOUNT is also set ({impersonate}) but is IGNORED")
    elif impersonate:
        _warn(f"service-account IMPERSONATION  →  {impersonate}")
        print(f"    {DIM}Impersonation still mints tokens from your user ADC, so it does{RESET}")
        print(f"    {DIM}NOT escape the Workspace session cap. Confirmed live, Round 4.{RESET}")
        mode = "impersonation"
    else:
        _warn("plain user ADC (no key, no impersonation)")
        mode = "user_adc"

    # ── Can we actually mint a token right now? ──
    print("\nLive token check:")
    try:
        import google.auth
        import google.auth.transport.requests as tr

        from pipeline.clients.gcp_auth import resolve_credentials

        creds = resolve_credentials(settings)
        if creds is None:
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        creds.refresh(tr.Request())
        _ok(f"token minted OK (this access token expires {creds.expiry} UTC)")
        print(f"    {DIM}Access tokens always last ~1h and auto-refresh; that is normal{RESET}")
        print(f"    {DIM}and not the thing that breaks long runs.{RESET}")
    except Exception as e:  # noqa: BLE001 — diagnostic script, report anything
        name = type(e).__name__
        if "Reauth" in str(e) or "RefreshError" in name:
            _bad(f"{name}: reauthentication required NOW")
            print("\n    Fix immediately:  gcloud auth application-default login")
            print("    Fix permanently:  bash scripts/setup_service_account_auth.sh\n")
        else:
            _bad(f"{name}: {str(e)[:180]}")
            print(f"\n    {DIM}If this is an SSLError it is usually a transient network{RESET}")
            print(f"    {DIM}flake — re-run before concluding auth is broken.{RESET}\n")
        return 1

    # ── The verdict that actually matters ──
    print("\nHow long will this survive unattended?\n")
    if mode == "sa_key":
        print(f"  {GREEN}INDEFINITELY.{RESET} Service-account keys are not tied to a user")
        print("  session and do not expire. Safe for a week or longer.\n")
        print(f"  {DIM}Remember to delete the key when the unattended period ends —{RESET}")
        print(f"  {DIM}see the teardown section of setup_service_account_auth.sh.{RESET}")
        verdict = 0
    else:
        print(f"  {RED}AT MOST 24 HOURS.{RESET} You are on a user credential, which a")
        print("  Google Workspace 'Google Cloud session control' policy caps at 24h")
        print("  maximum (there is no never-expires option). A week is not possible")
        print("  this way, regardless of how recently you logged in.\n")
        print(f"  To fix: {GREEN}bash scripts/setup_service_account_auth.sh{RESET}")
        verdict = 2

    # ── Non-GCP credentials the crew also needs ──
    print("\nOther credentials (these do NOT expire on a session timer):")
    _ok("REPLICATE_API_TOKEN set") if settings.replicate_api_token else _bad(
        "REPLICATE_API_TOKEN missing — Flux Fill / background-remover will fail"
    )
    if settings.apify_api_token:
        _warn("APIFY_API_TOKEN set, but the account was quota-blocked at last check")
    print()
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
