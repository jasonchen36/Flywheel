---
name: small-model-router
description: "Routes tasks to appropriate small models based on complexity. Use when deciding whether Flash-Lite or a larger model should handle a task."
---

# /small-model-router
---
description: Route a task for a weaker model such as Haiku or Gemini Flash-Lite; decide whether the task is safe, bounded, or requires a stronger model before any deeper work begins
---



# Small Model Router Skill

Use this before asking a weaker model to do meaningful work.

Load:
- `${HOME}/SMALL_MODEL_ROUTING.md`

## Workflow

### 1. Classify the task

Choose one:
- `small-safe`
- `small-bounded`
- `strong-model-required`

### 2. Route accordingly

- `small-safe` -> run the specific helper skill immediately
- `small-bounded` -> run the helper skill first and stop if ambiguity remains
- `strong-model-required` -> say so explicitly and recommend the stronger workflow

### 3. Default escalation cases

Escalate instead of improvising for:
- PR review
- incident triage
- promotions/deployments
- architecture
- schema-migration planning
- broad debugging across multiple systems

## Output format

Use:

```text
SMALL MODEL ROUTE

Class:
- <small-safe | small-bounded | strong-model-required>

Helper:
- <skill or workflow>

Why:
- <one short reason>

Next Step:
- <exact next action>
```
