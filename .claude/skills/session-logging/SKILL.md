---
name: session-logging
description: Write the end-of-session Obsidian vault logs for this project — the daily work log, changelog, and git log under Vault/Sessions/. Use at the end of a working session, after features change, or after commits.
---

# Obsidian Vault Protocol

Project knowledge lives in `Vault/`, split into:
- **`Structure/`** — static reference (ADRs, conventions, schemas). Update only when finalizing decisions.
- **`Sessions/`** — append-only work logs (`Daily/YYYY-MM-DD.md`, `Changelog.md`, `Git-Log.md`).

See [ADR-004](../../../docs/adr/ADR-004-knowledge-organization.md) (still in force).

## Session Logging (end of every session)

1. Write `Vault/Sessions/Daily/YYYY-MM-DD.md` (goal, decisions, files changed, next steps).
2. Append to `Vault/Sessions/Changelog.md` if features changed.
3. Append to `Vault/Sessions/Git-Log.md` after commits.
