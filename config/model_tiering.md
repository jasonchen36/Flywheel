# Model tiering policy (portable harness)

Default: **route down**. Escalate only when risk or ambiguity requires it.

## Tiers

| Tier | Use for | Examples (catalog may rename) |
|---|---|---|
| **cheap** | extract, summarize, classify, hygiene, sentiment, reclass | Gemini Flash / Flash-Lite, Haiku-class |
| **mid** | bounded implementation, single-file fixes, dry-runs | Sonnet-class |
| **high** | architecture, multi-repo design, high-blast review, ambiguous incidents | Opus / top Sonnet |

## Phase routing (AIDD/SDD)

1. **Intake / transcript extract / scrum digest** → cheap
2. **Ticket parse / checklist fill / search** → cheap
3. **Code edit in one repo, clear acceptance** → mid
4. **Design, infra-before-app multi-repo, cascade, security** → high
5. **Background self-improve SessionEnd** → cheap only (Vertex Gemini; never Opus)

## Rules

- Do not default every JIRA ticket to high tier end-to-end.
- Prefer tools + verification over a bigger model when the task is mechanical.
- Cost is a first-class metric: if mid fails once, escalate once with a tighter prompt — do not thrash Opus loops.
- Background harness LLM: `PAI_BACKGROUND_LLM_PROVIDER=gemini` (enforced when `PAI_CLAUDE_HEADLESS_DISABLED=1`).

## Agent defaults on this machine

- Interactive coding (Claude/Grok/pi): user-selected; prefer mid unless task is high-blast.
- SessionEnd / RatingCapture / claim detectors: cheap (Gemini).
- agent_rollouts evals: may use mid for quality signal (controlled, gated).
