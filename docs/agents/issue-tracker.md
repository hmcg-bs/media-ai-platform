# Issue Tracker — GitHub

Issues for this repo live in **GitHub Issues**.

## For Skills

Skills that read/write issues (`to-issues`, `triage`, `to-prd`, `qa`) use the GitHub CLI (`gh`):

- `gh issue create` — create a new issue
- `gh issue view <number>` — fetch an issue
- `gh issue list --label <label>` — filter by label
- `gh issue edit <number> --add-label <label>` — add labels
- `gh issue edit <number> --remove-label <label>` — remove labels

## Workflow

1. **Create** — `to-issues` skill writes a new GitHub issue
2. **Triage** — `triage` skill applies labels from the canonical set
3. **Promote** — Issues progress through states: `needs-triage` → `needs-info` → `ready-for-agent` → `ready-for-human` or `wontfix`
4. **Implement** — Agent picks up `ready-for-agent` issues; human handles `ready-for-human`

## Setup

Ensure you have `gh` CLI installed and authenticated:

```bash
gh auth login
```

Then skills can create and manage issues automatically.
