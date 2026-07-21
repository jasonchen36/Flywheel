---
name: evidence-check
description: "use before posting blockers, regression claims, contract claims, or stale-state claims; build and verify the minimum evidence packet with the local evidence-check helper. Use when this workflow is needed."
---
# Evidence Check Skill

Use this skill when a claim feels important enough that getting it wrong would create noise or false confidence.

Best use cases:
- before posting a blocker on a PR
- before saying "this PR introduced the regression"
- before making a contract/nullability/default-compatibility claim
- before trusting stale bot, CI, or task-state surfaces

It wraps:
- `~/.claude/tools/evidence-check.sh`

## Claim Types

- `state`: existence or current-state claim
- `causality`: regression or attribution claim
- `contract`: compatibility/nullability/default/code-domain claim
- `severity`: "must fix" or blocker claim
- `stale-state`: bot/CI/UI/task-state claim that requires live-source verification

## Workflow

### 1. Pick the Claim Type

If you cannot name the claim type, stop and clarify the claim first.

### 2. Generate the Checklist

```bash
bash ~/.claude/tools/evidence-check.sh checklist --claim-type causality
```

Use the checklist to gather the minimum evidence packet before asserting the claim.

### 3. Verify the Evidence Packet

Examples:

```bash
bash ~/.claude/tools/evidence-check.sh verify \
  --claim-type causality \
  --artifact "README.md:42" \
  --verify-command "git diff origin/master...HEAD -- README.md" \
  --result "head differs from base at README.md:42" \
  --baseline "origin/master has the old wording"

bash ~/.claude/tools/evidence-check.sh verify \
  --claim-type stale-state \
  --artifact "PR #507 bot comment 4008450224" \
  --live-source "gh pr view 507 --json headRefOid,commits,checks" \
  --verify-command "git rev-parse origin/branch && gh pr view 507 --json headRefOid" \
  --result "live head differs from bot-reviewed head"
```

If the helper says `BLOCKED`, do not escalate the claim yet.

### 4. Use the Packet in the Real Workflow

- for PR reviews: pair with `/review`
- for SQL/contract claims: pair with `/review-guard` and actual `bq` verification
- for regressions/baseline claims: pair with base-branch checks
- for status surfaces: verify the live source first

## Guardrails

- This helper validates completeness of the evidence packet, not the truth of the claim by itself.
- Do not treat a filled-out packet as a substitute for real command/query execution.
- For blocker-severity claims, baseline evidence is mandatory.
- For stale-state claims, live-source evidence is mandatory.

## Integration

- Use before posting blockers or regression findings.
- Use after running the actual command/query checks, not before.
- Pair with the anti-hallucination rules in `CLAUDE.md` and `HALLUCINATION_PREVENTION_GUIDE.md`.
