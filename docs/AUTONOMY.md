# Autonomous self-improvement loop

Last updated: 2026-07-08

Reference: [Weng, Lilian. “Harness Engineering for Self-Improvement”. Lil’Log (Jul 2026)](https://lilianweng.github.io/posts/2026-07-04-harness/)

## Status

This harness **is** a self-improving agent — with bounded, local mutations.
It does **not** retrain model weights. It mutates lessons, skill guardrails,
taxonomy, enforcement config, and an ACE playbook from failure signals.

### Agent parity (Claude / Grok / pi / Codex)

| Surface | Claude | Grok | pi | Codex |
|---|---|---|---|---|
| Shared MEMORY + ratings.jsonl | yes | yes | yes (`agent:"pi"`) | yes (`agent:"codex"`) |
| SessionEnd loop (`claude-session-end`) | settings hooks | `[compat.claude] hooks` | `claude-bridge.ts` → SessionEnd | **`codex-harness` EXIT** (no native SessionEnd) |
| FailurePatternReminder + ACE every turn | `FailurePatternReminder.hook.ts` | via hooks compat | `pai-learning-harness.ts` | `~/.codex/hooks/pai_user_prompt_submit.py` |
| EnforcementGate (block) | Stop hook | via hooks compat | `pai-enforcement-gate.ts` → follow-up | `pai_stop_enforcement.py` (Stop `decision:block`) |
| skill_autofix | `~/.claude/commands/*.md` | same | + `~/.pi/agent/skills/**` | + `~/.codex/skills/**` |
| Status skill | Grok `/self-improve` | same | pi `/self-improve` | `~/.codex/skills/self-improve` |

Pi extensions (auto-discovered): `~/.pi/agent/extensions/pai-learning-harness.ts`,
`pai-enforcement-gate.ts`, `claude-bridge.ts`.

Pi ↔ Grok Build runtime parity: `~/.pi/agent/PARITY.md` (status) +
`~/.pi/agent/ROADMAP.md` (phased plan: plan mode, subagents, worktrees, arena).

Codex surfaces: `~/.codex/hooks/pai_*.py`, `~/.codex/bin/codex-harness`,
`~/.codex/PARITY.md`. Launch via wrapper for SessionEnd; trust new hooks in `/hooks`.

## Lil'Log → this harness map

| Lil'Log pattern | Our implementation |
|---|---|
| **Workflow automation** (plan→act→observe→improve) | PAI Algorithm + SessionEnd feedback loop |
| **File system as persistent memory** | `MEMORY/*`, `lesson_autogen_*.md`, ratings.jsonl, diagnostics |
| **Sub-agent / backend jobs** | Parallel SessionEnd pipeline (`claude-session-end`) |
| **ACE playbook** (itemized bullets, not prompt blob) | `ace_reflector.py` (distill) → `ace_playbook.py` (curate) → `STATE/ace_playbook.json`; inject via FailurePatternReminder |
| **Self-Harness** mine→propose→validate | `self_harness.py` + existing measure / held_out / skill_autofix |
| **Editable surface outside loop** | `editable_surfaces.json` allow/deny (permission lives outside mutations) |
| **EVOLVE-BLOCK style bounds** | `AUTO-LEARNED-GUARDRAILS` markers in `skill_autofix.py` |
| **Held-in + held-out accept** | `measure_effectiveness.py` + `held_out_regression.py` + **`held_out_suite.py` fixtures** |
| **Preserve passing behaviors** | skill_autofix proposal prompt includes high-rated sessions |
| **Prior failed edits** | skill_autofix includes reverted ledger entries; negative_results.jsonl |
| **Negative results** | `SIGNALS/negative_results.jsonl` (failed/reverted harness edits) |
| **Diversity / near-dupe rejection** | ace_playbook dedupe + self_harness near-dupe scan |
| **Never edit the "OS"** | hooks/, settings.json, review_queue.py, CLAUDE.md, shared repos **deny** |
| **Humans up the stack** | `--reject`, min-age throttle, never auto-post to GitHub/prod |

## Harness upgrades (2026-07-12)

| # | Surface | Path |
|---|---|---|
| 1 | Ratings hygiene + junk skip | `ratings_hygiene.py`, `RatingCapture.hook.ts` |
| 2 | Blast-radius patterns | `PAI/USER/PAISECURITYSYSTEM/patterns.yaml` |
| 3 | Continuous scrum→Graphiti | `scrum_graphiti_ingest.py` (+ SessionEnd) |
| 4 | Model tiering | `MEMORY/STATE/model_tiering.md`, skill/command `model-tiering` |
| 5 | Expanded held-out / rollouts | `held_out_suite/fixtures/*` data_platform + tool_misuse |
| 6 | Intent vs HOW audit | `intent_how_audit.py` |
| 7 | Retrieval SOP | `MEMORY/STATE/retrieval_sop.md` + graph_preflight + FailurePatternReminder |

## Harness upgrades (2026-07-16) — integrity + anti-recidivism

| # | Bug / gap | Fix |
|---|---|---|
| 1 | `promote_to_taxonomy` substring-matched `PR_PATTERN_KEYWORDS` first → pollution (`"other": ["other"]` ×N) | Line-anchored `^PATTERN_KEYWORDS` + idempotent skip if pattern exists |
| 2 | Auto-drain re-promoted denylist labels (`other`, `not_a_failure`, …) | `PROMOTE_DENYLIST` + ledger `rejected_denylist` + refuse on approve |
| 3 | `lesson_dedup` `already_queued` never matched (`survivor` vs `survivor<-loser`) → 37× re-approve | Key by full pattern + include approved + skip missing files |
| 4 | Merge approve thrash when loser already deleted | Idempotent merge path + audit `lesson-merged-idempotent` |
| 5 | Healthcheck blind to taxonomy pollution / review thrash | `taxonomy_integrity` + `review_recidivism` checks |

## Harness upgrades (2026-07-17) — skill attribution + lesson collapse

| # | Bug / gap | Fix |
|---|---|---|
| 1 | 99% ratings skill=`general-session` → skill_autofix never qualifies real skills | `ratings_hygiene.py --reattribute` backfills from summary/preview text; RatingCapture text+path harvest at write |
| 2 | 99/114 lessons were template stubs; name Jaccard used STOPWORDS that strip `check` → 0 merge candidates | `name_tokens()` + template bar = threshold; `SEMANTIC_CLUSTERS`; `--apply-now` one-shot drain |
| 3 | 114 near-dupe autogen lessons | Drained **114 → 41** (73 merges); backups under `STATE/lesson_dedup_backups/` |

## Loop (every SessionEnd)

Wired in `~/.claude/hooks/claude-session-end` (Claude + Grok via `[compat.claude] hooks`):

```
ratings / FAILURES
    → self_improve.py          # pattern → lesson_autogen_*.md
    → evals.py / judge_outcomes.py
    → pattern_promotion.py     # new taxonomy candidates
    → measure_effectiveness.py # held-in verdicts
    → skill_autofix.py --apply # bounded skill guardrails + measure/revert
    → enforcement_promotion.py
    → held_out_regression.py   # held-out safety (Self-Harness)
    → lesson_dedup.py
    → lesson_evolve.py
    → review_queue.py --auto-drain
    → held_out_suite.py --gate     # fixture D_in + D_out (deterministic)
    → agent_rollouts.py --gate     # LLM PR-review rollouts under ACE playbook
    → self_harness.py --apply --skip-rollouts
    → consolidate_memory.py
    → agy-lessons-snapshot.md
```

### Metric gates

```bash
# Deterministic fixtures (26 D_in + 12 D_out)
python3 ~/.claude/MEMORY/LEARNING/held_out_suite.py --gate

# Agent-executed PR-review rollouts (LLM + ACE playbook + rubrics)
python3 ~/.claude/MEMORY/LEARNING/agent_rollouts.py --gate
python3 ~/.claude/MEMORY/LEARNING/agent_rollouts.py --update-baseline

# Combined Self-Harness gate
python3 ~/.claude/MEMORY/LEARNING/self_harness.py --gate

# skill_autofix HARD-BLOCKS new applies when either gate is red
python3 ~/.claude/MEMORY/LEARNING/skill_autofix.py --apply
# reverts still run; only NEW guardrail applies are blocked
```

| Gate | Signal | Blocks skill_autofix new applies? |
|---|---|---|
| `held_out_suite.py` | static good/bad responses | **yes** (always re-checked) |
| `agent_rollouts.py` | live model under ACE playbook | **yes** (via last run, ≥75% floor) |
| `held_out_regression.py` | live ratings traffic | queues review only |

- Fixtures are **human-only** — anti reward-hacking
- Agent rollout transcripts: `MEMORY/LEARNING/DIAGNOSTICS/agent_rollout_transcripts/`

Live readback during sessions:
- `FailurePatternReminder.hook.ts` — ACE playbook bullets + anti-hallucination floor (UserPromptSubmit)
- `EpistemicRules.hook.ts` — tag/confidence rules every turn (UserPromptSubmit)
- `EnforcementGate.hook.ts` (Stop) — ALWAYS_ON: unverified_completion, unverified_claims (block), guardrail_bypass; plus claim_evidence (block)
- `LoadContext.hook.ts` (SessionStart) + `graph_preflight.md`

Anti-hallucination stack (2026-07-09 → 09b):
| Layer | Mechanism | Mode |
|---|---|---|
| SessionStart brief | `STATE/anti_hallucination.md` + LoadContext | inject |
| Epistemic inject | tag every claim; system-state needs tools | every turn |
| Recent enforcement log | last block patterns re-injected | every turn |
| ACE seed bullets | completion / claims / silent_completion always kept | playbook |
| Strong completion | fence/CLI/URL only — paths AND bare N-rows fail | block |
| Hedge / confident-state / metrics | regex + tags | block (`unverified_claims`) |
| silent_completion | tools used + empty user text | block |
| claim_evidence | tool grounding + number orphan fast path | block |
| adversarial_claim_detector | LLM false-certainty (tech responses only) | under unverified_claims |
| graphiti preflight | research without graph | block |

## Autonomy tiers

| Tier | What auto-applies | Blast radius |
|---|---|---|
| L0 memory | Session learnings, ratings, diagnostics | local MEMORY/* only |
| L1 ACE playbook | Reflector (quality≥2) + Curator (id, description, section, counters) | STATE/ace_playbook.* |
| L2 lessons | `lesson_autogen_*.md` | personal memory dir |
| L3 skill autofix | marker-bounded section on `commands/*.md` + `~/.pi/agent/skills/**` | local; git snapshot + auto-revert |
| L4 review auto-drain | dedup/evolve/taxonomy/base | local harness files only |
| L5 enforcement | warn → block in EnforcementGate | agent Stop behavior only |
| **Never** | PR posts, prod, shared-repo PRs, model weights, hooks/settings | — |

## Graph memory (Graphiti + Bungraph)

| Store | Role | Path / group |
|---|---|---|
| **graphiti-memory** | Durable work + architecture (Neo4j) | group_id=`main` |
| **bungraph** | Local hybrid search + harness loopback | **`~/.bungraph.db` only** (CLI must pass `--db`; env alone ignored) |


**Canonical bungraph path (2026-07-17):** only `~/.bungraph.db`. CLI ignores `BUNGRAPH_DB_PATH` (defaults to `./bungraph.db`). cwd-scattered DBs were consolidated + quarantined under `STATE/bungraph_quarantine_*` (~11GB LEARNING bloat + session notes).

SessionEnd runs `session_graphiti_autoseed.py` (durable transcript → pending),
then `sync_graph_memory.py` (which also calls `flush_graphiti_pending.py`) after
measure/ACE so regressed lessons and harness status:
  - land in bungraph (local hybrid)
  - queue to `STATE/graphiti_pending_episodes.jsonl`
  - **flush into live Graphiti Neo4j** via MCP streamable HTTP (`:8000/mcp`)
  - refresh `STATE/graph_preflight.md`

Flushed episodes archive to `STATE/graphiti_flushed_archive.jsonl`. Failures stay
pending for the next SessionEnd (non-fatal).

SessionStart injects graph preflight via LoadContext (Claude/Grok) and
pai-learning-harness (pi). FailurePatternReminder also injects graph preflight
every UserPromptSubmit. Agents must search graphiti/bungraph **before** broad
research — `graphiti_bypassed` is **block** when ≥2 research tools fire without
a graphiti-memory or bungraph call.

## Harness upgrades (2026-08-13) — freshness, burn-in stall, chronic recidivism

| # | Bug / gap | Fix |
|---|---|---|
| 1 | Capture sensor flatline silent in healthcheck | `harness_healthcheck.py` checks `rating_age_days > 5` (error) / `> 2` (warn) |
| 2 | Active skill_autofix edits sat 28d with `post_n=0` | `skill_burnin.py --resolve-stall --apply` parks unmeasurable edits; auto-reactivates on new traffic |
| 3 | Ladder-top patterns queued human review indefinitely | `chronic_failures.py` detects regressed+blocked patterns (audit >= 5) and emits intervention-class rotation report |

## Operator controls

```bash
# Dashboard
python3 ~/.claude/MEMORY/LEARNING/harness_healthcheck.py
python3 ~/.claude/MEMORY/LEARNING/self_harness.py
python3 ~/.claude/MEMORY/LEARNING/ace_reflector.py --self-test
python3 ~/.claude/MEMORY/LEARNING/ace_reflector.py --dry-run
python3 ~/.claude/MEMORY/LEARNING/ace_playbook.py --dry-run
python3 ~/.claude/MEMORY/LEARNING/ace_playbook.py   # rebuild STATE/ace_playbook.*
python3 ~/.claude/MEMORY/LEARNING/review_queue.py --stats
python3 ~/.claude/MEMORY/LEARNING/skill_autofix.py --status
python3 ~/.claude/MEMORY/LEARNING/sync_graph_memory.py --dry-run
python3 ~/.claude/MEMORY/LEARNING/flush_graphiti_pending.py --dry-run

# Manual cycle
bash ~/.claude/hooks/claude-session-end

# Throttle auto-drain
export SELF_IMPROVE_AUTO_DRAIN_MIN_AGE=3

# Kill a bad lesson/promotion
python3 ~/.claude/MEMORY/LEARNING/review_queue.py --reject <pattern> --source base --reason "noise"
```

## Success definition (Weng-aligned)

Autonomous harness self-improvement = closed loop on **machinery**, not weights:

1. Failure signal recorded (rating ≤4 or FAILURES dump)
2. Weakness mined into patterns (Self-Harness stage 1)
3. Bounded proposal on allowlisted surface only (stage 2)
4. Held-in + held-out validation; negative results archived (stage 3)
5. ACE playbook curated as itemized bullets for next-session injection
6. Flat/regressed → evolve or escalate; skill concentration → autofix with revert
7. Human only for policy conflicts and noise rejection


## Grok accuracy package (2026-07-17)

| # | Change |
|---|---|
| 1 | `unverified_claims` + `claim_evidence` → **block** |
| 2 | `tool_misuse` ACE+lesson reinforced; escalate still active until re-measure |
| 3 | Guardrails SSOT `~/.claude/commands` (Grok via compat); optional pi/agents mirror |
| 4 | FailurePatternReminder: hard floor 4 patterns + domain playbook inject |
| 5–7 | held_out fixtures expanded (tool_misuse, claims, incomplete_analysis) |
| 8 | RESEARCH_TOOL_RE expanded for Grok tools; graph_preflight refreshed |
| 9 | Background LLM probe (DeepSeek primary / Gemini fallback) |
| 10 | `agent_effectiveness.py` per-agent report |
| 11 | review_queue compact (last approval per pattern+source) |
| 12 | `skill_autofix_burnin.md` measure-after checklist |


## Phases A–D further improvement (2026-07-17)

See `DIAGNOSTICS/phases_ABCD_2026-07-17.md` and `self_harness_status.py`.


## Phases E–I (2026-07-17)

See `DIAGNOSTICS/phases_EFGHI_2026-07-17.md`.


## Phases J–O (2026-07-17)

See `DIAGNOSTICS/phases_JKLMNO_2026-07-17.md`.
