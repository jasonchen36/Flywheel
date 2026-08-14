# Portable Engineering Principles

Generated 2026-07-12 from a personal errors-and-lessons archive.

Employer-agnostic agent/operator hygiene. Company product names, ticket IDs, and internal
paths removed or generalized. Full incident narratives omitted.

Pair with `memory.md` (harness logic) in this repo.

---

## Standing rules

1. **Evidence before claims** — schema/CI/counts/deploy/"done" need tool output (fenced CLI, exit code, live URL). Bare paths are not proof.
2. **Dry-run SQL before judging it** — never claim syntax OK/fail from reading alone when a dry-run exists.
3. **Never post without approval** — draft reviews/comments; ask before submit.
4. **Scoped comment → scoped change** — one-line review feedback is not global rename.
5. **Confirm high-blast writes** — prod mutations, force-push, deletes, external posts.
6. **Re-fetch live PR/CI head** before approve/merge/comment.
7. **Graph/memory before multi-tool thrash** when a memory graph is available.
8. **Default model tier down**; escalate only for hard design/high-blast work.
9. **Do not approve on red CI** or missing status checks.
10. **Interrupted review is incomplete** — restart, do not ship half a pass.
11. **3-Category Hallucination Architecture** — Checkable reality requires automated harness walls (type checkers, linters, tests, bq dry-runs, symbol verifier); uncheckable tribal reality requires grounding + flagging for human review; business judgment requires human accountability.
12. **Mandatory 5-Source Pre-Flight Grounding** — BEFORE starting any task or writing code/docs, ALWAYS verify grounded truth across all applicable sources: JIRA (specs), AKB (architecture graph), Confluence (requirements), RTFM (design specs/docs), and GitHub repos (source code).
13. **Mandatory Immediate Memory Persistence on Review Feedback** — Whenever review feedback, PR comments, human corrections, or verification errors occur, fixing local code/text is only half the job. You MUST IMMEDIATELY persist the feedback, root cause, and prevention rule to Graphiti/memory and patch relevant skills so the mistake NEVER recurs.
14. **Source Symbol Grounding** — Before citing class names, method signatures, or package names in design docs or PRs, run `rtk verify-doc-symbols.sh <file>` or `rtk rg <symbol>` against target repos. Never guess or invent class/API signatures (e.g. `WarehouseDatasetUtility` vs `WarehouseDatasetFactory`).
15. **Parse-Time Dev Fallback Prohibition** — NEVER put hardcoded parse-time dev fallbacks (`us-central1`, `achievers-dev`) in production DAG code. Non-dev environments (`stg`, `uat`, `prd`) MUST raise explicit `ValueError` if required configuration/enums are missing.
16. **Data Freshness Completeness Guards** — In metrics/observability queries, ALWAYS enforce completeness guards (`CASE WHEN COUNT(domain_max) = {num_domains} THEN MIN(domain_max) ELSE NULL END`) and deduplicate domain input lists (`sorted(set(domains))`).
17. **Unit Test Forwarding Preservation** — When refactoring or delegating methods in shared utilities, NEVER delete unit tests asserting argument passthrough without adding equivalent `assert_called_once_with(...)` tests.
18. **GitHub Stacked PRs (`gh-stack`) Awareness** — Use `gh stack init`, `add`, `view`, `push`, `submit`, `sync` for dependent PR chains. Run unit tests and symbol verification on every layer in the stack.
19. **Precondition Enumeration over Generic Deliberation** — Structure beats raw information by 2.83x. BEFORE executing tasks or proposing actions, explicitly enumerate all physical and logical preconditions that MUST be true for the goal to succeed.
20. **Deterministic Controls Over Probabilistic Guardrails** — Never rely solely on prose instructions for safety or blast radius limits. Mutating/destructive operations MUST be wrapped in deterministic tool wrappers, code checks, and permission gates that hold regardless of LLM attention weights.
21. **Independent Observer Verification** — Self-audit in the same generating model shares the same blind spots. Verification loops and Stop hooks MUST use independent, specialized tools (linters, AST checks, schema validators, regex detectors) to audit outputs before commitment.
22. **Flywheel Brake & Artifact Lifecycle** — Classify learned artifacts as `compensation` (provisional model workaround), `boundary` (permanent safety/governance limit), or `context` (durable domain facts). Revalidate compensation rules upon model upgrades; retire unearned rules via counterfactual NullMemory testing.
23. **$pass^k$ Reliability Benchmark Metric** — Evaluate multi-step agent workflows using $pass^k$ (all $k$ attempts succeed) rather than $pass@k$ (at least 1 succeeds) to expose run-to-run stochasticity.

---

## Distilled failures (195 portable entries)

### AI Service Operations / MCP Timeout Handling

- **P060** Treating 60s MCP Tool Deadlines as review-platform Review Outcomes (2026-02-22)
  - _Session analysis for 2026-02-15 through 2026-02-22 found **27 occurrences** of: - `timed out awaiting tools/call after 60s`_

### AI Service Operations / Model Rollout

- **P054** Enabling Latest Reviewer Model Without Entitlement Verification (2026-02-20)
  - _`bot review` failed with `litellm.NotFoundError` / Vertex 404 for `publishers/anthropic/models/claude-sonnet-4-6@default`. Latest model track was selected via opt-in label/flag, but runtime entitlement/access was not verified first._

### Airflow / Jinja Templating / Generated DAGs / STG Validation

- **P093** Jinja-Escaping Fixes for Generated DAGs Must Be Valid in Both the Generator Template and Airflow Runtime (2026-03-12)
  - _**Category:** Airflow / Jinja Templating / Generated DAGs / STG Validation_

### Airflow / STG Validation / DAG Discovery / Promotion Testing

- **P187** Deleted DAG Files Can Remain as Active Airflow Metadata (2026-05-01)
  - _**Category:** Airflow / STG Validation / DAG Discovery / Promotion Testing **Date:** 2026-05-01 **Ticket:** TICKET-N **PRs:** `pipelines-data-architecture` PR #692_

### Architecture / Cascade Delete / Evidence

- **P131** key_mirror.is_bronze_layer_synthetic_delete_added Is the Authoritative Full-Chain Signal (2026-03-17)
  - _**Category:** Architecture / Cascade Delete / Evidence_

### Architecture / cdc-service / data-infra-repo / PR Review

- **P188** cdc-service Backfill strategy and Guard Comments (Locked vs Open)
  - _**Category:** Architecture / cdc-service / data-infra-repo / PR Review **Date:** 2026-05-02 **PRs:** `data-infra-repo` PR #5704_

### Architecture / Pub/Sub / Testing Pattern

- **P129** Pub/Sub Fan-Out Enables Parallel Observation Without Interference (2026-03-17)
  - _**Category:** Architecture / Pub/Sub / Testing Pattern_

### Architecture / streaming-pipeline / Cache Key Design

- **P150** Assuming File-Path-Scoped Schema Caching Was Good Enough for cdc-service Workloads (2026-04-08)
  - _**Category:** Architecture / streaming-pipeline / Cache Key Design_

### Architecture / streaming-pipeline / Evidence Collection

- **P126** streaming-pipeline Writes to Bronze GCS, Not Bronze Native BQ — Waiting for BQ Evidence When GCS Is the Source (2026-03-17)
  - _**Category:** Architecture / streaming-pipeline / Evidence Collection_

### Claims Accuracy

- **P224** False Characterization of Pricing / Cost Tracking Availability
  - _- Check if the table is newly added or part of a recent feature (context from parent tickets). - Use the `❓ q:` or `🟡 risk:` tag for "breaking" changes in DEV to solicit the author's plan first. - Re-read **Error 132** (don't flag INFORMATION_SCHEMA columns as blockers) - applies the same "don't be too rigid with infra PRs" principle._

