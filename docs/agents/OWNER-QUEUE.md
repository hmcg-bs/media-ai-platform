# Owner Queue

Things the autonomous crew could not decide or unblock on its own. **Read this
first on return.** Newest at the bottom.

Agents append here rather than guessing when they hit: expired credentials,
budget exhaustion, a product/scope question, a paid external account, anything
touching `master`, or data deletion. See
`docs/agents/autonomous-operation-protocol.md` §6.

---

## Entry template

```markdown
## <ISO date> — <one-line summary>
**Blocked on**: <what exactly>
**Why it needs a human**: <credential / cost / product decision / ambiguity>
**What I did instead**: <the work switched to>
**Options, with a recommendation**: <2-3 concrete choices, the pick, why>
```

---

## 2026-08-30 — Known blockers carried in from handover (pre-existing, not crew-generated)

**1. GCP auth expiry — ROOT-CAUSED, fix available, ACTION REQUIRED before a
long away-period.** Vertex Gemini calls periodically fail with
`RefreshError: Reauthentication is needed`.

*Root cause* (diagnosed 2026-08-30): ADC is a **user** credential
(`authorized_user`) on the Workspace domain `battenstreet.com`. Google
Workspace's "Google Cloud session control" caps user sessions at a **maximum
of 24 hours**, with **no never-expires option** — so no amount of re-logging-in
buys a week. Service-account *impersonation* does not escape it either
(confirmed Round 4): it still mints tokens from the underlying user
credential. Current state verified via `scripts/check_auth_health.py`:
impersonation mode, 24h ceiling.

*Fix*: use a service-account **key**, which is not tied to a user session and
does not expire. `pipeline/clients/gcp_auth.py` already supports this — it
only needs `GOOGLE_APPLICATION_CREDENTIALS_PATH` in `.env`. No code change.

    bash scripts/setup_service_account_auth.sh

The one thing that may block it: the org policy
`constraints/iam.disableServiceAccountKeyCreation`. The script detects this
and prints exact override instructions (you likely have the rights, being on
your own Workspace domain). Verify afterwards with
`uv run python scripts/check_auth_health.py` — exit 0 means indefinite.

**Delete the key when the away-period ends** (teardown commands are printed
by the setup script). `secrets/` is now gitignored so the key can't be
committed.

**2. Apify monthly usage hard limit exceeded** — blocks
`ingestion/refresh_image_urls.py`, which is the only durable fix for
Facebook CDN signed-URL expiry. Consequence: `get_top_reference_ads()` returns
0 ads, so Generation's feature-fidelity gate is inert (it correctly reports
`checked=False` rather than fabricating a verdict). Needs the quota window to
reset or the account limit raised.

**3. Bug #14 — product decision, deliberately not decided by the crew**:
should `validate_layout()`'s `zones_respected=False` finding gate the
regeneration loop? Currently it's recorded for observability only, a
deliberate Generation v2 scope decision. Live evidence so far: one run where
scene props intruded into a reserved zone but the compositor's background band
absorbed it with no legibility loss — suggesting a gate might cause
unnecessary regenerations. One observation is not enough to settle it.
**Your call.**

**4. Uncommitted Generation v2 work** — at handover, 17 changed/new files
(the whole v2 build plus docs) were uncommitted on `master`. Decide whether to
commit them before the crew builds on top.
