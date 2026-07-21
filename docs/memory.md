# Harness Memory (logic only)

Generated 2026-07-12.

Portable **agent operating system** state for the self-learning harness. Safe to keep personally.
Does **not** contain employer schemas, tickets, customer data, or meeting transcripts.

Load at a new job via `./install.sh` then copy to `$HARNESS_HOME/MEMORY/STATE/memory.md`
(and add to Claude `loadAtStartup` if desired).

---

## 1. What the harness is

A bounded self-improvement loop around coding agents (Claude Code / Grok / pi-compatible):

- Observe failures (ratings, evals, enforcement fires)
- Write lessons and ACE playbook bullets
- Optionally patch skill files only inside `AUTO-LEARNED-GUARDRAILS` markers
- Gate changes with held-out fixtures so edits cannot silently regress

It does **not** retrain model weights. Permission to auto-edit lives in `editable_surfaces.json` **outside** the loop.

## 2. SessionEnd order

```
ratings / FAILURES
  → self_improve → evals / judge / pattern_promotion
  → measure_effectiveness
  → skill_autofix --apply
  → enforcement_promotion / held_out_regression
  → lesson_dedup / lesson_evolve / review_queue
  → held_out_suite --gate
  → agent_rollouts --gate
  → self_harness --apply
  → optional Graphiti autoseed / sync / flush
  → background: ratings_hygiene, meeting_summary_ingest, intent_how_audit
```

## 3. Enforcement detector families

| Detector | Intent |
|---|---|
| unverified_completion | Block "done/fixed" without strong artifact |
| unverified_claims | Block certain system-state claims without tool trace |
| incomplete_analysis | Block confident agree/dismiss without research |
| guardrail_bypass | Block naming a mandatory check then skipping it |
| tool_misuse | Block forbidden tools when policy mandates another |
| duplicate_approval | Block re-approving already-approved PRs |
| silent_completion | Block tool work with no user-visible summary |
| claim_evidence | Optional external verifier on strong claims |
| graphiti_bypassed | Block multi-research without graph memory read |
| graphiti_writeback_skipped | Warn when durable facts not written back |

Configured overrides (mode names only): `{"unverified_completion": "block", "unverified_claims": "block", "incomplete_analysis": "block", "duplicate_approval": "block", "claim_evidence": "block", "silent_completion": "block", "guardrail_bypass": "block", "graphiti_bypassed": "block", "graphiti_writeback_skipped": "warn"}`

## 4. Retrieval SOP

1. Graph memory (if configured)
2. Code / schema / docs tools
3. Meeting summaries (if needed)
4. Broad web search last
5. Write durable decisions back

## 5. Model tiering

| Tier | Use |
|---|---|
| cheap | extract, classify, hygiene, SessionEnd LLM |
| mid | bounded implementation |
| high | architecture / high-blast review only |

## 6. Rating hygiene

- Prefer explicit 1–10 ratings
- Tag every rating with `agent` + `skill`
- Drop graph-sync JSON / system noise from implicit ratings
- skill_autofix cannot learn without skill attribution

## 7. ACE playbook bullets (current, sanitized)