### Code Style / Communication / Repository Hygiene

- **P151** Reintroducing Emoji Characters in Code or Logs While Applying Functional Fixes (2026-04-08)
  - _**Category:** Code Style / Communication / Repository Hygiene_

### Codex Workflow / Tool Orchestration / Session Efficiency

- **P170** Recent Codex Sessions Overused Serial Read Steps and PTY Polling for Read-Only Work (2026-04-17)
  - _**Category:** Codex Workflow / Tool Orchestration / Session Efficiency_

### Communication / Chat Clutter

- **P158** Unnecessarily Displaying File Contents in Chat (2026-04-11)
  - _**What Happened:** I printed the contents of files (or large blocks of text from files) in my responses after reading or editing them._

### Communication / Scope Control / Approval Discipline

- **P091** Treating a Request to "Draft" a Diagram as Approval to Implement and Push It (2026-03-11)
  - _**Category:** Communication / Scope Control / Approval Discipline_

### DAG Template

- **P018** Wrong Domain — Reporting Domain Used, MV in communications_gold
  - _**Date:** 2026-02-06 **Category:** DAG Template, Domain Configuration_

### Data Architecture / PII Obfuscation / Investigation

- **P192** Conflating Bronze (BigLake/GCS) with Bronze Native when Asserting Obfuscation Scope
  - _**Category:** Data Architecture / PII Obfuscation / Investigation **Date:** 2026-05-04_

### Data Infrastructure / data-infra-repo / BigLake External Tables / Terraform Apply Failure

- **P242** Including Partition Columns `client_pk` and `file_timestamp` in BigLake External Table Schema (Recurrence) (2026-06-04)
  - _**Date:** 2026-06-04 **Category:** Data Infrastructure / data-infra-repo / BigLake External Tables / Terraform Apply Failure_

### Data Investigation / BigQuery / Context Awareness

- **P123** Slow BigQuery JSON Querying and Failure to Connect Context with Recently Reviewed PRs (2026-03-17)
  - _**Category:** Data Investigation / BigQuery / Context Awareness_

### Data Pipeline

- **P172** Atomic Transaction Timestamp Ordering in MySQL Binlog
  - _- Never rely solely on source timestamp for ordering - Always use log file and log position for CDC events - Implement split load for all SCD1/SCD2 tables_
- **P175** Validation False Positives Due to Timing Differences
  - _- Don't validate values when atomic transaction issues exist - Use time windows instead of exact interval matching - Account for natural timing differences between SCD1 and SCD2 - Monitor validation failure patterns to identify systemic issues_
- **P177** Schema Change Ordering - MySQL Before Warehouse Causes Manual Recovery
  - _**Category:** Data Pipeline, Schema Management, CDC **Date:** 2026-04-15 **Source:** Scrum Recording (2026-04-15)_
- **P178** Migration Validation Gap - Green DAGs Masked Data Loss
  - _**Category:** Data Pipeline, Bronze Layer, Validation **Date:** 2026-04-15 **Source:** Scrum Recording (2026-04-15)_

### Data Pipeline / Validation Logic

- **P034** NULL Handling Bug in Validation Logic Causing Silent First-Run Failures (2026-02-10)
  - _Discovered a critical bug in `GoldLayerToDatalakeConfig.get_data_interval_start_and_end_timestamp()` that affects all 59 many-to-one gold-layer-to-datalake DAGs when running for the first time (empty metrics tables)._

### Data Pipeline / Validation Logic / transform-layer Integration

- **P241** Assuming Gold Validation is Caught Up Because streaming-pipeline Processor Caught Up (2026-06-02)
  - _**Date:** 2026-06-02 **Category:** Data Pipeline / Validation Logic / transform-layer Integration_

### Data Safety / Multi-Region Verification

- **P051** Accepting Regional Data Assumptions Without Metadata Verification (2026-02-19)
  - _A regional-risk argument ("non-NA regions have no data/pipeline") was nearly accepted without hard metadata validation, despite evidence of non-zero rows in non-NA UAT tables._

### Data Safety / Warehouse Deletes / CDC Semantics

- **P215** Repairing Missing Warehouse Deletes with Hard Deletes (2026-05-09)
  - _**Category:** Data Safety / Warehouse Deletes / CDC Semantics_

### Database / data_change/shards / SQL Safety

- **P171** Missing NOT NULL Column in Shard Data Change Script — Runtime Failure After Pipeline Submission (2026-04-17)
  - _**Category:** Database / data_change/shards / SQL Safety_

### Database / SQL / Schema Verification

- **P166** Wrong MySQL Column Names in news_feed_event INSERT (2026-04-15)
  - _**Category:** Database / SQL / Schema Verification_

### Dataflow / Deployment Operations / Capacity Triage

- **P141** na-ne1 UAT Dataflow `n1-standard-1` Launcher Failures Can Be GCP Capacity, Not Our Config (2026-03-19)
  - _**Category:** Dataflow / Deployment Operations / Capacity Triage_

### Deployment

- **P011** Multi-Env Changes in Single PR Violating Environment-per-PR Pattern
  - _**Date:** 2026-02-05 **Category:** Deployment, data-infra-repo_

### Deployment / Data Pipeline Validation

- **P035** Scrum-Derived Validation Gaps Before Production Promotion (2026-02-12)
  - _During scrum follow-up for active data-platform work, two production-facing issues were highlighted that were not detected during earlier validation:_

### Deployment Workflow

- **P014** Created prd-promotion Branch and PRd Directly to PRD
  - _**Date:** 2026-02-05 **Category:** Deployment Workflow_
- **P015** Bug Fix PR Targeting UAT Branch Directly Instead of Master
  - _**Date:** 2026-02-05 **Category:** Deployment Workflow, Git_

### Deployment Workflow / Airflow Promotion / Shared Package Coordination

- **P218** Promoting Shared Airflow Changes Without Cross-Change Coordination (2026-05-09)
  - _- When delivering infrastructure or CI/CD pipeline changes, never rely entirely on mocked unit tests. - Always include an integration/functional test script that hits real infrastructure (e.g., dry-runs or isolated test prefixes) to prove the end-to-end flow works._

### Deployment Workflow / Promotion Hygiene

- **P064** Opened Promotion PR in Wrong Repo and Wrong Branch Flow (2026-02-25)
  - _While promoting `TICKET-N` for `dim_user_reports_to_flat`, I created a PR in the wrong repository and used the wrong promotion path first: - Opened PR in `example-org/pipelines-master` (`#68`) instead of the domain repo `example-org/pipelines-members`. - Created a direct branch-to-`prd` PR in members repo (`#278`) instead of _

### Deployment Workflow / Runtime State Verification

- **P067** Treating `airflow-image` PR Merge as PRD Deployment Completion (2026-02-27)
  - _A recent `airflow-image` promotion gap showed that a merged PR in the source repo is not enough to conclude PRD is actually on the new image. In this case: - `airflow-image` PR `#698` was merged to `prd` - but the downstream `data-infra-deployments` PRD tag update PR `#30431` was still open - therefore PRD had not yet consumed the new image un_

### Deployment Workflow / transform-layer Promotion

- **P053** Treating Merge as Promotion Readiness Without UAT Airflow Validation (2026-02-20)
  - _A transform-layer change was already merged, but promotion readiness was assumed before a successful UAT Airflow validation run. UAT evidence showed failures and missing dependency tables (`dim_user_reports_to_flat`) even though merge had completed._

### Documentation

- **P020** Stale Documentation After TRUNCATE+INSERT → CTAS Switch
  - _**Date:** 2026-02-05 **Category:** Documentation, Code Quality_

### Environment / Python Tooling

- **P049** Running Python Validation with Incompatible Interpreter Version (2026-02-19)
  - _Validation/test execution used a local Python interpreter outside repository constraints (for example 3.12+ when project requires `<3.12`), producing misleading failures and slowing diagnosis._

