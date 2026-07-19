# Triage Labels

Issues flow through a five-state machine using these labels:

| Label | Meaning | Owner | Next State |
|-------|---------|-------|-----------|
| `needs-triage` | Maintainer must evaluate | Maintainer | → `needs-info` or `ready-for-agent` |
| `needs-info` | Waiting on reporter for details | Reporter | → `ready-for-agent` or `wontfix` |
| `ready-for-agent` | Fully specified, AFK-ready | Agent | Implement |
| `ready-for-human` | Needs human decision/review | Human | Implement |
| `wontfix` | Will not be actioned | Maintainer | Closed |

## Triage Workflow

1. **New issue arrives** → `triage` skill applies `needs-triage`
2. **Maintainer evaluates** → Add `needs-info` (if ambiguous) or `ready-for-agent` (if clear)
3. **Reporter responds** (if needed) → Remove `needs-info`, add `ready-for-agent`
4. **Agent picks up** → Works on `ready-for-agent` issues
5. **Human review needed** → Label as `ready-for-human`
6. **Won't fix** → Label as `wontfix` and close

## Label Names

These exact strings are used. Do not create variants like `bug/triage` or `status/needs-info`.

```
needs-triage
needs-info
ready-for-agent
ready-for-human
wontfix
```
