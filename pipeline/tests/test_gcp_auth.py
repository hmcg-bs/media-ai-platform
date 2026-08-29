"""Tests for pipeline/clients/gcp_auth.py -- the shared three-way credential
fallback (key file -> impersonation -> plain ADC) used by both GenAIClient
and VisionClient."""

from __future__ import annotations

from unittest.mock import patch

from pipeline.clients.gcp_auth import resolve_credentials
from pipeline.config import Settings


class TestResolveCredentials:
    def test_returns_none_when_nothing_configured(self):
        settings = Settings(google_application_credentials_path="", impersonate_service_account="")
        assert resolve_credentials(settings) is None

    def test_key_path_takes_precedence_over_impersonation(self):
        settings = Settings(
            google_application_credentials_path="/tmp/key.json",
            impersonate_service_account="sa@proj.iam.gserviceaccount.com",
        )
        with patch(
            "google.oauth2.service_account.Credentials.from_service_account_file"
        ) as from_file:
            from_file.return_value = "key-credentials"
            result = resolve_credentials(settings)
            assert result == "key-credentials"
            from_file.assert_called_once_with(
                "/tmp/key.json", scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )

    def test_impersonation_used_when_only_that_is_configured(self):
        settings = Settings(
            google_application_credentials_path="",
            impersonate_service_account="sa@proj.iam.gserviceaccount.com",
        )
        with (
            patch("google.auth.default") as auth_default,
            patch("google.auth.impersonated_credentials.Credentials") as impersonated_cls,
        ):
            auth_default.return_value = ("source-creds", "proj")
            impersonated_cls.return_value = "impersonated-credentials"

            result = resolve_credentials(settings)

            assert result == "impersonated-credentials"
            auth_default.assert_called_once_with(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            impersonated_cls.assert_called_once_with(
                source_credentials="source-creds",
                target_principal="sa@proj.iam.gserviceaccount.com",
                target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
                lifetime=3600,
            )
