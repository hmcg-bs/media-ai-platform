"""Shared GCP credential resolution for GenAIClient/VisionClient -- both need
the exact same three-way fallback (explicit service-account key -> service-
account impersonation -> plain ADC), so it lives in one place rather than
being duplicated per client.

Background (confirmed live, 2026-08-27): this GCP org enforces
`constraints/iam.disableServiceAccountKeyCreation`, so the key-file path is
unusable here in practice -- kept for portability to a less-restricted
project. Impersonation is this org's actual working path, per Google's own
docs ("the preferred method for local development... avoids the security
risks associated with downloading and storing service account keys"), though
whether it also escapes this org's periodic user-identity reauth prompt is
unconfirmed -- impersonation still mints its token from the user's own
source ADC.
"""

from __future__ import annotations

from pipeline.config import Settings

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def resolve_credentials(settings: Settings) -> object | None:
    """Returns a `google.auth.credentials.Credentials` instance, or None to
    let the caller's client fall back to plain ADC discovery (the previous,
    unchanged default behavior when neither setting below is configured)."""
    if settings.google_application_credentials_path:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            settings.google_application_credentials_path, scopes=[_CLOUD_PLATFORM_SCOPE]
        )

    if settings.impersonate_service_account:
        import google.auth
        from google.auth import impersonated_credentials

        source_credentials, _project = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
        return impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=settings.impersonate_service_account,
            target_scopes=[_CLOUD_PLATFORM_SCOPE],
            lifetime=3600,
        )

    return None
