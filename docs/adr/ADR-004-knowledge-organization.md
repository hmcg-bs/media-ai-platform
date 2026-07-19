# ADR-004: Separate Knowledge Into Structure (Reference) and Sessions (Audit Trail)

**Status:** Accepted  
**Date:** 2026-05-27  
**Author:** Avinaash Padman

## Decision

All project knowledge in the Obsidian vault (`Vault/`) is split into two sections:
- **`Vault/Structure/`** — Static, read-only architectural reference
- **`Vault/Sessions/`** — Append-only work logs and audit trail

## Context

As the project grew, it became unclear whether to treat the vault as:
- **A living document** (continuously updated, best practices change)
- **An audit trail** (immutable record of decisions and work)
- **Reference material** (stable documentation)

These purposes conflict. Continuously rewriting architectural decisions obscures *why* they were made. But appending every decision creates noise and drift.

## Decision

**Two-layer system:**

### Layer 1: `Vault/Structure/` — Reference (Read-Only)

Static architectural documentation updated only when decisions are finalized:
- `Architecture/` — ADRs (Architecture Decision Records)
- `Conventions/` — Team guidelines and coding standards
- `Schemas/` — JSON schemas, database schemas
- `Models/` — AI model routing, prompt templates, vector schemas
- `Platforms/` — Domain knowledge (platform APIs, copywriting frameworks)

These files are *stable*. Once written, they're rarely updated. When they are, it's because a decision was reversed or clarified, which itself warrants an ADR.

### Layer 2: `Vault/Sessions/` — Work Logs (Append-Only)

Immutable record of work and decisions:
- `Daily/` — One markdown file per Claude session (YYYY-MM-DD.md)
- `Changelog.md` — Meaningful feature additions/removals
- `Git-Log.md` — Table of all commits
- `Decisions/` — One-off decisions with timestamps

These files only grow. Nothing is deleted or rewritten.

## Mapping of Events

| Event | File | Action |
|---|---|---|
| Architecture decision finalized | `Structure/Architecture/ADR-NNN.md` | Create new ADR file |
| Coding convention established | `Structure/Conventions/NAME.md` | Create convention doc |
| Claude session ends | `Sessions/Daily/YYYY-MM-DD.md` | Create with: goal, decisions, code changes, next steps |
| Feature shipped | `Sessions/Changelog.md` | Append entry under `[Unreleased]` |
| Git commit made | `Sessions/Git-Log.md` | Append row: date \| branch \| commit \| description |
| Emergent decision | `Sessions/Decisions/YYYY-MM-DD-NAME.md` | Create with timestamp |

## Consequences

### ✅ Benefits
- **Stable reference.** Architecture docs don't change unexpectedly.
- **Audit trail.** Work logs show what was built and why.
- **Easy onboarding.** New team members read `Structure/` for how things work.
- **Blame attribution.** `Sessions/Git-Log.md` shows who did what and when.
- **Decision history.** `Decisions/` shows how thinking evolved.

### ❌ Drawbacks
- **Vault duplication.** Some decisions live in both `ADR` and session notes.
- **Maintenance burden.** Two locations to update.
- **Can drift.** Code evolves; architectural docs may become stale.

## Alternatives Considered

1. **Single unified vault** — Everything grows together
   - ❌ Can't distinguish permanent decisions from temporary notes
   - ❌ Hard to find what's current vs historical

2. **Git history as source of truth** — Rely on commit messages and blame
   - ❌ Requires deep git knowledge
   - ❌ Hard to reason about *why* architectural choices were made

3. **Wiki (Living document)** — Continuous collaborative editing
   - ❌ Loses history of why decisions were made
   - ❌ Opinions evolve; docs become unreliable

## Implementation

**Before session:** Read `Vault/Structure/` to understand architecture.

**During session:** Take notes in `Sessions/Daily/YYYY-MM-DD.md`.

**After session:** 
1. Write session summary to `Sessions/Daily/YYYY-MM-DD.md`
2. Update `Sessions/Changelog.md` if features changed
3. Update `Sessions/Git-Log.md` after each commit
4. If architectural decision was made, create `Structure/Architecture/ADR-NNN.md`

**Never:** Edit `Structure/` docs mid-session. If architecture needs to change, document it in a new ADR and update `Structure/` after decision is finalized.

## Related Decisions

- All architectural decisions → `Structure/Architecture/ADR-*.md`
- Complement with `CONTEXT.md` files in source directories for domain language
