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

**1. GCP auth expiry — ROOT-CAUSED; keyless orb fix implemented, GOOGLE ADMIN
SETUP REQUIRED.** Vertex Gemini calls periodically fail with
`RefreshError: Reauthentication is needed`.

*Root cause* (diagnosed 2026-08-30): ADC is a **user** credential
(`authorized_user`) on the Workspace domain `battenstreet.com`. Google
Workspace's "Google Cloud session control" caps user sessions at a **maximum
of 24 hours**, with **no never-expires option** — so no amount of re-logging-in
buys a week. Service-account *impersonation* does not escape it either
(confirmed Round 4): it still mints tokens from the underlying user
credential. Current state verified via `scripts/check_auth_health.py`:
impersonation mode, 24h ceiling.

*Fix*: use Amp OIDC with Google Workload Identity Federation, not a
service-account key. The repository lifecycle/helper implementation and exact
administrator commands are in `docs/gcp-orb-auth.md`. The owner must create the
Google pool/provider and IAM bindings, then add `GCP_PROJECT_ID` and the full
`GCP_WIF_PROVIDER` resource to the Amp project environment. Verify afterwards
with `uv run python scripts/check_auth_health.py`.

**2. Apify quota — RESOLVED 2026-09-12.** The owner reset the quota and the
supplements scrape completed all 12 queries: 3,555 unique image ads across
1,151 pages. Facebook CDN URLs still expire, so resumable refresh/download
artifacts remain necessary, but account quota is no longer the blocker.

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
