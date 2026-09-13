"""Report which GCP credential is actually in use and whether it can refresh.

Run this before leaving the crew unattended, and any time a run dies with
`RefreshError: Reauthentication is needed`.

    uv run python scripts/check_auth_health.py

Amp orbs should use keyless Workload Identity Federation (WIF), documented in
``docs/gcp-orb-auth.md``. Local workstations may still use user ADC. This script
distinguishes those modes without printing credential or token contents.
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
    project = settings.gcp_project_id
    print(f"Project: {project or '(missing)'}   Location: {settings.vertex_location}\n")
    if not project:
        _bad("GCP_PROJECT_ID is missing")
        print("    Configure it in the Amp project environment; see docs/gcp-orb-auth.md.\n")

    # ── Which credential will gcp_auth.resolve_credentials() actually pick? ──
    print("Credential resolution (pipeline/clients/gcp_auth.py order):")
    key_path = settings.google_application_credentials_path
    impersonate = settings.impersonate_service_account
    adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

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
    elif adc_path:
        adc_file = Path(adc_path)
        if not adc_file.is_file():
            _bad(f"GOOGLE_APPLICATION_CREDENTIALS file missing  →  {adc_path}")
            print("\n    Fix the Amp WIF environment; see docs/gcp-orb-auth.md.\n")
            return 1
        try:
            credential_type = json.loads(adc_file.read_text()).get("type")
        except (OSError, json.JSONDecodeError) as exc:
            _bad(f"GOOGLE_APPLICATION_CREDENTIALS is unreadable: {type(exc).__name__}")
            return 1
        if credential_type == "external_account":
            _ok(f"keyless external-account WIF  →  {adc_path}")
            mode = "wif"
        elif credential_type == "service_account":
            _warn(f"service-account key via ADC  →  {adc_path}")
            mode = "sa_key"
        else:
            _warn(f"ADC file type {credential_type!r}  →  {adc_path}")
            mode = "user_adc"
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
            if mode == "wif":
                print(
                    "\n    Inspect the WIF provider and Amp environment; "
                    "see docs/gcp-orb-auth.md.\n"
                )
            else:
                print("\n    Local only: gcloud auth application-default login")
                print("    Amp orb: configure WIF using docs/gcp-orb-auth.md.\n")
        else:
            _bad(f"{name}: {str(e)[:180]}")
            print(f"\n    {DIM}If this is an SSLError it is usually a transient network{RESET}")
            print(f"    {DIM}flake — re-run before concluding auth is broken.{RESET}\n")
        return 1

    # ── The verdict that actually matters ──
    print("\nHow long will this survive unattended?\n")
    if mode == "wif":
        print(f"  {GREEN}AUTOMATICALLY REFRESHABLE.{RESET} Amp mints short-lived OIDC tokens")
        print("  on demand; no user session or long-lived key is involved.\n")
        verdict = 0
    elif mode == "sa_key":
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
        print(f"  In an Amp orb, fix this with {GREEN}docs/gcp-orb-auth.md{RESET}.")
        verdict = 2

    # ── Non-GCP credentials the crew also needs ──
    print("\nOther credentials (these do NOT expire on a session timer):")
    _ok("REPLICATE_API_TOKEN set") if settings.replicate_api_token else _bad(
        "REPLICATE_API_TOKEN missing — Flux Fill / background-remover will fail"
    )
    if settings.apify_api_token:
        _ok("APIFY_API_TOKEN set")
    print()
    return verdict if project else 1


if __name__ == "__main__":
    raise SystemExit(main())
