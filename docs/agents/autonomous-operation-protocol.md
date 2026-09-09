# Autonomous Operation Protocol

**The shared contract every agent in the Generation crew operates under.**
Read this in full at the start of every task. It is referenced by
`.claude/agents/*.md` rather than duplicated into each of them.

Context: this crew runs **while the repo owner is away**. Nobody is watching
in real time. That single fact drives every rule below — the cost of a silent
mistake is much higher than the cost of stopping and asking.

---

## 1. The prime directives

1. **Everything is provisional until the owner approves it.** No work product
   is "done." It is "ready for review." See §4 (Git policy).
2. **Never claim a fix works without live evidence.** This project's entire
   documentation discipline rests on symptom → root cause → fix → regression
   test → **confirmed live**. A change that passes unit tests but was never
   run against a real generated image is *unverified*, and must be reported
   as such.
3. **Report negative and partial findings honestly.** "Mitigated, not
   eliminated" and "fixed X but Y still fails" are the norm here — see bugs
   #6, #12, #13, #14 in `docs/generation-failure-modes.md`. An agent that
   overclaims a fix does more damage than one that fails, because it poisons
   the catalog future rounds rely on.
4. **Deterministic before generative.** If code/math can compute it, never ask
   a model. This is `CLAUDE.md`'s stated principle and was the direct cause of
   a real architecture correction (Round 7). Applies to *evaluation* too, not
   just the pipeline.
5. **Stay inside the assigned scope.** If you discover work outside your
   task, write it down and report it. Do not opportunistically fix it.

---

## 2. Required reading before any task

| File | Why |
|---|---|
| `CLAUDE.md` | Authoritative coding conventions. Overrides default behavior. |
| `CONTEXT.md` | Domain glossary. Use these exact terms. |
| `docs/PROJECT-HANDOVER.md` | Project-wide state, what's built vs. not. |
| `docs/generation-v1-architecture.md` | Generation's current (v2) architecture. |
| `docs/generation-failure-modes.md` | 14 known failure modes. **Check here before diagnosing anything — it likely already exists.** |

---

## 3. Hard constraints (violating these is a failed task)

- **`os.environ` only in `pipeline/config.py::get_settings()`.** Nowhere else.
- **All Vertex Gemini calls via `GenAIClient`** (`google-genai`). All Replicate
  calls via the clients in `pipeline/clients/replicate_client.py` (the
  ADR-008-scoped exception). Never import `litellm`, `anthropic`, or `openai`.
- **Never modify** `archive/old-src/`, `frontend/`, or top-level `tests/` —
  retired scaffold from the superseded product (ADR-005).
- **Never edit `data/*.json` corpus or report files.** They are inputs and
  expensive to regenerate. Read only.
- **Type hints on every function. Pydantic models are the source of truth** —
  don't create parallel data shapes alongside `models/output_schema.py` or
  `pipeline/generation/layout.py`'s `LayoutPlan`/`BoundingBox`.
- **Tests: hand-rolled fakes only.** No mocking libraries beyond pytest's
  `monkeypatch`. Tests must run offline (`uv run pytest pipeline/tests` needs
  no GCP credentials).
- **`uv run` for everything.** Python is pinned to 3.12; the system 3.14 is
  not deployable.

---

## 4. Git policy while the owner is away

The owner's standing rule is *never commit or push without explicit
authorization*. That rule cannot be satisfied literally by an unattended crew,
so it is replaced — **only for this crew, only for the duration of the
away-period** — by this narrower one:

- **All work happens on a branch named `auto/<short-topic>`.** Never on
  `master`.
- **Committing to that branch is pre-authorized.** Commit early and often with
  descriptive messages, so the owner can read the history as a narrative.
- **Pushing that branch to `origin` is pre-authorized** (it is how the owner
  reviews remotely while away).
- **Merging to `master` is NOT authorized. Ever.** No fast-forward, no PR
  merge, no rebase onto master. Opening a PR for review is fine; merging it is
  not.
- **Never force-push. Never rewrite published history. Never `git reset --hard`,
  `git checkout -- .`, or `git clean` without first checking `git status` and
  stashing anything untracked.**
- Before the first commit of a session, confirm the working tree's existing
  uncommitted work is either included deliberately or stashed — do not sweep
  unrelated changes into a commit.

---

## 5. Cost and credentials

Every live pipeline run spends **real money** (Replicate: background-remover +
Flux Fill; Vertex: 5-6 Gemini calls per attempt). A 3-variant run is roughly
3× a single run.

- **Budget rule**: the Foreman authorizes live runs explicitly and counts
  them. Default ceiling is **12 full pipeline runs per away-period** unless the
  owner set a different number. Isolated single-step tests (one Flux Fill call,
  one background-removal) count as ⅕ of a run.
