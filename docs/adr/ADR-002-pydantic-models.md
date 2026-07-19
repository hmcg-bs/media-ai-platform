# ADR-002: Agents Return Pydantic Models (Structured Output)

> **⚠️ SUPERSEDED by [ADR-005](./ADR-005-supersede-media-ai-platform.md).** The
> *principle* survives — the new agent still returns validated Pydantic models
> (handed to Gemini as `response_schema`) — but the LiteLLM/agent framing is
> retired. Kept for history.

**Status:** Superseded by ADR-005  
**Date:** 2026-05-27  
**Author:** Avinaash Padman

## Decision

All agents return structured Pydantic models, never raw strings. AI output is parsed and validated at the agent boundary.

## Context

Early drafts returned raw strings from agents:

```python
# ❌ Bad: raw string
response = await generate_copy_agent(...)
copy_text = response  # Just a string, unvalidated
```

This causes problems:
- **No type safety.** Downstream code doesn't know if output is valid.
- **Silent failures.** Invalid output is passed along until it breaks in the adapter.
- **Hard to test.** No schema to validate against.
- **No structure.** Can't extract parts (headline vs body) from a blob of text.

## Decision

Every agent returns a Pydantic model. Output is parsed and validated before leaving the agent.

```python
# ✅ Good: structured Pydantic model
from src.models import CopyVariant

response: CopyVariant = await generate_copy_agent(...)
# response.headline, response.body, response.cta are guaranteed valid
```

The agent itself:
1. Calls the AI model
2. Parses the response into a Pydantic model
3. Validates it (Pydantic checks types, ranges, constraints)
4. Raises an error if invalid
5. Returns the validated model

## Consequences

### ✅ Benefits
- **Type safety.** Downstream code knows the shape of the data.
- **Early validation.** Errors caught at the source, not downstream.
- **Self-documenting.** The model shape is the contract.
- **Easier testing.** Can mock specific fields, validate structure.
- **Better error messages.** Pydantic validation errors are clear.

### ❌ Drawbacks
- **Extra parsing step.** Agent must convert AI text → Pydantic.
- **Stricter contracts.** If the AI output changes shape, validation fails.
- **More boilerplate.** Define models for each agent output type.

## Alternatives Considered

1. **Raw strings throughout** — No parsing, no validation
   - ❌ Error-prone, hard to debug

2. **Validate only at API boundary** — Agent returns string, API validates
   - ❌ Errors propagate through internal services
   - ❌ Hard to test agents in isolation

3. **JSON schema validation** — Parse to dict, validate against schema
   - ❌ Less type-safe than Pydantic
   - ❌ More verbose

## Implementation

**Models live in `src/models/`** (source of truth):

```python
# src/models/copy.py
from pydantic import BaseModel, Field

class CopyVariant(BaseModel):
    headline: str = Field(max_length=60)
    body: str = Field(max_length=200)
    cta: str = Field(max_length=40)
    framework: str  # "AIDA", "PAS", "Hook-Body-CTA"
```

**Agents parse and validate:**

```python
# src/agents/copy_generator.py
async def generate_copy_agent(brief: str) -> CopyVariant:
    response = await litellm.acompletion(...)
    # Parse AI response into dict
    data = json.loads(response.choices[0].message.content)
    # Validate and convert to Pydantic
    variant = CopyVariant(**data)
    return variant
```

If parsing fails or validation fails, the agent raises an error immediately.

## Related Decisions

- [[ADR-001-litellm-abstraction]] — All AI calls go through LiteLLM
- [[ADR-003-platform-adapters]] — Platform adapters receive Pydantic models as input
