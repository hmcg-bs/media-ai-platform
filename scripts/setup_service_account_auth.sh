#!/usr/bin/env bash
# Set up non-expiring service-account auth for long unattended runs.
#
# WHY THIS EXISTS
#   This project's normal auth is a *user* credential (`gcloud auth
#   application-default login`). On a Google Workspace domain those are
#   governed by "Google Cloud session control", which caps sessions at a
#   maximum of 24 hours — there is no never-expires option. That is why
#   `RefreshError: Reauthentication is needed` keeps interrupting long runs,
#   and why service-account *impersonation* did not fix it (it still mints
#   tokens from the underlying user credential).
#
#   A service-account KEY is different: it is not tied to a user session and
#   does not expire. That is the only way to keep auth alive for a week+.
#
#   pipeline/clients/gcp_auth.py already supports this path — it just needs
#   GOOGLE_APPLICATION_CREDENTIALS_PATH set in .env. No code change required.
#
# SECURITY NOTE
#   A key file is a long-lived credential. It is written to ./secrets/ which
#   must be gitignored. Never commit it. Delete it when the unattended period
#   ends (see the teardown command printed at the end).
#
# USAGE
#   bash scripts/setup_service_account_auth.sh

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-clean-patrol-496108-m9}"
SA_NAME="autonomous-crew"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/secrets"
KEY_PATH="${KEY_DIR}/${SA_NAME}.json"

echo "=============================================================="
echo " Service-account auth setup"
echo " Project: ${PROJECT_ID}"
echo " Service account: ${SA_EMAIL}"
echo " Key destination: ${KEY_PATH}"
echo "=============================================================="
echo

# ── 0. Preconditions ─────────────────────────────────────────────────
echo "[0/5] Checking gcloud CLI authentication..."
if ! gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  cat <<'EOF'
  ✗ gcloud CLI cannot reach the project.

    The gcloud CLI credential is SEPARATE from the ADC credential the
    pipeline uses — refreshing one does not refresh the other. Run BOTH:

        gcloud auth login
        gcloud auth application-default login

    Then re-run this script.
EOF
  exit 1
fi
echo "  ✓ gcloud CLI authenticated"
echo

# ── 1. Create the service account ────────────────────────────────────
echo "[1/5] Creating service account (skipped if it already exists)..."
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "  · already exists"
else
  gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="Autonomous dev crew (unattended pipeline runs)"
  echo "  ✓ created"
fi
echo

# ── 2. Grant roles ───────────────────────────────────────────────────
# aiplatform.user  -> Vertex Gemini calls (Generation's vision/text agents)
# serviceusage.serviceUsageConsumer -> quota project attribution
# cloudvision - only needed if Step 2 OCR is run; harmless to include.
echo "[2/5] Granting roles..."
for ROLE in \
  "roles/aiplatform.user" \
  "roles/serviceusage.serviceUsageConsumer"
do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet >/dev/null
  echo "  ✓ ${ROLE}"
done
echo

# ── 3. Create the key (the step the org policy may block) ────────────
echo "[3/5] Creating key..."
mkdir -p "${KEY_DIR}"
chmod 700 "${KEY_DIR}"

if [ -s "${KEY_PATH}" ]; then
  # -s (not -f): a 0-byte file counts as absent. A prior blocked attempt can
  # leave an empty file behind (gcloud opens the output path before it
  # validates the request), and a bare -f check would wrongly treat that as
  # "already set up" forever after.
  echo "  · key already exists at ${KEY_PATH} — leaving it alone"
elif gcloud iam service-accounts keys create "${KEY_PATH}" \
       --iam-account="${SA_EMAIL}" \
       --project="${PROJECT_ID}" 2>/tmp/sa_key_err.txt; then
  chmod 600 "${KEY_PATH}"
  echo "  ✓ key written to ${KEY_PATH}"
else
  echo
  echo "  ✗ Key creation was BLOCKED. Error:"
  sed 's/^/      /' /tmp/sa_key_err.txt
  cat <<EOF

  This is almost certainly the org policy
  'constraints/iam.disableServiceAccountKeyCreation', which this
  organization enforces by default.

  TO FIX (you need the Organization Policy Administrator role):

    Option A — Cloud Console (easiest):
      1. https://console.cloud.google.com/iam-admin/orgpolicies
      2. Select project '${PROJECT_ID}'
      3. Find "Disable service account key creation"
      4. Manage policy -> Customize -> Override parent's policy
         -> Enforcement: Off -> Set policy
      5. Re-run this script.

    Option B — gcloud:
      cat > /tmp/sa-key-policy.yaml <<'YAML'
      name: projects/${PROJECT_ID}/policies/iam.disableServiceAccountKeyCreation
      spec:
        rules:
          - enforce: false
YAML
      gcloud org-policies set-policy /tmp/sa-key-policy.yaml

  Re-tighten the policy once the unattended period is over.
EOF
  exit 1
fi
echo

# ── 4. Wire it into .env ─────────────────────────────────────────────
echo "[4/5] Wiring into .env..."
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
if grep -q "^GOOGLE_APPLICATION_CREDENTIALS_PATH=" "${ENV_FILE}" 2>/dev/null; then
  echo "  · GOOGLE_APPLICATION_CREDENTIALS_PATH already present — verify it points to:"
  echo "    ${KEY_PATH}"
else
  {
    echo ""
    echo "# Non-expiring service-account auth for unattended runs."
    echo "# Takes precedence over impersonation and user ADC (see gcp_auth.py)."
    echo "GOOGLE_APPLICATION_CREDENTIALS_PATH=${KEY_PATH}"
  } >> "${ENV_FILE}"
  echo "  ✓ appended to .env"
fi

# Impersonation is now redundant and would be ignored anyway (the key path
# wins in gcp_auth.py), but leaving it set is confusing. Flag it.
if grep -q "^IMPERSONATE_SERVICE_ACCOUNT=.\+" "${ENV_FILE}" 2>/dev/null; then
  echo "  ! IMPERSONATE_SERVICE_ACCOUNT is still set in .env."
  echo "    It is now ignored (key path takes precedence). Consider clearing it"
  echo "    to avoid confusion about which credential is actually in use."
fi
echo

# ── 5. Verify ────────────────────────────────────────────────────────
echo "[5/5] Verifying..."
if command -v uv >/dev/null 2>&1; then
  uv run python scripts/check_auth_health.py || true
else
  echo "  · uv not found; run 'uv run python scripts/check_auth_health.py' manually"
fi

cat <<EOF

=============================================================
 DONE.
 Auth should now survive indefinitely — no reauth prompts.

 Confirm .gitignore covers the key:
     grep -n "secrets/" .gitignore

 TEARDOWN when the unattended period ends (recommended):
     gcloud iam service-accounts keys list --iam-account=${SA_EMAIL}
     gcloud iam service-accounts keys delete <KEY_ID> --iam-account=${SA_EMAIL}
     rm -f ${KEY_PATH}
   ...then re-enable the org policy you turned off, if you turned one off.
=============================================================
EOF
