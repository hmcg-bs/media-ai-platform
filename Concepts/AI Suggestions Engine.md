# AI Suggestions Engine

**Type:** Concept Note  
**Links:** [[Platform Overview]] · [[Copywriting Fundamentals]] · [[Performance Ads]] · [[Organic Ads]] · [[Audience Targeting]] · [[Copy Testing & Iteration]]

---

## Core Idea

The AI Suggestions Engine is the heart of the Media AI Platform. It takes inputs (brand, audience, goal, format) and outputs copy recommendations — headlines, hooks, body copy, CTAs — tailored to the specific ad context.

---

## Input Variables

| Variable | Description | Example |
|---|---|---|
| Brand Voice | Tone, personality, language style | "Bold, direct, no jargon" |
| Audience | Who the ad is targeting | "Founders aged 28–45, SaaS" |
| Goal | The conversion objective | "Free trial sign-up" |
| Platform | Where the ad will run | "Meta Feed" |
| Format | Ad type | "Single image, 40-char headline" |
| Product/Offer | What's being advertised | "AI scheduling tool, 14-day free" |

---

## Output Types

1. **Headline variants** — Multiple options ranked by predicted performance
2. **Hook suggestions** — Platform-specific scroll-stopping openers
3. **Body copy** — Full ad copy in brand voice
4. **CTA options** — Action phrases matched to the goal
5. **Copy critiques** — Feedback on user-submitted copy with improvement notes

---

## Suggestion Logic (Conceptual)

```
Input → Context Parser → Framework Matcher (AIDA / PAS / Hook-Body-CTA)
      → Voice Calibrator → Platform Adapter → Output Ranker → Suggestions
```

---

## Quality Signals the Engine Should Optimise For

- Clarity (is the value prop immediately obvious?)
- Specificity (numbers, names, outcomes over vague claims)
- Emotional resonance (does it connect to a real pain or desire?)
- Platform fit (does it sound native to where it's being placed?)
- CTA strength (is the next step obvious and low-friction?)

---

## Related Notes
- [[Platform Overview]] — System architecture context
- [[Copy Testing & Iteration]] — How outputs get refined over time
- [[Audience Targeting]] — Inputs that shape suggestions
- [[AIDA Framework]] · [[PAS Framework]] — Frameworks the engine draws on
