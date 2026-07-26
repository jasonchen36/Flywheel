# Hooks

| Hook | Event | Role |
|---|---|---|
| `RatingCapture.hook.ts` | UserPromptSubmit | ratings.jsonl + skill/agent tags |
| `FailurePatternReminder.hook.ts` | UserPromptSubmit | ACE bullets + graph preflight |
| `VerificationReminder.hook.ts` | PostToolUse Write/Edit | Enforce live test verification before completion |
| `PRDescriptionReminder.hook.ts` | PostToolUse Bash (git push) | Remind agent to update GitHub PR body after push |
| `EnforcementGate.hook.ts` | Stop | block/warn completion & claim failures |
| `SecurityValidator.hook.ts` | PreToolUse Bash/Edit/Write | blast-radius patterns |
| `EpistemicRules.hook.ts` | UserPromptSubmit | confidence tags |
| `LastResponseCache.hook.ts` | (pair with rating) | last assistant text for ratings |

## Pi

| Extension | Role |
|---|---|
| `pai-learning-harness.ts` | ACE + ratings + skill attribution |
| `pai-enforcement-gate.ts` | agent_end follow-up on block |

Pi SessionEnd: run the same `harness-session-end.sh` via your Claude-compat bridge or a pi shutdown hook.
