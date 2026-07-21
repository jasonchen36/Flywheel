#!/usr/bin/env bun
/**
 * EpistemicRules.hook.ts — Inject epistemic tagging rules every turn.
 *
 * Prevents context rot: rules loaded at session start get buried by compression.
 * Fires on every UserPromptSubmit with a compact reminder (~80 tokens).
 *
 * TRIGGER: UserPromptSubmit
 * OUTPUT: hookSpecificOutput.additionalContext
 */

const REMINDER = `EPISTEMIC RULES (active — anti-hallucination):
TAG every factual claim: [KNOWN]·[COMPUTED]·[INFERRED]·[COMMON]·[FRAME]·[GUESS]. Untagged state/code/data claims = violate.
CONFIDENCE: HIGH≥80% MED50–80% LOW20–50% VLOW<20% UNKNOWN. [FRAME]/[GUESS] cap LOW.
DON'T KNOW = first line. No fabricated citations/numbers/paths/PR status.
SYSTEM STATE (schema/CI/PR/row counts/deploy): tool first, then claim. No tool → say unverified.
COMPLETION (done/fixed/complete): STRONG paper trace only (fenced CLI/test output, pass counts, exit codes, live URL). Bare paths FAIL.
HEDGES (probably/I think/should be): verify or tag [GUESS]/unverified.
Append [RULES I BROKE] if violated.`;

// Drain stdin (required by Claude Code hooks protocol)
try {
  const reader = Bun.stdin.stream().getReader();
  const timeout = new Promise<void>(r => setTimeout(r, 300));
  await Promise.race([
    (async () => { while (!(await reader.read()).done) {} })(),
    timeout,
  ]);
} catch {}

process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: 'UserPromptSubmit',
    additionalContext: REMINDER,
  }
}));

process.exit(0);