### Environment Reliability / Model Access

- **P057** Skipping Model/Entitlement Preflight in Interactive Sessions (2026-02-21)
  - _Session data showed repeated model-access friction: - 12 Claude history entries with Vertex `API Error: 404` / `NOT_FOUND` model-access failures. - Gemini sessions repeatedly asked for current model checks and used deterministic health probes._

### Estimation / Release Path Analysis

- **P065** Estimating a Shared Validation Fix Before Tracing the Release Path (2026-02-26)
  - _During the `dim_user_reports_to_flat` Gold-to-MySQL validation discussion, I initially estimated the proper fix as roughly `1-2 hours`. After tracing the actual ownership and rollout path, it became clear that the reusable fix was not a single local query tweak: - the query logic lives in `airflow-operators-repo` - the package change needs a releas_

### General

- **P095** `z` (zoxide) Fails in Non-Interactive Shells — Use `cd` (2026-03-12)
  - _Subagent Bash commands used `z <path>` for directory navigation. This generated repeated `Exit code 127: z:1: command not found: __zoxide_z` errors across multiple sessions (confirmed in session JSONL analysis). Zoxide's `z` command is a shell function injected only into interactive shells via `eval "$(zoxide init zsh)"`. Claude's Bash tool runs no_
- **P096** Versioned Claude Binary Path Goes Stale After Updates (2026-03-12)
  - _After a Claude Code version update, error logs showed repeated `ENOENT: no such file or directory, posix_spawn '/opt/homebrew/Caskroom/claude-code/2.1.49/claude'`. The errors clustered in one session and then disappeared._
- **P132** data-infra-repo Terraform PRs — Do Not Flag INFORMATION_SCHEMA Column Presence as a Blocker (2026-03-18)
  - _**Mistake:** Flagging a finding like "Terraform will fail to apply when modifying `f_point_transaction` because columns `external_number`/`memo` still exist in INFORMATION_SCHEMA" as a blocker on an data-infra-repo PR._
- **P133** Ignoring System Rules Due to "Shell-First" Bias (2026-03-18)
  - _**Mistake:** I fell into a trial-and-error loop of running `bq ls | grep` and guessing GCP regions to find a BigQuery table, directly violating the explicit system rule to use `mcp_bq-schema_tool_search_tables`. When corrected, I just added the rule to `mem0` instead of addressing why I ignored the rule in the first place._
- **P134** Execution Inefficiencies and "Bash Fallback" Anti-Patterns (2026-03-18)
  - _**Mistake:** Exhibiting multiple execution inefficiencies in a single session: stepping through local git commands sequentially, falling back to raw bash (`grep`, `cat`, `head`) instead of native tools, failing to parallelize tool calls, and triggering expensive pre-push hooks on a branch that was already behind the remote._
- **P231** Failing to Provide Manual Output Evidence
  - _- Never post simulated output as the sole proof of functionality. - Always perform manual verification against real infrastructure (`gcloud`, `gsutil`, `bq`). - Paste the exact CLI output (or screenshots) in the PR as undeniable evidence._
- **P232** Missing Referential Integrity Check for Keys
  - _- When a configuration file references a schema column (e.g., `user_key_column`, `partition_column`), always perform a strict referential integrity check against the actual schema definition to ensure the column exists._

### Git Workflow

- **P005** Validation Artifacts Committed to Production Repo
  - _**What happened**: Validation artifacts were committed to the production repo instead of PR comments. Treated evidence as production code._

### Git Workflow / Pull Request Creation

- **P246** Git Workflow / Pull Request Creation — Branching from Feature Branch instead of Master (2026-06-15)
  - _**Date:** 2026-06-15 **Category:** Git Workflow / Pull Request Creation_

### Git Workflow / Version Management

- **P063** Version Downgrade When Resolving Merge Conflicts (2026-02-23)
  - _While resolving merge conflicts in `airflow-operators-repo` PR #491, I used `git checkout --ours setup.py` to take the PR branch's version (`3.15.8`) over master's (`3.15.9`). This was wrong — master had already merged PR #492 bumping to `3.15.9`, so the PR branch version was now stale and lower than master. The reviewer flagged it as a downgrade._

### GitHub API / PR Review Workflow / Reply Posting

- **P136** Polluted Diffs from Reusing STG/Master Branches for Environment-Specific PRs (2026-03-18)
  - _**Mistake:** When creating PR #5801 to target `uat-data_change`, I reused the existing branch (`TICKET-N-uat-data-change-unblock`) which was originally cut from `stg`/`master`. Because `uat-data_change` is behind `stg`, the PR diff accidentally included unrelated commits from other developers (like TICKET-N and TICKET-N) that were present in `st_
- **P137** Misinterpreting CI Validation Scripts on MySQL Data Change Files (2026-03-18)
  - _**Mistake:** I flagged a BigQuery dry-run failure (`Syntax error: Expected end of input but got keyword USE`) on a `.sql` file inside the `database/data_change/` directory as a real error. I failed to recognize that `validate-changed-files.sh` was incorrectly applying a BigQuery validation script to a MySQL file, resulting in a false positive._
- **P138** GitHub PR Discussion Replies Use `in_reply_to` on the PR Comments Endpoint, Not `/replies` (2026-03-19)
  - _**Category:** GitHub API / PR Review Workflow / Reply Posting_
- **P250** Routing UAT Unblockers Through the Standard STG Pipeline (2026-03-18)
  - _**Mistake:** I routed PR #5800 (which was specifically created to unblock a failed `uat-data_change` deployment) through the standard `master -> stg` path. Because the `stg` pipeline's "merge branch upwards" step only fires for emergency changes, the fix stalled in STG and never reached `uat-data_change`, requiring a manual follow-up PR (#5801) to _

### GitHub Review State / Stale-Comment Prevention / Verification

- **P088** Bot Issue Comments Can Mutate — Re-Fetch the Exact Comment Before Assessing "False Positive" Status (2026-03-11)
  - _**Category:** GitHub Review State / Stale-Comment Prevention / Verification_

### GitHub Review Workflow / Comment Routing / Approval Integrity

- **P087** GitHub Comment Target Drift — Posting or Drafting Against the Wrong Review Object (2026-03-11)
  - _**Category:** GitHub Review Workflow / Comment Routing / Approval Integrity_

### GitHub Review Workflow / Comment Routing / Verification-Before-Completion

- **P245** GitHub PR Review — Claiming Inline Comments Posted Without Executing Script (2026-06-15)
  - _**Date:** 2026-06-15 **Category:** GitHub Review Workflow / Comment Routing / Verification-Before-Completion_

### GitHub Review Workflow / Target Routing / Approval Integrity

- **P139** `#pullrequestreview-...` URLs Are Review Summaries, Not Interchangeable with PR Comments or Inline Threads (2026-03-19)
  - _**Category:** GitHub Review Workflow / Target Routing / Approval Integrity_

### Gold Layer Pattern

- **P022** Success Metrics Inside BEGIN...END Instead of After END
  - _**Date:** 2026-02-06 **Category:** Gold Layer Pattern, SQL_

### Incident Resolution / Root Cause Analysis / PRD Support Communication

- **P240** Combining Region-Specific RCA and Incident Summaries in Region-Specific Alert Threads
  - _**Date:** 2026-06-02 **Category:** Incident Resolution / Root Cause Analysis / PRD Support Communication_

### Infrastructure

- **P179** GCS Retention Policy Approval on Bronze Tables
  - _**Category:** Infrastructure, GCS, Bronze Layer, Approval Process **Date:** 2026-04-15 **Source:** Scrum Recording (2026-04-15)_

### Infrastructure / Platform Architecture / Verification Before Recommendation

