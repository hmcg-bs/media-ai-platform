# Domain Docs Consumer Rules

This repo uses **multi-context** domain documentation.

## Layout

```
Media AI Platform/
├── CONTEXT-MAP.md              ← Index mapping contexts
├── src/CONTEXT.md              ← Backend domain language
├── frontend/CONTEXT.md         ← Frontend domain language
└── docs/adr/                   ← Architecture Decision Records (global)
```

## When Skills Read Domain Docs

### Backend Work

Skills like `improve-codebase-architecture`, `diagnose`, and `tdd` read:

1. **`src/CONTEXT.md`** — Backend domain language (LiteLLM, Pydantic models, AI agents, adapters, etc.)
2. **`docs/adr/`** — Global architectural decisions

### Frontend Work

Same skills read:

1. **`frontend/CONTEXT.md`** — Frontend domain language (Next.js, React, components, hooks, etc.)
2. **`docs/adr/`** — Global architectural decisions

## What Goes in CONTEXT.md Files

**Backend (`src/CONTEXT.md`):**
- Domain entities (Copy, CopyVariant, Platform, Agent, etc.)
- Core abstractions (LiteLLM, Platform adapters, services, models)
- Data flow (API → services → agents → database)
- Naming conventions and patterns
- Known constraints and invariants

**Frontend (`frontend/CONTEXT.md`):**
- Component structure and naming
- Page hierarchy and routing patterns
- State management approach
- API client patterns
- TypeScript conventions

## What Goes in `docs/adr/`

Architectural Decision Records that apply globally or bridge frontend/backend:

- Technology choices (why FastAPI, Next.js, Supabase, etc.)
- Integration patterns (API client → backend)
- Deployment strategy
- Testing strategy
- Database schema design

**ADRs in this repo:**
- `ADR-001-litellm-abstraction.md` — All AI calls use LiteLLM, no vendor SDKs
- `ADR-002-pydantic-models.md` — Agents return structured Pydantic models
- `ADR-003-platform-adapters.md` — Every response goes through a platform adapter
- `ADR-004-knowledge-organization.md` — Vault/Structure vs Sessions split

## When to Update

- **CONTEXT.md** — After significant refactors or when domain language evolves
- **docs/adr/** — When making architecture decisions
- **CONTEXT-MAP.md** — If you add or reorganize contexts

---

Skills will prefer reading the most specific CONTEXT.md (backend or frontend) before consulting ADRs.
