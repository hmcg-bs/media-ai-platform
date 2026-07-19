# ADR-003: Platform Adapters Enforce Format Constraints

> **⚠️ SUPERSEDED by [ADR-005](./ADR-005-supersede-media-ai-platform.md).**
> Platform adapters belonged to the copy-generation product. The Social Media
> Analysis Agent analyses creatives; it has no platform-formatting layer. Kept
> for history.

**Status:** Superseded by ADR-005  
**Date:** 2026-05-27  
**Author:** Avinaash Padman

## Decision

Every copy response goes through a platform-specific adapter before being returned to the user. Adapters enforce format constraints (character limits, allowed characters, formatting rules) for each platform.

## Context

Different platforms have different constraints:

| Platform | Headline Limit | Body Limit | Format Rules |
|----------|---|---|---|
| Meta (Facebook Ad) | 40 chars | 125 chars | No URLs in headline, `{` `}` not allowed |
| Google Ads | 30 chars | 90 chars | Single line, sanitize special chars |
| TikTok Captions | 150 chars | Unlimited | Emojis encouraged, hashtags suggested |
| LinkedIn Post | Unlimited | 3000 chars | Paragraph breaks, mentions with @ |

Without adapters, the API could return copy that violates platform rules — the frontend or marketer would discover this too late.

## Decision

All adapters inherit from `BasePlatformAdapter` and implement:
- `format_copy()` — Reformat copy to fit platform constraints
- `validate_copy()` — Check if copy violates rules
- `get_constraints()` — Return the platform's constraints

```python
class BasePlatformAdapter(ABC):
    @abstractmethod
    def format_copy(self, variant: CopyVariant) -> FormattedCopy:
        """Enforce platform-specific format constraints."""
        pass

    @abstractmethod
    def validate_copy(self, copy: str) -> ValidationResult:
        """Check if copy violates platform rules."""
        pass

    @abstractmethod
    def get_constraints(self) -> FormatConstraints:
        """Return character limits, format rules, etc."""
        pass
```

API routes accept a `platform` parameter and route to the correct adapter:

```python
@app.post("/copy/generate")
async def generate_copy(brief: str, platform: str) -> FormattedCopy:
    # Generate
    variant = await copy_generator_agent(brief)
    # Adapt
    adapter = AdapterRegistry.get(platform)
    formatted = adapter.format_copy(variant)
    # Validate
    result = adapter.validate_copy(formatted.text)
    if not result.valid:
        raise ValueError(result.errors)
    return formatted
```

## Consequences

### ✅ Benefits
- **Platform constraints are enforced, not suggested.** Copy is guaranteed to fit.
- **Easy to add platforms.** New adapter = new platform support automatically.
- **Clear ownership.** Each platform's rules live in one place.
- **Defensive design.** Bad data doesn't slip through to the user.

### ❌ Drawbacks
- **Extra layer.** Every response goes through an adapter.
- **Adapter complexity.** Some platforms have complex rules (nested formatting, conditional constraints).
- **Potential reformatting loss.** Truncating copy to fit may lose nuance.

## Alternatives Considered

1. **No adapters; suggest constraints in the prompt**
   - ❌ LLM can't guarantee it will follow constraints
   - ❌ Users get invalid copy

2. **Validate at the frontend**
   - ❌ Validation logic lives in frontend (hard to share)
   - ❌ API returns invalid data

3. **Let users manually fix copy**
   - ❌ Poor UX
   - ❌ Marketing teams lose time

## Implementation

**Adapters live in `src/adapters/`:**

```
src/adapters/
├── base.py              # BasePlatformAdapter
├── meta.py              # Facebook/Instagram adapter
├── google.py            # Google Ads adapter
├── tiktok.py            # TikTok adapter
├── linkedin.py          # LinkedIn adapter
└── registry.py          # Adapter lookup by platform
```

**Adding a new platform:**

```python
# src/adapters/new_platform.py
from src.adapters.base import BasePlatformAdapter

class NewPlatformAdapter(BasePlatformAdapter):
    def format_copy(self, variant: CopyVariant) -> FormattedCopy:
        # Enforce platform rules here
        pass

    def validate_copy(self, copy: str) -> ValidationResult:
        # Return success/failure
        pass

    def get_constraints(self) -> FormatConstraints:
        # Return platform limits
        pass
```

Then register in `src/adapters/registry.py`:

```python
AdapterRegistry.register("new_platform", NewPlatformAdapter())
```

**Platform constraints live in `src/config/platform_constraints.py`**, not hardcoded in adapters:

```python
CONSTRAINTS = {
    "meta": {
        "headline_max": 40,
        "body_max": 125,
        "allowed_chars": "a-zA-Z0-9 !?.-",
    },
    # ... etc
}
```

## Related Decisions

- [[ADR-002-pydantic-models]] — Adapters receive Pydantic CopyVariant models as input
- [[ADR-001-litellm-abstraction]] — Model choice is separate from adapter logic
