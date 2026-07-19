# Platform Overview

**Type:** Platform Note  
**Links:** [[AI Suggestions Engine]] · [[Ad Format Library]] · [[Copy Testing & Iteration]] · [[Audience Targeting]] · [[Performance Ads]] · [[Organic Ads]]

---

## Core Idea

The Media AI Platform helps users write better ad copy — faster. It combines AI-powered suggestions with proven copywriting frameworks to produce performance and organic ad copy tailored to the user's brand, audience, and platform.

---

## Problem It Solves

- Writing ad copy is time-consuming and often inconsistent
- Most marketers don't have deep copywriting expertise
- Testing copy variants manually is slow
- Copy often isn't adapted properly for different platforms

---

## Core User Flow

```
1. User inputs context (brand, audience, goal, platform, format)
        ↓
2. AI Suggestions Engine generates copy variants
        ↓
3. User reviews, selects, and edits suggestions
        ↓
4. Copy is exported or pushed to ad platform
        ↓
5. Performance data feeds back to improve future suggestions
```

---

## Key Features (Planned)

| Feature | Description | Status |
|---|---|---|
| Copy Generator | Headline, hook, body, CTA suggestions | Planned |
| Framework Selector | Choose AIDA, PAS, or custom | Planned |
| Platform Adapter | Reformats copy per platform rules | Planned |
| Copy Critic | AI feedback on user-submitted copy | Planned |
| Variant Library | Save and organise top-performing copy | Planned |
| Brand Voice Settings | Lock tone and language style | Planned |

---

## Tech Considerations

- LLM backbone for generation ([[AI Suggestions Engine]])
- Brand voice fine-tuning or prompt engineering
- Integration with ad platforms (Meta Ads Manager, Google Ads)
- Copy performance data ingestion for feedback loop

---

## Related Notes
- [[AI Suggestions Engine]] — The generation core
- [[Ad Format Library]] — Supported formats and constraints
- [[Copy Testing & Iteration]] — The improvement loop
- [[Competitor Analysis]] — What similar platforms do
