---
name: verify
description: "Generic verification pass for completed work (tests, dry-runs, evidence). Use when about to claim a change is done, fixed, or ready to ship."
---

# Verification & Anti-Hallucination Gate

**Usage:** `/verify [task_or_file]`

**Purpose:** Act as an aggressive anti-hallucination gate. Enforces the "Evidence First" rule by requiring cryptographic, value-level, or dry-run proof before any task is considered complete.

<instructions>
1. **Load Guidelines:**
   - Execute `read_file` on `${HOME}/HALLUCINATION_PREVENTION_GUIDE.md` to load specific anti-hallucination patterns.
2. **Schema & SQL Verification (Data Tasks):**
   - **Rule:** NEVER assume column names or types.
   - **Action:** Run `mcp__bq-schema__get_table_schema` to prove the columns exist.
   - **Action:** Extract the SQL and run `run_shell_command` with `bq query --dry_run`. Present the success output.
3. **Value-Level Proof (Data Contracts):**
   - **Rule:** NEVER claim a contract is met or tables are identical without data proof.
   - **Action:** Run `bq query --format=json` with targeted `COUNTIF(col IS NULL)`, distribution, or parity checks.
   - **Action:** Output the raw JSON evidence.
4. **Architectural Verification:**
   - **Rule:** NEVER guess system dependencies or architecture.
   - **Action:** Invoke the `codebase_investigator` sub-agent to trace exact file paths and symbols.
5. **Infrastructure Verification:**
   - **Rule:** NEVER assume Terraform validity.
   - **Action:** Run `mcp__terraform__terraform_plan` (dry-run).
6. **Final Report:**
   - Produce an "Evidence Report" with explicit citations (e.g., "Verified column `user_id` exists via schema pull", "Zero nulls confirmed via query output"). If any step lacks hard evidence, the verification **FAILS**.
</instructions>

<available_resources>
- Context: `${HOME}/HALLUCINATION_PREVENTION_GUIDE.md`, `${HOME}/errors-and-lessons.md`
- Tools: `get_table_schema`, `run_shell_command`, `codebase_investigator`, `terraform_plan`
</available_resources>