- **Prefer the cheapest evidence that answers the question.** Test one step in
  isolation before paying for an end-to-end run. Reuse existing artifacts in
  `/tmp/smoke_*` before generating new ones.
- **GCP auth — check this before you plan anything.** Run
  `uv run python scripts/check_auth_health.py` at session start. It reports
  which credential is actually in use and, critically, **how long it will
  last**:
  - **Service-account key** (`GOOGLE_APPLICATION_CREDENTIALS_PATH` set) →
    does not expire. Safe for long unattended runs. This is the intended
    setup; `scripts/setup_service_account_auth.sh` establishes it.
  - **User ADC or impersonation** → **24 hours maximum.** A Google Workspace
    "Google Cloud session control" policy caps user sessions at 24h and offers
    no never-expires option. Impersonation does *not* escape this — it still
    mints tokens from the underlying user credential (confirmed live, Round 4).
    If the crew is on this credential, **say so in your first log entry**: a
    week-long run *will* be interrupted, and the owner should be told.
  - When it does expire, the failure is
    `google.auth.exceptions.RefreshError: Reauthentication is needed`. Fixing
    it needs an **interactive** `gcloud auth application-default login` no
    agent can perform. **Stop, do not retry in a loop, escalate** (§6), and
    switch to work that doesn't need Vertex (unit tests, deterministic
    checks, research, documentation).
  - **Do not conclude auth is broken from one failure.** A transient
    `SSLError`/`TransportError` against `oauth2.googleapis.com` looks alarming
    but is usually a network flake — observed and confirmed benign on this
    machine. Re-run once before escalating.
  - Note that the `gcloud` CLI credential and the ADC credential are
    **separate stores**; refreshing one does not refresh the other.
- **Replicate and Apify tokens are static API keys** — they do not expire on a
  session timer. Only the GCP/Vertex side has this problem.
- **Apify is quota-blocked** (`Monthly usage hard limit exceeded`), so
  reference-ad image refresh is unavailable. Do not burn attempts on it.
  Consequence: `get_top_reference_ads()` returns 0 in practice and the
  feature-fidelity gate is inert. Design around this; don't rediscover it.

---

## 6. Escalation — the owner's queue

When blocked on something only the owner can resolve, **do not guess and
proceed**. Append to `docs/agents/OWNER-QUEUE.md` (create if absent) with:

```markdown
## <ISO date> — <one-line summary>
**Blocked on**: <what exactly>
**Why it needs a human**: <credential / cost / product decision / ambiguity>
**What I did instead**: <the work you switched to>
**Options, with a recommendation**: <2-3 concrete choices, your pick, why>
```

Escalate — don't decide alone — for: expired credentials; exceeding the run
budget; any product/scope question ("should X gate regeneration?"); anything
requiring a paid external account; any change to `master`; deleting data.

---

## 7. Definition of Done (per work item)

A work item is **ready for review** — never "done" — only when all of these
hold, and the agent states explicitly which ones it could not satisfy:

1. `uv run pytest pipeline/tests -q` passes, with new tests for the specific
   failure mode addressed.
2. `uv run ruff check <changed paths>` is clean.
3. **Live evidence exists** — a real run, with artifacts saved and referenced
   by path — or an explicit statement that live verification was blocked and
   why.
4. The change is documented: `docs/generation-failure-modes.md` for a bug,
   `docs/generation-v1-architecture.md` for an architecture change.
5. Committed to the `auto/*` branch with a message explaining *why*, not just
   what.
6. Known limitations of the fix are written down, not omitted.

---

## 8. Artifact conventions

- **Live-run outputs**: `/tmp/auto_<agent>_<topic>_<n>.png` plus a sibling
  `.report.json` when the pipeline produced one. Never overwrite an artifact
  another agent may still be reasoning about — increment `<n>`.
- **Scratch scripts**: the session scratchpad directory, never the repo.
- **Evaluation verdicts**: returned to the Foreman as structured text (schema
  in `.claude/agents/ad-evaluator.md`), and appended to the run log.
- **Run log**: `docs/agents/AUTONOMOUS-LOG.md` — one dated entry per Foreman
  decision cycle. This is the crew's shared memory across context resets and
  the first thing a resuming Foreman reads.

---

## 9. Communication rules between agents

- Agents **do not talk to each other directly.** Everything routes through the
  Foreman. A sub-agent's final report is its only output channel.
- **Reports must be self-contained.** The Foreman may have compacted context
  since dispatching you. Restate what you were asked, what you found, and what
  you changed — don't rely on shared memory.
- **Include file paths and line references** (`pipeline/generation/background.py:230`)
  for anything the Foreman may need to look at.
- **State confidence explicitly.** Distinguish "confirmed by running it" from
  "inferred from reading the code." The Foreman routes differently based on
  which.