- **P256** Recommending an Untested "Self-Service" Platform Mechanism as a Fix Without Finding a Live Working Example (2026-07-06)
  - _**Category:** Infrastructure / Platform Architecture / Verification Before Recommendation_

### Investigation

- **P001** Hallucinating Dataflow Autoscaling Issues
  - _**Date:** 2026-01-29 **Context:** Investigating bizops streaming-pipeline high latency in us-central1 **Category:** Investigation, Dataflow, Autoscaling_

### Investigation / Data Pipeline / SCD2 / ADW Backfill

- **P145** Misdiagnosing Deleted `group_user` SCD2 Rows with NULL End Timestamps as a Sync Bug (2026-03-24)
  - _**Category:** Investigation / Data Pipeline / SCD2 / ADW Backfill_

### Investigation / Root Cause Analysis

- **P045** Misidentified Root Cause as Infrastructure Instead of Code Configuration (2026-02-10)
  - _**Context:** Testing announcement_view_rate_dag in STG after PR #853 merged_

### Jira Scope / AI Agent Governance / data-infra-repo / Pub/Sub

- **P184** Ambiguous Jira Scope Let Agents Reintroduce Out-of-Scope Pub/Sub DLT and Log Tables (2026-04-30)
  - _**Category:** Jira Scope / AI Agent Governance / data-infra-repo / Pub/Sub **Date:** 2026-04-30 **Ticket:** TICKET-N **PRs:** `data-infra-repo` PR #5678, PR #5703_

### Local Airflow / Executor Reliability / Validation Workflow

- **P072** Treating Local Airflow Task State as Ground Truth During Pgbouncer and Executor Churn (2026-03-05)
  - _While validating the local Airflow run `manual__local_20260306T005622Z` for `mysql_bronze_records_created_full_table_data_validation`, many mapped tasks were marked `failed`, `up_for_retry`, or `upstream_failed` even though the underlying Celery task execution had actually succeeded. The run also inherited false `upstream_failed` compare tasks afte_

### Meta-Ops / Scope Control / Tool Efficiency

- **P078** Running Full-Estate Retests and Retrospectives as Single Unbounded Sessions (2026-03-07)
  - _**Severity:** High **Tags:** external-tables, schema-contract, false-positive, consumer-verification, reporting, data-infra-repo **Related:** `data-infra-repo` PR `#5341`, discussion comments `2907528027`, `2907696097`, `2907862064` **Lesson Applied:** Added a canonical PR-review rule in `CLAUDE.md` requiring table-specific consumer/provenance evidence before blocking on external-table contract mismatches._

### Performance / AI Agents / PR Review / Orchestration

- **P191** Marking RateLimitError as Non-Retryable in Parallel Orchestration
  - _**Category:** Performance / AI Agents / PR Review / Orchestration **Date:** 2026-04-25 **Session ID:** `497b5312`_

### Posting / Approval Flow

- **P225** Premature Posting Without Adequate Testing
  - _- Check if the table is newly added or part of a recent feature (context from parent tickets). - Use the `❓ q:` or `🟡 risk:` tag for "breaking" changes in DEV to solicit the author's plan first. - Re-read **Error 132** (don't flag INFORMATION_SCHEMA columns as blockers) - applies the same "don't be too rigid with infra PRs" principle._

### PR Communication / Evidence Mapping / Reviewer Clarity

- **P149** Posting PR Replies Where the Evidence Did Not Match the Reviewer's Exact Question (2026-04-08)
  - _**Category:** PR Communication / Evidence Mapping / Reviewer Clarity_

### PR Communication / Validation Scoping / Reviewer Clarity

- **P101** Posting Praise or "Working Well" Comments in PR Reviews (2026-03-14)
  - _**What Happened:** I posted PR review comments pointing out things that were working well, praising the code, or saying "this looks good," rather than focusing strictly on issues or necessary changes._
- **P102** Mixing Validation Routes in One PR Comment Made the Evidence Ambiguous (2026-03-16)
  - _**Category:** PR Communication / Validation Scoping / Reviewer Clarity_
