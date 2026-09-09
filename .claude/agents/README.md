# Generation Development Crew

Six agents for autonomous development of the ad-image Generation pipeline
(`pipeline/generation/`) while the repo owner is away.

**Shared contract**: [`docs/agents/autonomous-operation-protocol.md`](../../docs/agents/autonomous-operation-protocol.md)
— budget, git policy, escalation, Definition of Done. Every agent reads it
first. It exists so six agent files don't each carry a duplicated copy of the
same 200 lines of constraints.

## The crew

| Agent | Role | Model | Writes code? |
|---|---|---|---|
| `foreman` | Orchestrates. Owns the queue, dispatches, judges reports, decides next action. | opus | No |
| `pipeline-coder` | Implements one bounded change on the `auto/*` branch. | opus | Yes |
| `ad-evaluator` | Judges generated ads against the 14-entry failure-mode taxonomy. Deterministic checks first. | opus | No |
| `researcher` | External research to unblock the foreman. | sonnet | No |
| `code-reviewer` | Audits a diff before the foreman accepts it. | opus | No |
| `scribe` | Folds verified findings into the catalog, architecture doc, and wayfinder map. | sonnet | Docs only |

## How to start a session

The foreman is designed to run **in the main session**, where it can spawn the
others via the Agent tool:

```
Act as the foreman agent (.claude/agents/foreman.md). Read the protocol and the
autonomous log, then begin the decision loop. Scope: <what you want worked on>.
Budget: <N> live pipeline runs.
```

Sub-agents can also be dispatched directly for one-off work:

```
Use the ad-evaluator agent to evaluate /tmp/smoke_v2_full_run.png against its report JSON.
```

## Information flow

```
                    ┌──────────────┐
                    │   FOREMAN    │  decides, dispatches, judges, records
                    └──────┬───────┘
        ┌──────────┬───────┼────────┬──────────┐
        ▼          ▼       ▼        ▼          ▼
     coder    evaluator  research  reviewer  scribe
        │          │       │        │          │
        └──────────┴───────┴────────┴──────────┘
                    reports back only
```

Agents never talk to each other — everything routes through the foreman, and
every report must be self-contained (the foreman may have compacted context
since dispatching).

## Persistent state

| File | Purpose |
|---|---|
| `docs/agents/AUTONOMOUS-LOG.md` | Crew working memory. One entry per foreman decision cycle. **First thing a resuming foreman reads.** |
| `docs/agents/OWNER-QUEUE.md` | Blockers and decisions awaiting the owner. Read this on return. |
| Wayfinder map [#36](https://github.com/hmcg-bs/media-ai-platform/issues/36) | Permanent narrative record; readable remotely while away. |

## How your code is kept safe (plain English)

Git branches are the mechanism, and they work exactly the way you described
wanting: **your current code stays untouched, the crew's new code sits beside
it, and later you pick.**

Think of `master` as your real project. A branch is a *parallel copy* — the
crew works there, and nothing they do can alter `master` until someone
deliberately merges. They are forbidden from merging.

```
master        ──●──●──●────────────────────  ← your work, untouched
                       ╲
auto/dup-fix            ●──●──●              ← crew's work, isolated
```

When you're back, for each branch you have three choices:

| You want to… | Command |
|---|---|
| See what they changed | `git log master..auto/<name>` and `git diff master...auto/<name>` |
| Try it out without committing to it | `git checkout auto/<name>` — then `git checkout master` to go back |
| **Keep it** | `git checkout master && git merge auto/<name>` |
| **Throw it away** | `git branch -D auto/<name>` (deletes the branch; `master` never knew it existed) |

Discarding costs nothing and loses nothing of yours. That's the whole point of
the setup — the crew can be wrong safely.

One branch per topic keeps those choices independent: you can accept the
duplicate-detection work and bin the layout experiment, rather than having to
take or leave everything at once. The foreman is instructed to name them
`auto/<topic>` and keep them focused.

If you'd rather review on GitHub than in the terminal, the crew pushes each
branch, so `https://github.com/hmcg-bs/media-ai-platform/branches` will list
them and you can open a pull request to see a readable diff — **just don't
click merge until you've decided.**

## Safety summary

- All work on `auto/*` branches. **Merging to `master` is never authorized.**
- Committing and pushing to `auto/*` **is** pre-authorized (that's how you
  review remotely) — this narrowly overrides the standing "never commit
  without asking" rule, for this crew only, for the away-period only.
- Live runs cost real money; the foreman counts them against an explicit
  budget (default 12).
- GCP ADC expiry requires interactive login no agent can perform → the crew
  escalates and switches to credential-free work rather than looping.
- Product/scope decisions (e.g. bug #14) go to the owner queue, never decided
  unilaterally.
