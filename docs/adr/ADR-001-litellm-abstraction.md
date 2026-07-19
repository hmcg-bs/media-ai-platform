# ADR-001: Use LiteLLM for AI Provider Abstraction

> **⚠️ SUPERSEDED by [ADR-005](./ADR-005-supersede-media-ai-platform.md).** The
> Media AI Platform was replaced by the GCP Social Media Analysis Agent, which
> calls Vertex AI Gemini via the google-genai SDK, not LiteLLM. Kept for history.

**Status:** Superseded by ADR-005  
**Date:** 2026-05-27  
**Author:** Avinaash Padman

## Decision

Use LiteLLM as a unified interface for all AI model calls instead of directly importing `anthropic` or `openai` SDKs.

## Context

The Media AI Platform supports multiple AI models for different use cases:
- Claude Sonnet for reasoning-heavy tasks (copy generation, critique)
- GPT-4o for vision/image analysis
- GPT-4o-mini for fast/bulk operations
- Ollama for local/offline development

Hardcoding vendor-specific SDK imports (`from anthropic import Anthropic`, `from openai import OpenAI`) tightly couples the codebase to vendors. Switching models or adding new providers becomes painful — it requires changes across multiple files and agent implementations.

## Decision

All AI calls go through LiteLLM's unified API (`litellm.completion()`, `litellm.acompletion()`). The model string is injected from `src/config/model_routing.py` at runtime.

**Rule:** Never import `anthropic` or `openai` SDKs directly. All calls use `litellm`.

## Consequences

### ✅ Benefits
- **Model swapping is a config change.** Change `.env` or `src/config/model_routing.py`, no code rewrites.
- **Easy to add new providers.** Register in model_routing, done.
- **Vendor lock-in is optional.** Can migrate providers without refactoring agents.

### ❌ Drawbacks
- **Extra abstraction layer.** Slight latency overhead and additional dependency.
- **Vendor-specific features harder to use.** Streaming, tool_choice, vision parameters must be routed through LiteLLM's API.
- **LiteLLM dependency risk.** If LiteLLM breaks, all AI calls break.

## Alternatives Considered

1. **Direct SDK imports per model** — Each agent imports the vendor SDK it needs
   - ❌ Couples code to vendors
   - ❌ Model swapping requires agent rewrites
   - ❌ High maintenance cost

2. **Custom abstraction layer** — Build our own provider wrapper
   - ❌ Reinvents the wheel
   - ❌ Maintenance burden on the team
   - ✅ Full control, no external dependency

3. **No abstraction; pick one model** — Use only Claude (or only GPT-4)
   - ❌ Limits flexibility
   - ❌ Can't optimize cost per use case

## Implementation

All agents call `litellm` at the boundary:

```python
from litellm import acompletion
from src.config.model_routing import get_model_for_task

model = get_model_for_task("copy_generation")
response = await acompletion(
    model=model,
    messages=[...],
    temperature=0.7,
)
```

Model routing is centralized in `src/config/model_routing.py`:

| Use Case | Primary | Fallback |
|---|---|---|
| Copy generation | claude-sonnet-4-6 | gpt-4o-mini |
| Copy critique | claude-sonnet-4-6 | (same) |
| Vision/image analysis | gpt-4o | (none) |
| Fast/bulk variants | gpt-4o-mini | (none) |
| Embeddings | text-embedding-3-small | (none) |
| Local/offline dev | ollama/llama3.2 | (none) |

## Related Decisions

- [[ADR-002-pydantic-models]] — Agents return structured Pydantic models, not raw strings
- [[ADR-003-platform-adapters]] — Every response goes through a platform adapter for format validation
