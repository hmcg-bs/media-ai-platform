# GCP authentication for Amp orbs

Amp orbs authenticate to Cloud Vision and Vertex AI with short-lived Amp OIDC
tokens exchanged through Google Workload Identity Federation (WIF). This is
keyless Application Default Credentials (ADC): do not upload a service-account
JSON key and do not run interactive `gcloud auth application-default login` in
an orb.

The pipeline remains unchanged. `VisionClient` calls Cloud Vision
`document_text_detection`, and Google client libraries discover the generated
external-account file through `GOOGLE_APPLICATION_CREDENTIALS`.

## One-time Google administrator setup

Run these commands from a trusted workstation authenticated as an IAM
administrator. The resource project currently named by the Amp audience is
`clean-patrol-496108-m9`; confirm it before applying changes. The identity pool
may live in that project or a separate security project.

```bash
export GCP_RESOURCE_PROJECT_ID='clean-patrol-496108-m9'
export GCP_IDENTITY_PROJECT_ID='<project-that-owns-the-pool>'
export GCP_IDENTITY_PROJECT_NUMBER='<numeric-project-number>'
export POOL_ID='amp-orbs'
export PROVIDER_ID='amp-provider'
export AMP_WORKSPACE_ID='bddde7cf-dcba-4527-8bb3-a06b35b795ed'
export AMP_PROJECT_ID='d348bad5-f47e-41ab-a230-60a0fa8f3e08'
export AMP_GCP_AUDIENCE="urn:amp:gcp:${GCP_RESOURCE_PROJECT_ID}"

gcloud iam workload-identity-pools create "$POOL_ID" \
  --project="$GCP_IDENTITY_PROJECT_ID" \
  --location=global \
  --display-name='Amp orbs'

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$GCP_IDENTITY_PROJECT_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --issuer-uri='https://ampcode.com/api/workload-identity' \
  --allowed-audiences="$AMP_GCP_AUDIENCE" \
  --attribute-mapping='google.subject=assertion.thread_id,attribute.workspace_id=assertion.workspace_id,attribute.project_id=assertion.project_id,attribute.user_id=assertion.user_id' \
  --attribute-condition="assertion.workspace_id == '${AMP_WORKSPACE_ID}' && assertion.project_id == '${AMP_PROJECT_ID}' && assertion.token_use == 'exchanged'"

gcloud services enable \
  vision.googleapis.com \
  aiplatform.googleapis.com \
  sts.googleapis.com \
  --project="$GCP_RESOURCE_PROJECT_ID"

principal_set="principalSet://iam.googleapis.com/projects/${GCP_IDENTITY_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.project_id/${AMP_PROJECT_ID}"

# Cloud Vision requests using inline image bytes need service usage permission.
gcloud projects add-iam-policy-binding "$GCP_RESOURCE_PROJECT_ID" \
  --member="$principal_set" \
  --role='roles/serviceusage.serviceUsageConsumer'

# Vertex Gemini calls need permission to invoke publisher models.
gcloud projects add-iam-policy-binding "$GCP_RESOURCE_PROJECT_ID" \
  --member="$principal_set" \
  --role='roles/aiplatform.user'
```

If a target API rejects a direct federated principal, create a narrow service
account, grant the principal set `roles/iam.workloadIdentityUser` on that
service account, and configure `GCP_WIF_SERVICE_ACCOUNT`. Do not create a key.
Direct federation should be tested first because it has fewer identities and
grants to maintain.

## Amp project environment

Set these non-secret environment variables in the Amp project. The provider
value uses the **numeric** project number that owns the identity pool.

```text
GCP_PROJECT_ID=clean-patrol-496108-m9
AMP_GCP_AUDIENCE=urn:amp:gcp:clean-patrol-496108-m9
GCP_WIF_PROVIDER=projects/451537045072/locations/global/workloadIdentityPools/amp-orbs/providers/amp-provider
GOOGLE_APPLICATION_CREDENTIALS=/home/user/workspace/repo/.amp/gcp-external-account.json
GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES=1
```

Optionally set `GCP_WIF_SERVICE_ACCOUNT=<email>` only when impersonation is
required. These are identifiers and paths, not credentials. Project-level
settings are preferred so every orb for this repository receives the same
configuration.

`.agents/resume` validates the configuration and atomically creates the ignored,
mode-0600 ADC file. `scripts/amp-gcp-identity` mints a ten-minute token only when
Google refreshes ADC; it never stores the token.

After changing Amp settings, refresh the orb processes:

```bash
amp orb restart-processes
```

## Verification

Run the health check first, then one paid OCR request before starting a corpus
backfill:

```bash
uv run python scripts/check_auth_health.py
uv run pytest pipeline/tests/test_orb_gcp_auth.py pipeline/tests/test_vision_client.py -q
```

The health check must report an external-account/WIF credential and a successful
token refresh. Then run the workflow's one-image OCR smoke path before using
`--reprocess-ocr` on the seeded 600-ad supplements set. Never print an ID token,
access token, API token, or credential-file contents while diagnosing auth.

## Failure modes

- `DefaultCredentialsError` with a missing `.amp/gcp-external-account.json`:
  one or more Amp environment values above is absent; inspect `.agents/resume`
  output after refreshing the orb.
- STS `invalid_target`: `GCP_WIF_PROVIDER` does not name the created provider.
- STS audience or attribute-condition failure: the provider audience or immutable
  Amp workspace/project IDs do not match this document.
- `PERMISSION_DENIED`: token exchange worked but the principal set lacks a
  resource-project role, or an API is not enabled.
- `GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES` error: it must be exactly `1`.

References: [Amp handling secrets](https://ampcode.com/docs/orbs/handling-secrets),
[Google WIF](https://cloud.google.com/iam/docs/workload-identity-federation), and
[Cloud Vision authentication](https://cloud.google.com/vision/docs/authentication).
