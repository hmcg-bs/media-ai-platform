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

## 2026-09-12 — Meta warehouse live writes need BigQuery and Storage data roles

**RESOLVED 2026-09-12.** The owner created both resources and granted dataset Writer/Data Editor,
bucket Object Admin, and project BigQuery Job User. Live schema creation, object write/read, and
full metadata/feature backfill now pass.

**Blocked on**: Keyless WIF token minting succeeds, but the `amp-orbs` principal set receives
`bigquery.datasets.create` denied for `clean-patrol-496108-m9.ad_intelligence` and
`storage.buckets.create` denied for `clean-patrol-496108-m9-media-ai-ads`.

**Why it needs a human**: IAM and creation of shared cloud resources require a project
administrator. The existing logging/monitoring/service-usage/Vertex roles do not grant data
warehouse or object-storage writes.

**What I did instead**: Implemented and offline-tested append-only schemas, idempotent writes,
lifecycle polling, survival reports, and content-addressed image storage. No paid polling or bulk
image archive was attempted after the permission checks failed.

**Options, with a recommendation**: Prefer least privilege: have an administrator create the
`ad_intelligence` dataset in `us-central1` and the
`clean-patrol-496108-m9-media-ai-ads` bucket in `us-central1`, then grant the WIF principal set
BigQuery Data Editor on that dataset and Storage Object Admin on that bucket. Also grant BigQuery
Job User at project level for queries. Alternatively, project-level BigQuery User and Storage Admin
allow this workflow to create resources itself, but are broader than needed. Then rerun the schema
health check and a one-row sync before the full corpus sync.
