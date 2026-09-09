# Autonomous Crew — Run Log

Working memory for the Generation development crew
(`.claude/agents/`). One entry per Foreman decision cycle, newest at the
bottom. **A resuming Foreman reads this first** — it is the authoritative
record of state across context resets, and should be trusted over
recollection.

Durable findings graduate out of here into
`docs/generation-failure-modes.md`, `docs/generation-v1-architecture.md`, and
wayfinder map [#36](https://github.com/hmcg-bs/media-ai-platform/issues/36).
This file is the scratch narrative; those are the permanent record.

**Never delete entries.** To shorten a long log, add a consolidated summary
section above the detail, leaving the detail in place.

---

## Entry template

```markdown
## Cycle <n> — <ISO datetime> — <one-line summary>
**State**: <branch, suite status, live runs used/remaining>
**Decision**: <what you chose and why — the reasoning, not just the action>
**Dispatched**: <agent> — <task summary>
**Result**: <what came back, and your assessment of it>
**Verified**: <what you checked yourself vs. took on trust>
**Open**: <what this leaves unresolved>
**Next**: <intended next move>
```

---

## Session 0 — 2026-08-30 — crew created, no cycles run yet

**State at handover**: branch `master`; 408 tests passing; Generation v2
(layout-first planning, deterministic-first duplicate detection, surgical
repair, multi-variant generation) implemented and live-verified once
end-to-end, but **uncommitted** in the working tree. Live-run budget unused.

**Known open threads** (candidate first work items, detail in
`docs/generation-failure-modes.md`):
- Bug #13 — surgical repair can reintroduce a duplicate in the erased region;
  capped-retry-then-full-regen is the current mitigation. Genuinely open.
- Bug #14 — `validate_layout`'s `zones_respected=False` doesn't gate
  regeneration. **Product decision — owner's call, do not decide.**
- Duplicate detection missed 3 smaller hallucinated jars while correctly
  catching the primary duplicate bottle. Bounded and tractable.
- Reference-ad retrieval returns 0 (CDN expiry + Apify quota) → the
  feature-fidelity gate is inert. Not fixable by this crew; design around it.

**Next**: a Foreman session should start by confirming the state of the
uncommitted Generation v2 work before building on top of it.