- **P248** Proving a Negative Using Incomplete CLI/API Output (2026-03-13)
  - _**What Happened:** When asked if rate limits existed for `gemini-3.1-pro-preview`, I ran a `curl` to a `global` Vertex AI endpoint (which returned 404) and a `gcloud alpha services quota list` (which didn't explicitly list the preview model). Based on these two failures, I confidently asserted that the model "does not currently exist in Vertex AI."_

### PR Creation / Database Data Change / Validation Safety

- **P169** Database Retry PR Carried the Wrong Ticket and Repeated Unsafe Insert Patterns (2026-04-16)
  - _**Category:** PR Creation / Database Data Change / Validation Safety_

### PR Creation / Self-Review Discipline

- **P073** Reviewing My Own PR in Builder Mode Instead of Running a Separate Diff-Based Self-Review (2026-03-06)
  - _While driving `airflow-operators-repo` PR `#507`, I repeatedly fixed issues only after the GitHub review bot surfaced them. The pattern was consistent: - implement a fix - run tests - push - wait for the bot to catch repo-boundary, filter-before-limit, sentinel-contract, timezone, or `None`-handling issues that I should have caught myself first_

### PR Creation / Validation

- **P156** Pushing Code to GitHub Before Resolving review-platform Review Findings (2026-04-11)
  - _**What Happened:** When creating PRs or pushing updates, agents sometimes push code to GitHub before fully running the `review-platform` review tool locally or before ensuring all critical issues have been fixed and verified by a clean re-run._

### PR Review

- **P025** Not Using review-platform MCP for PR Reviews (2026-02-09)
  - _**Situation:** Reviewing multiple PRs (#5141, #484, #5127, #5143, etc.)_
- **P027** Approving PR Before review-platform Review Completed (2026-02-09)
  - _**Error:** Approved the PR immediately while review-platform review was still running in background. The review took 3.5 minutes to complete and returned after I had already approved._
- **P028** PR Review - Claiming SQL Failure Without Validation
  - _**Date:** 2026-02-10 **Context:** Reviewing PR #5121 (data-infra-repo) - MV changes for likes/comments count **Category:** PR Review, SQL Validation_
- **P029** PR Review - Posting Verification Comments When Everything Is Fine
  - _**Date:** 2026-02-10 **Context:** Reviewing PR #5158 (data-infra-repo) - Bizops GL partitioning **Category:** PR Review, Communication_
- **P229** PR Review / BigQuery Infrastructure / Context Awareness
  - _- Check if the table is newly added or part of a recent feature (context from parent tickets). - Use the `❓ q:` or `🟡 risk:` tag for "breaking" changes in DEV to solicit the author's plan first. - Re-read **Error 132** (don't flag INFORMATION_SCHEMA columns as blockers) - applies the same "don't be too rigid with infra PRs" principle._

### PR Review / Airflow / Jinja Templating

- **P182** False Positive PR Review - Flagging Intentional f-string/Jinja Nesting in Airflow Templates
  - _**Category:** PR Review / Airflow / Jinja Templating **Date:** 2026-04-23 **PR:** `reporting-pipelines` PR #928_
- **P233** Schema-Only Changes Skipping Sync Workflows
  - _- Ensure CI/CD sync workflows handle logic or schema-only changes correctly. - If a core schema change can invalidate existing configs, the pipeline must force a full resync or re-evaluation of all dependent assets, not just skip the job._
- **P234** Using Hardcoded Path Indices
  - _- Never blindly extract data using hardcoded path indices. - Always validate the expected directory shape/anchor (e.g., `environment/<env>/domains/<domain>/bq/<dataset>`) before parsing it to fail-fast on structural changes._
- **P235** Accidental Approval of Database Schema Changes Without Warehouse Setup (PR #6160)
  - _**What Happened:** Database PR `https://github.com/example-org/database/pull/6160` (MySQL schema changes) was approved by mistake. The PR was approved without first verifying that the corresponding columns were added to the Bronze/Bronze Native (BN) tables in `data-infra-repo` and ensuring proper synchronization across the warehouse layers._

### PR Review / Airflow / Jinja Templating / False Positive Prevention

- **P181** `Param(None)` Does Not Neutralize Jinja `'None'` in Airflow DAG Params (2026-04-22)
  - _**Category:** PR Review / Airflow / Jinja Templating / False Positive Prevention_

### PR Review / Airflow / SCD1

- **P195** Flagging Missing `WHEN NOT MATCHED` on SCD1 Tables Relying on Source `delete_flag`

### PR Review / Airflow Loops

- **P200** Flagging Loop Variable Leak in Many-to-One Reporting DAGs

### PR Review / API Semantics / False Positive Prevention

- **P085** PR Review — Flagging BigQuery `BadRequest` Retry as a False Positive Without Verifying API Semantics or Repo Precedent (2026-03-10)
  - _**Category:** PR Review / API Semantics / False Positive Prevention_

### PR Review / Baseline Verification / False Positive Prevention

- **P143** Using Local `origin/master...HEAD` Instead of Live PR Base Created a False-Positive Scope Finding (2026-03-20)
  - _**Category:** PR Review / Baseline Verification / False Positive Prevention_

### PR Review / BigLake Schema

- **P154** Including `client_pk`/`file_timestamp` as Schema Fields in BigLake External Table YAMLs (2026-04-10)
  - _Approved data-infra-repo PR #5478 (TICKET-N) which added new BigLake external table YAMLs for `product_catalog` and `product_catalog_region` in the marketplace domain. Both YAMLs incorrectly included `client_pk` (NULLABLE STRING) and `file_timestamp` (NULLABLE STRING) as explicit schema fields after the payload se_

### PR Review / BigLake vs External Table

- **P213** Applying BigLake Terraform Partition Restriction to Standard BQ External Tables (2026-05-05)
  - _**Category:** PR Review / BigLake vs External Table_

### PR Review / BigQuery Optimization

- **P196** Recommending Partitioning for Very Small Tables

### PR Review / Causality Verification / False Positive Prevention

- **P071** Flagging a Pre-Existing Cross-Repo Drift as a PR Regression Without Baseline Proof (2026-03-05)
  - _**Severity:** High **Tags:** review-quality, false-positive, baseline-check, causality, cross-repo-drift **Related:** `reporting-pipelines` PR `#892`, `data-infra-repo` schema drift discussion on `f_user_activity_daily_agg` **Lesson Applied:** Blocking comments now require an explicit base-vs-head causality checkpoint before posting._
- **P075** Flagging a Pre-Existing In-Repo Docs Drift as a PR Regression Without Checking Base Branch (2026-03-06)
  - _**Severity:** High **Tags:** review-quality, false-positive, baseline-check, same-file-diff, docs-drift, causality **Related:** `review-platform` PR `#86`, comments `2897981774`, `2898003657`, `2898028686` **Lesson Applied:** PR review workflow now includes a mandatory same-file base-branch check for semantics and documentation findings before posting._

### PR Review / CI Debugging / Evidence-First Verification

- **P257** Accepting a Bot's Root-Cause Diagnosis by Symptom Match Without Independently Re-Deriving the Cause (2026-07-06)
  - _**Category:** PR Review / CI Debugging / Evidence-First Verification_

### PR Review / Claim Verification

- **P046** Posted a Regex Finding Before Verifying the Exact PR SQL Artifact (2026-02-18)
  - _A blocking review finding was posted on `data-infra-repo` PR #5201 claiming BigQuery regex `\d` was the cause of cost under-allocation and should be replaced with `[0-9]`._

### PR Review / Comment Accuracy

- **P221** Branch Poisoning and Multi-Ticket Mismatch (2026-05-10)
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._
- **P222** Referencing Non-Public Endpoints in Posted PR Comment
  - _**Date:** 2026-05-12 **Category:** PR Review / Data Cardinality / False Positive Prevention_

### PR Review / Communication

- **P160** Commenting on What Works Well in PR Reviews (2026-04-14)
  - _**What Happened:** In PR reviews for assertion promotion PRs (#131, #121, #216, #139), I included positive commentary like "Excellent coverage", "Clean assertion set", "Otherwise excellent", "Overall looking excellent", "Nice work", etc._

### PR Review / Communication / User Intent

- **P144** Collapsing Duplicate Review Drafts When the User Asked for All Comments (2026-03-20)
  - _**Category:** PR Review / Communication / User Intent_

### PR Review / Consumer Verification / False Positive Prevention

- **P079** Inferring an External-Table Contract From Sibling Patterns Without Table-Specific Consumer Evidence (2026-03-09)
  - _**Severity:** High **Tags:** external-tables, schema-contract, false-positive, consumer-verification, reporting, data-infra-repo **Related:** `data-infra-repo` PR `#5341`, discussion comments `2907528027`, `2907696097`, `2907862064` **Lesson Applied:** Added a canonical PR-review rule in `CLAUDE.md` requiring table-specific consumer/provenance evidence before blocking on external-table contract mismatches._

### PR Review / Context Validation / False Positive Prevention

- **P251** Providing PR Feedback Without Thoroughly Reading All Comments
  - _**Date:** 2026-05-12 **Category:** PR Review / Context Validation / False Positive Prevention_

### PR Review / Cross-Repo Dependency / Analytics Layer

- **P081** Reviewing Analytics Layer DAG Without Verifying transform-layer Tag Exists (2026-03-10)
  - _While reviewing `pipelines-members` PR `#280`, which added `analytics_layer_trigger_dataform.py` with `DATAFORM_TAGS = ['members-analytics']`, I approved the PR without independently checking whether the `members-analytics` tag existed in the `transform-repo` repo. The concern only surfaced because a prior comment on the PR had already flagged_

### PR Review / DAG Generation

- **P199** Flagging Duplicated Logic between YAML Config and Python DAG

### PR Review / Data Pipeline

- **P030** PR Review - Claiming Schema Design Issue Without bq CLI Verification
  - _- Always use bq query dry_run to test schema patterns before claiming issues - Test BOTH approaches to understand tradeoffs - Consider use case context (config vs data, manual vs generated) - Only claim schema issues if verification shows actual problems_

### PR Review / Data Types

- **P204** Suggesting Strict Typing over Source Alignment
  - _**Context:** Working on TICKET-N, TICKET-N, and TICKET-N in the ai-tools repository._

### PR Review / Database & Warehouse Coordination / Strict Deployment Order

- **P210** Database PR Approved Without Ensuring Column Prep in Bronze Tables
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._

### PR Review / Decision Gating

- **P050** Asking Approval-Readiness Before Completing Mandatory Review Gates (2026-02-19)
  - _Approval-readiness was evaluated or implied before all required gates were complete (latest author response review, evidence re-check, and final check status confirmation)._

### PR Review / Dependency Sequencing / False Positive Prevention

- **P100** PR Review — Posted a Dependency Blocker Before Verifying Merge/Apply Order (2026-03-13)
  - _**Category:** PR Review / Dependency Sequencing / False Positive Prevention_

### PR Review / Domain Knowledge

- **P161** Flagging Intentional `WHERE tenant_key > 0` Pattern in Assertion Reviews (2026-04-14)
  - _**What Happened:** In reviews of transform-layer assertion PRs (#131, #121, #216, #139), I flagged `WHERE tenant_key > 0` as a potential issue, suggesting it should be removed or documented because it won't detect violations where `tenant_key ≤ 0`._

### PR Review / Environment Config

- **P202** Flagging Dropped Regions in Dev Environment Gold Layer

### PR Review / Environment Contract / False Positive Prevention

- **P140** Flagging Unsupported Local Interpreter Compatibility as a PR Blocker (2026-03-19)
  - _**Category:** PR Review / Environment Contract / False Positive Prevention_

### PR Review / Environment Selection / Evidence Integrity

- **P098** Agent Workflow — Over-reliance on Context and Interaction (2026-03-13)
  - _**What Happened:** Historically, I have relied on conversational branching (asking clarifying questions) and reading large chunks of files (relying on my context window) to solve problems. This approach is slow, consumes unnecessary tokens, and fails in automated/constrained environments. I also frequently dropped steps in multi-step tasks because _
- **P099** PR Review — Used DEV Data for a Production-Path Contract Claim (2026-03-13)
  - _**Category:** PR Review / Environment Selection / Evidence Integrity_

### PR Review / Execution Integrity

- **P041** Accepting Interrupted `/review` Runs as If Review Work Was Complete (2026-02-18)
  - _Multiple sessions returned: "Review was interrupted. Please re-run /review and wait for it to complete."_

### PR Review / Execution Reliability

- **P056** Accepting Interrupted Review Attempts Without Full Restart (2026-02-21)
  - _Codex session analysis found repeated interruption markers: - 29 occurrences of: user-initiated review interrupted, re-initiate `/review` and wait for completion. - 10 occurrences of: previous turn intentionally interrupted and running processes terminated._

### PR Review / File Context / False Positive Prevention

- **P082** Answering "Is This a False Positive?" Without Reading the Code First (2026-03-10)
  - _User linked `https://github.com/example-org/review-platform/pull/88#issuecomment-4014428054` and asked if it was a false positive. The URL contains a specific comment ID. The MCP tool (`get_pr_comments`) returned comments without their GitHub comment IDs — just "Comment 1", "Comment 2", etc. — so I could not directly map the fragment ID to a speci_
- **P083** PR Review — Reviewing Only the Diff Instead of the Full File, Missing Cross-Subquery Consistency (2026-03-10)
  - _**Category:** PR Review / File Context / False Positive Prevention_

### PR Review / GCS Paths

- **P206** Flagging GCS Path Inconsistency in Many-to-One DAGs
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._

### PR Review / Ingestion Gap Analysis / False Positive Prevention

- **P238** False Positive Ingestion and Deletion Sync Lag Diagnoses on UAT/Live Programs
  - _**Date:** 2026-06-01 **Category:** PR Review / Ingestion Gap Analysis / False Positive Prevention_

### PR Review / Ingestion Gap Analysis / Scrub Verification

- **P239** Triaging Ingestion Failures for Decommissioned/Scrubbed Programs Without Checking Scrub DAG
  - _**Date:** 2026-06-01 **Category:** PR Review / Ingestion Gap Analysis / Scrub Verification_

### PR Review / JIRA Context / False Positive Prevention

- **P084** PR Review — Posting a Ticket-Scoped Finding Before Reading the Referenced JIRA (2026-03-10)
  - _**Category:** PR Review / JIRA Context / False Positive Prevention_

### PR Review / Local-Upstream Drift / False Positive Prevention

- **P236** PR Review - Citing Local Assistant Guidelines with Upstream Drift (2026-05-22)
  - _**Date:** 2026-05-22 **Category:** PR Review / Local-Upstream Drift / False Positive Prevention_

### PR Review / Meta-Ops

- **P209** Leaving Bad Reviews Unedited After Debunking
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._

### PR Review / Noise Reduction

- **P208** Posting Stylistic or Optimization Nits
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._

### PR Review / Pattern Verification / Domain Knowledge

- **P163** Giving Contradictory Advice Between Related PRs Without Verifying Standard Pattern First (2026-04-14)
  - _**Category:** PR Review / Pattern Verification / Domain Knowledge_

### PR Review / Reporting DAGs

- **P198** Flagging Missing `source_region` in Unique Columns when `client_pk` is Unique

### PR Review / Rollout Topology / False Positive Prevention

- **P080** Flagging a Cross-Region Review Blocker Without Verifying the Rollout Topology (2026-03-09)
  - _**Severity:** High **Tags:** pr-review, rollout-topology, region-scope, false-positive, airflow, promotions **Related:** `pipelines-data-architecture` PR `#653`, discussion comments `2907494234`, `2907512149`, `2907526604` **Lesson Applied:** Added a canonical rule in `CLAUDE.md` requiring rollout/topology verification before posting cross-region blockers._

### PR Review / Schema Design

- **P203** Flagging Missing Audit Fields on Custom Query Native Tables
  - _**Context:** Working on TICKET-N, TICKET-N, and TICKET-N in the ai-tools repository._
- **P205** Flagging `NUMERIC` Type as Incompatible for External Tables
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._
- **P207** Flagging Composite Key Elements as Non-PK Attributes
  - _- Run `git branch -a` and `gh pr list` at the start of every session to map the landscape. - Use `mktemp` for all test artifacts (especially evil runners) to avoid race conditions and cleanup traps. - Trigger `review-platform-review` *immediately* after every push to catch regressions and architectural drift early. - Keep a "/tmp/backup" of your feature-specific code when performing complex git operations._

### PR Review / Schema Evolution

- **P197** Recommending `NULLABLE` to `REQUIRED` Change on Existing Datalake Tables

### PR Review / Schema Verification

- **P036** PR Review - Claimed Bronze Native `client_pk` Risk Without Verifying Schema (2026-02-17)
  - _During PR review for `airflow-operators-repo` PR #489, I posted a blocking P1 comment claiming that central Bronze Native tables might not have `client_pk`, which could make the single-count query path invalid or undercount._

### PR Review / Scope and Severity Discipline

- **P070** Escalating a Robustness Concern to "Must Fix Before Merge" Without Ticket-Scope Confirmation (2026-03-04)
  - _**Severity:** High **Tags:** review-quality, false-positive, baseline-check, causality, cross-repo-drift **Related:** `reporting-pipelines` PR `#892`, `data-infra-repo` schema drift discussion on `f_user_activity_daily_agg` **Lesson Applied:** Blocking comments now require an explicit base-vs-head causality checkpoint before posting._

### PR Review / Scope Discipline

- **P061** Drifting Out of Review-Only Mode Into Implementation or Post-Merge Execution (2026-02-22)
  - _PR-focused session analysis (2026-02-15 through 2026-02-21) found repeated self-corrections where review mode drifted: - "I went beyond review scope and made changes when you asked for review." - "I should have stayed in review mode." - "I executed review-oriented steps when you asked for post-merge execution."_

### PR Review / SQL / Data Contracts

- **P162** Assuming Removed `coalesce` Fallback is Automatically a Problem (2026-04-14)
  - _**What Happened:** In transform-repo PR #131, I flagged the removal of `coalesce(uts.redeem_only, u_scd2.is_redeem_only)` to just `u_scd2.is_redeem_only` as a data contract issue requiring verification, assuming it could cause NULLs if the SCD2 join fails._

### PR Review / State Synchronization

- **P047** Reviewing Stale PR State After Author Updates (2026-02-19)
  - _Review conclusions were carried forward from an earlier PR snapshot after author updates (new commits, force-push, or addressed comments). That created a risk of approving or commenting on outdated code state._

### PR Review / Template Logic / False Positive Prevention

- **P228** Misidentifying Missing Placeholder Substitution Due to Layered Templating
  - _- Check if the table is newly added or part of a recent feature (context from parent tickets). - Use the `❓ q:` or `🟡 risk:` tag for "breaking" changes in DEV to solicit the author's plan first. - Re-read **Error 132** (don't flag INFORMATION_SCHEMA columns as blockers) - applies the same "don't be too rigid with infra PRs" principle._

### PR Review / transform-layer Integration

- **P201** Suggesting transform-layer Tags in Airflow Repositories

### PR Review / transform-layer Syntax / Assertion Management

- **P164** Missing Trailing Comma Before `disabled: true` in SQLX Config Blocks (2026-04-15)
  - _**Category:** PR Review / transform-layer Syntax / Assertion Management_

### PR Review / transform-layer Tag Verification

- **P168** False Positive transform-layer Tag Blocker Due to Stale State Check (2026-04-16)
  - _**Category:** PR Review / transform-layer Tag Verification_

### PR Review / Value-Level Contract / Data Cardinality

- **P086** PR Review — Missed Registry Lookup Uniqueness Bug Because I Didn't Verify Data Cardinality (2026-03-10)
  - _**Category:** PR Review / Value-Level Contract / Data Cardinality_

### PR Review / Verification Workflow

- **P033** PR Review - Verified Behavior But Forgot SQL Syntax Validation (2026-02-10)
  - _**Context:** Reviewing PRs #485 (airflow-operators-repo) and #259 (internal-lib-dw-utility)_

### PR Review / Workflow Compliance

- **P038** Skipping review-platform Review Because of Time Pressure (2026-02-13)
  - _A PR review request explicitly asked to skip review-platform due to time pressure, and review proceeded manually._

### PR Workflow / Review Bot State Management

- **P074** Trusting Stale GitHub Review or CI State Without Verifying the Live PR Head (2026-03-06)
  - _**Severity:** High **Tags:** review-quality, false-positive, baseline-check, same-file-diff, docs-drift, causality **Related:** `review-platform` PR `#86`, comments `2897981774`, `2898003657`, `2898028686` **Lesson Applied:** PR review workflow now includes a mandatory same-file base-branch check for semantics and documentation findings before posting._

### Process / Documentation Drift / AI-Agentic Metadata / data-infra-repo / PR Review

- **P243** Documentation Drift and AI-Agentic Hallucinations in Repository Instructions (CLAUDE.md) (2026-06-06)
  - _**Date:** 2026-06-06 **Category:** Process / Documentation Drift / AI-Agentic Metadata / data-infra-repo / PR Review_

### Prompt Hygiene / Token Efficiency / Scope Control

- **P076** Leaving Massive Inline Artifacts in the Prompt Instead of Externalizing Scope (2026-03-07)
  - _Multiple recent sessions accepted huge pasted inputs directly into the working prompt instead of first converting them into files, targeted excerpts, or bounded context artifacts._

### Review Communication / Evidence Integrity

- **P066** Posting the Wrong-Environment Screenshot as UAT Evidence (2026-02-27)
  - _When asked to add UAT evidence to `pipelines-data-architecture` PR `#638`, I first tried to satisfy the request by reusing nearby artifacts: - referenced evidence from PR `#637` - posted a screenshot the user identified as STG, not UAT - had to be corrected and rerun the validation in actual UAT before posting the final proof_

### Review Quality / Claim Verification

- **P042** Capping Findings Before Claim Verification (2026-02-14)
  - _There was pressure to limit the number of findings passed into claim verification (e.g., "maximum findings is 3"), while unresolved concern remained that all findings should be verified._

### Safety / Process Discipline / Guardrail Integrity

- **P255** Bypassing Established Guardrails or Safety Checks Under Perceived User Pressure (2026-07-04)
  - _**Date:** 2026-07-04 **Category:** Safety / Process Discipline / Guardrail Integrity_

### Schema / PR Review

- **P193** False positive on REQUIRED schema columns for Backfills

### Service Operations / Incident Triage

- **P039** Treating LaunchAgent Bootstrap Failure as Service Outage Without Data-Plane Check (2026-02-17)
  - _`launchctl bootstrap` reported `Input/output error`, but health endpoint checks still returned `{"status":"ok"}` for `http://127.0.0.1:8081/health`._

### Shell / macOS / Local Testing

- **P128** macOS Has No `timeout` Command — Use Background Job + Sleep Loop Instead (2026-03-17)
  - _During the DirectRunner execution for the cascade smoke test, used `timeout 90 python streaming-pipeline.py ...` to bound the run time. The command silently failed on macOS because macOS does not ship GNU `timeout`. The process exited with an error immediately instead of running the pipeline._

### Shell Safety / Review Communication

- **P048** Shell Backtick Expansion Corrupting `gh` Comment Commands (2026-02-19)
  - _Markdown content containing backticks was passed through shell quoting in a way that allowed command substitution/escaping side effects, leading to malformed `gh pr comment` body content._

### Shell Safety / Review Evidence Collection

- **P062** Unquoted `gh api` URLs Triggering zsh Globbing During CODEOWNERS Verification (2026-02-22)
  - _During PR approval-requirement verification (`CODEOWNERS` + branch rules), two `gh api` commands failed with: - `zsh:1: no matches found: repos/example-org/.../CODEOWNERS?ref=prd`_

### SQL

- **P006** BigQuery BEGIN TRANSACTION Nesting Confusion
  - _**What happened**: Misunderstood that `BEGIN TRANSACTION` cannot be nested inside `BEGIN` in BigQuery. BigQuery actually allows an outer `BEGIN...EXCEPTION...END` block combined with an inner `BEGIN TRANSACTION...COMMIT TRANSACTION` block for atomicity._
- **P008** Flat Schema Assumption on Nested RECORD Gold Event Log
  - _**Date:** 2026-02-05 **Category:** SQL, Schema, Gold Layer_
- **P009** Missing SELECT DISTINCT in SCD2 CTE Causing Fan-Out
  - _**Date:** 2026-02-05 **Category:** SQL, Data Quality, SCD2_
- **P012** Duplicated 8-Line SCD2 Temporal Join Logic Across 5 CTEs
  - _**Date:** 2026-02-05 **Category:** SQL, Code Quality, DRY Principle_
- **P013** Missing Partition/Cluster Filter on Event Log Table Join
  - _**Date:** 2026-02-05 **Category:** SQL, Performance, BigQuery_
- **P017** `flag = FALSE` on BOOLEAN Column Excluding NULLs
  - _**Date:** 2026-02-05 **Category:** SQL, Boolean Logic_
- **P019** Column Missing from Subquery SELECT List
  - _**What happened**: A column referenced in an outer `WHERE` clause was missing from the subquery's `SELECT` list, resulting in a "column not found" error._
- **P021** Mixed `is_deleted=0` and `_warehouse_metadata.delete_flag=false`
  - _**Date:** 2026-02-05 **Category:** SQL, Gold Layer Conventions_
- **P023** Numeric Comparisons on BOOLEAN Columns
  - _**Date:** 2026-02-06 **Category:** SQL, Boolean Logic_

### SQL Query

- **P024** Missing Required Column in MERGE Statement (2026-02-09)
  - _**Category:** SQL Query, Local Testing, Data Pipeline_

### SQL Semantics

- **P010** SCD2 Boundary Changed from <= to <
  - _**Date:** 2026-02-05 **Category:** SQL Semantics, SCD2_

### State Verification

- **P226** Incorrect Assumption About PR Creation / State
  - _- Check if the table is newly added or part of a recent feature (context from parent tickets). - Use the `❓ q:` or `🟡 risk:` tag for "breaking" changes in DEV to solicit the author's plan first. - Re-read **Error 132** (don't flag INFORMATION_SCHEMA columns as blockers) - applies the same "don't be too rigid with infra PRs" principle._

### Testing / Dataflow / Evidence Quality

- **P124** Used Functional Tests (DirectRunner) as Evidence for a Dataflow Job Fix (2026-03-17)
  - _**Category:** Testing / Dataflow / Evidence Quality_

### Testing / streaming-pipeline / Dataflow / Evidence Scoping

- **P148** Treating Batch or Bronze-Only Dataflow Evidence as Enough for a Requested Streaming Validation (2026-04-08)
  - _**Category:** Testing / streaming-pipeline / Dataflow / Evidence Scoping_

### Go Microservices / Scaffolding / CI/CD Scope Discipline

- **P262** Microservice Scaffolding Scope Creep, Incorrect Teams Channels, and Active Deploy on Unregistered Repo (2026-08-13)
  - _Keep repo scaffolding PRs strictly scoped to framework setup (cmd/app/grpc, Dockerfile, doit.sh, CI). Route MS Teams notifications to Data Architecture channels (Data Architecture - Non-PRD/UAT/PRD) under team 'BE - Notifications'. Default enable_deployment to false in new service deploy.yml until ArgoCD registration lands._

### Git Workflow / Pre-Push Gates / Process Safety

- **P263** Bypassing Pre-Push No-Mistakes Gate on Feature Branch via `--no-verify` (2026-08-13)
  - _Never use `--no-verify` to bypass local pre-push validation on feature branches. Always execute `no-mistakes axi run --intent "..."` or `no-mistakes push` prior to pushing._

### Testing / streaming-pipeline / Evidence Methodology

- **P127** Synthetic AVRO DELETE Pattern — Building PR-Branch Evidence Without a Real MySQL Delete (2026-03-17)
  - _**Category:** Testing / streaming-pipeline / Evidence Methodology_

### Testing / Validation / PR Review

- **P155** Testing the Mechanism Instead of the Business Goal (streaming-pipeline PR 245) (2026-04-11)
  - _In `streaming-pipeline` PR 245, the goal was to improve performance by caching AVRO schemas for a full day instead of hourly to reduce GCS I/O. The developer initially tested the caching mechanism by processing two records *inside the same single file*._

### Tool Output Discipline / Transcript Processing / Session Efficiency

- **P077** Using Brute-Force Shell and Polling Loops for Transcript and Output-Heavy Tasks (2026-03-07)
  - _I repeatedly handled transcript-heavy or output-heavy work with the same live shell/polling loop pattern used for interactive debugging._

### Tool Usage / Attribution

- **P223** Tool Misidentification — Claimed Claude When Gemini Was Used
  - _- Check if the table is newly added or part of a recent feature (context from parent tickets)._

### Tooling / AI Assistant Setup / Vertex AI

- **P142** ob1 Vertex AI Integration Requires a Multi-Layer Proxy to Work with Claude Models (2026-03-19)
  - _**Category:** Tooling / AI Assistant Setup / Vertex AI_

### Tooling / Gemini CLI / Configuration

- **P189** Gemini CLI MCP Server Deactivation (Missing "disabled": true support)
  - _**Category:** Tooling / Gemini CLI / Configuration **Date:** 2026-04-21 **Session ID:** `feeaae1a`_
- **P253** Confusing Gemini CLI's Layered Context Files
  - _**Category:** Tooling / Gemini CLI / Configuration_

### Tooling / MCP Diagnostics

- **P059** Collapsing Different MCP Failure Modes into a Single "Server Broken" Diagnosis (2026-02-22)
  - _Session analysis for 2026-02-15 through 2026-02-22 showed repeated MCP failures with different root causes: - **90** `Transport closed` failures across multiple MCP tools/endpoints - **3** `resources/list ... Method not found` failures (all `confluence`)_

### Tooling / MCP Reliability

- **P040** Assuming MCP Servers Are Usable Because They Are Registered (2026-02-16)
  - _Session flow exposed confusion between MCP servers being "registered" and being "connected/usable."_

### Tooling / Shell Session Management

- **P058** Polling Non-PTY Shell Sessions with `write_stdin` After Stdin Closed (2026-02-22)
  - _Codex session analysis for 2026-02-15 through 2026-02-22 found **67 occurrences** of: - `write_stdin failed: stdin is closed for this session; rerun exec_command with tty=true to keep stdin open`_

### transform-layer / Analytics Layer Review

- **P152** Assuming `getSqlWithMetricsForAnalytics` Uses `ref()` for `layerFeatureConfigTable` (2026-04-09)
  - _Reviewed transform-repo PR #123. Flagged `layerFeatureConfigTable: "layer_feature_config"` as a compile blocker, claiming `getSqlWithMetricsForAnalytics` resolves this via transform-layer's `ref()` and therefore requires a declaration file in the repo. No declaration file existed in the diff. Posted as a blocker._
- **P153** Treating `featureName` in `getSqlWithMetricsForAnalytics` as a Display/Copy Field (2026-04-09)
  - _In the same transform-repo PR #123 review, flagged `featureName: "program_list"` on `dim_program_region.sqlx` as a copy-paste error because the table is `dim_program_region`, not a program list._

### transform-layer / Validation Coverage

- **P037** Validated Main Operations But Missed Changed Assertion SQLX Target (2026-02-18)
  - _In `transform-repo` PR #188, the core operation targets were validated and succeeded in DEV, but a changed assertion target was not executed as part of validation. The assertion file used `latest_silver.is_deleted IS TRUE` while `is_deleted` in Silver is `INT64`, which caused runtime failure when the assertion target was invoked later._

### Validation Planning / Data Safety / Candidate Selection

- **P120** UAT Validation Candidate Selection — Defaulted to `program_fk = 1` Before Proving a Smaller Flowing Alternative Did Not Exist (2026-03-16)
  - _**Category:** Validation Planning / Data Safety / Candidate Selection_

### Validation Planning / DBRE Risk Guidance / Database Pipeline Metadata

- **P121** Overcorrected Away From Internal Program 1 and Then Broadened Shard Scope Before Respecting DBRE Risk Guidance (2026-03-16)
  - _**Category:** Validation Planning / DBRE Risk Guidance / Database Pipeline Metadata_

### Verification / Communication / Claims Accuracy

- **P254** Premature Claims of Completion/Verification Without Actually Doing the Work (2026-07-04)
  - _**Date:** 2026-07-04 **Category:** Verification / Communication / Claims Accuracy_

### Workflow

- **P044** Created PR for Already-Fixed Bug (2026-02-10)
  - _**Context:** PR #853 (announcement_view_rate DAG) was merged_

### Workflow / Authorization

- **P032** Deleted BigQuery Table Without Approval (2026-02-10)
  - _PR #5169 (Tommy's `fact_nominee_scd2_status` view) triggered a CI/CD failure. The error was on a DIFFERENT table: `announcement_view_rate_external_table`. The Terraform apply failed with:_

### Workflow / Cross-Agent Governance

- **P055** Cross-Agent Policy Drift from Unsynchronized Instruction Files (2026-02-21)
  - _Session review across assistants showed policy-clarity prompts in Gemini (`"you can use claude.md?"`, `"you always ask permission?"`) while core workflow/approval rules were maintained mainly in `AGENTS.md` / `Claude.md`._

### Workflow / Environment Readiness

- **P043** Starting Complex Work from Home Directory Without Repo Preflight (2026-02-18)
  - _Session analyses show the dominant historical error pattern came from working in `~` instead of a target repository with full context._

### Workflow / PR Creation / Tooling Guardrails

- **P094** PR Creation Guardrails Depend on the Actual `gh` Binary in PATH (2026-03-12)
  - _**Category:** Workflow / PR Creation / Tooling Guardrails_

### Workflow Reliability / Guardrail Handling

- **P052** Skipping Structured Fallback After Local Policy/Hook Blocks a Workflow (2026-02-19)
  - _When local hooks/policies blocked an operation, workflow handling was inconsistent (retry loops or ad-hoc workarounds) instead of following a clear fallback path with explicit risk/approval handling._

### Workflow Reliability / Status Contract Integrity

- **P069** Allowing False-Green Status Propagation from Program Scrub Prechecks (2026-03-02)
  - _During scrum incident review, the team identified that a program-scrub flow could return overall green even when latency/wait prechecks failed. Because downstream workflows consumed that status, this created a risk that launch-related actions could proceed under a false success signal._