- **unverified_completion** (strategy): Never claim done/fixed/complete without STRONG paper trace: fenced CLI/test output, exit codes, pass counts next to a test runner, or live URL. Bare paths and bare 'N rows/tests' are NOT evidence. If you cannot fence proof, say what is still unverified — do not say done.
- **incomplete_analysis** (strategy): Before concluding or agreeing: read ALL relevant context (full diff, existing PR comments, ticket, related files, CLAUDE.md). Never say looks-unrelated / you're-right / same-issue without a research trace (I read X / gh pr diff / fenced tool output). Research first, respond second.
- **unverified_claims** (strategy): Never assert system state (schema/CI/PR/partition/row counts) without tool output. Tag [GUESS]/unverified when unverified. Never invent metrics, PR numbers, or line refs.
- **duplicate_approval** (pitfall): If reviewDecision is already APPROVED, skip — do not approve again. Saying 'already APPROVED, skipping second approval' is correct. Never claim you approved again / just in case / left a second approval.
- **silent_completion** (strategy): After any tool use, emit at least one user-visible line: what changed and how verified. Silent tool turns hide failures and feed later hallucinations.
- **tool_misuse** (strategy): Check CLAUDE.md tool routing before acting. Wrong tool = wasted work. When unsure which tool to use, re-read the routing rules first.
- **missed_context** (strategy): Before answering: load ticket, full PR diff, existing comments, and CLAUDE.md routing. If any is unread, say so — do not conclude.
- **performance_regression** (pitfall): Do not widen queries, drop partition filters, or full-scan BQ 'for convenience'. Filter-before-limit; dry-run bytes before claiming safe.
- **unhelpful_debugging_response** (strategy): On debug: restate error, show repro command + output, change one variable. Never 'works on my side' without fresh repro in current env.
- **variable_identification_error** (pitfall): Before using a name (table, env, branch, secret): resolve it with a tool (`bq show`, `gh`, file read). Never swap similar identifiers by memory.
- **explicitly_forbidden_behavior** (pitfall): If user said never/don't/forbid X, block X at the planning step. Do not rationalize an exception without re-asking.
- **unverified_claim_accepted** (pitfall): Do not accept bot/CI/self claims at face value. Re-check live git/PR/BQ state before agreeing.
- **retained_learning_doubt** (strategy): Session-start and UserPromptSubmit inject lessons; act as if they bind. Do not re-ask whether lessons persist — apply the active ACE bullets.
- **explicit_forbidden_behavior_repeated** (pitfall): Draft → Show → Ask → Wait → Post. Never post, push, or act on colleague-owned work without explicit approval.
- **scope_misunderstanding** (strategy): Before executing, confirm scope interpretation. When an instruction has multiple plausible meanings, state the interpretation chosen and why. Ambiguity = ask, never silently guess.
- **acting_without_permission** (strategy): Draft → Show → Ask → Wait → Post. Never post, push, or commit without explicit user approval. Applies to all external writes, comments, and colleague-owned work.
- **missed_analysis_edge_case** (strategy): Before claiming complete analysis: list edge cases (empty input, permissions, env mismatch, partial deploy). If untested, mark residual risk.
- **implicit_correction_needed** (strategy): If user must restate the same constraint twice, the first response failed. Surface the constraint explicitly and confirm before continuing.
- **missed_validation_against_precedent** (strategy): Before proposing a new pattern: search repo for existing precedent (similar YAML/SQLX/DAG). Prefer match local style over inventing a third way.
- **failed_documentation_check** (strategy): Before architectural decisions: query docs-mcp/Confluence/local docs. Do not invent process that docs already define.
- **memory_retention_skepticism** (strategy): Scoped comment = change only named location. Global replace only when user says all/everywhere. Confirm before mass-change.
- **variable_confusion** (pitfall): When two similar symbols exist (dev/uat/prd, silver/gold, cow/goat): quote the exact ID from tool output before acting.
- **suboptimal_tool_choice** (strategy): Prefer specialized tools (ticket-cli, bq skill, MCP schema search) over raw shell guesswork. Re-read routing when unsure.
- **implicit_assumption_corrected** (pitfall): When implicit assumption corrected risk appears: stop, gather tool evidence, then act. Do not proceed on memory or assumption alone.
- **performance_worse_than_alternative** (strategy): Preserve partition filters and baseline cost. Measure before claiming a change is safe or faster.
- **inauthentic_tone** (pitfall): Do not over-agree ('you're absolutely right') before checking. Verify, then respond with evidence.
- **missed_documentation_check** (pitfall): Before concluding: explicitly cover documentation check. If unread or untested, say so and fetch/check it — do not skip.
- **wrong_approach** (pitfall): Before committing to an approach, verify it against the source of truth — confirm the exact model, config target, test path, and required workflow the user actually wants. Do not improvise a path when a specific one was implied.
- **root_cause_not_addressed** (pitfall): When root cause not addressed risk appears: stop, gather tool evidence, then act. Do not proceed on memory or assumption alone.
- **acknowledged_uncertainty** (strategy): Uncertainty is fine only when paired with the next verification step. Do not stall on vague doubt — run the check or ask one precise question.
- **capability_doubt** (strategy): Do not claim inability without trying the routed tool. If blocked, report the exact error and next concrete step — not vague capability doubt.
- **excessive_changes** (strategy): Match the requested scope exactly. Do only what was asked; surface extra ideas separately instead of bundling them into the change.
- **insufficient_documentation_check** (pitfall): When insufficient documentation check risk appears: stop, gather tool evidence, then act. Do not proceed on memory or assumption alone.
- **stateless_behavior** (strategy): Re-load prior decisions from MEMORY/PR/ticket before restarting work. Stateless restarts of finished analysis waste the user.
- **uncertain_response_with_workaround** (pitfall): When uncertain response with workaround risk appears: stop, gather tool evidence, then act. Do not proceed on memory or assumption alone.

## 8. Tracked failure pattern ids (names only)

These are harness taxonomy keys, not incident writeups:

- `acknowledged_uncertainty`
- `acting_without_permission`
- `approved_without_verification`
- `blind_retry`
- `capability_doubt`
- `capability_misrepresented`
- `consistency_concern`
- `consistency_doubt`
- `context_retention_doubt`
- `cross_session_learning_doubt`
- `documentation_not_consulted`
- `duplicate_approval`
- `excessive_changes`
- `explicit_forbidden_behavior_repeated`
- `explicit_instruction_violation`
- `explicit_requirement_violated`
- `explicitly_forbidden_behavior`
- `failed_documentation_check`
- `failed_rtfm`
- `failed_to_consult_docs`
- `format_error_in_output`
- `formatting_error`
- `formatting_flaw`
- `hallucinated_capability`
- `ignored_explicit_constraint`
- `implicit_assumption_corrected`
- `implicit_correction_needed`
- `implicit_correction_without_acknowledgment`
- `inauthentic_tone`
- `incomplete_analysis`
- `incomplete_dependency_resolution`
- `incomplete_problem_diagnosis`
- `inconsistent_learning_across_sessions`
- `inconsistent_learning_retention`
- `inconsistent_memory_capability`
- `insufficient_context_to_classify`
- `insufficient_documentation_check`
- `lack_natural_voice`
- `lack_naturalness`
- `lack_of_authenticity`
- `lack_session_persistence`
- `lacking_natural_communication_style`
- `malformed_output_structure`
- `memory_retention_skepticism`
- `missed_analysis_edge_case`
- `missed_clarification`
- `missed_context`
- `missed_documentation_check`
- `missed_uncertainty_acknowledgment`
- `missed_validation_against_precedent`
- `missing_validation`
- `misunderstood_capability`
- `performance_degradation`
- `performance_regression`
- `performance_slower_than_baseline`
- `performance_worse_than_alternative`
- `poor_tone_authenticity`
- `posted_without_approval`
- `pr_review_failure`
- `redundant_recommendation`

## 9. Anti-hallucination floor

- Never claim completion without fenced CLI/test output, exit codes, pass counts, or a live URL.
- Never assert external system state without a tool result in the same turn.
- Prefer [INFERRED]/[GUESS] tags when not verified.
- Interrupted workflows are not complete workflows.

## 10. Deliberately omitted

- Employer warehouse tables, SQL, DAG names
- Ticket IDs, customer/tenant identifiers
- Internal MCP URLs and credentials
- Meeting audio/transcripts
- Full ratings transcripts and effectiveness logs

## 11. New employer checklist

1. `git clone` this repo → `./install.sh`
2. Copy `docs/memory.md` + `docs/principles.md` into STATE / loadAtStartup
3. New Graphiti group (empty)
4. Customize `patterns.yaml` for new cloud tools
5. Collect fresh explicit ratings for 1–2 weeks

See also: `MIGRATE_EMPLOYER.md`, `ARCHITECTURE.md`, `principles.md`.
