#!/usr/bin/env python3
"""
Self-improving AI feedback loop.

Three signal sources:
  --source ratings     Option 3: ratings.jsonl (default) — all session failures
  --source pr_review   Option 1: FAILURES dir filtered to PR review sessions
  --source dag         Option 2: FAILURES dir filtered to DAG/Dataform sessions
  --source failures    All FAILURES dir entries

Usage:
  python self_improve.py                            # ratings.jsonl, threshold ≤4
  python self_improve.py --source pr_review         # PR review evals
  python self_improve.py --source dag               # DAG/Dataform evals
  python self_improve.py --dry-run                  # Preview without writing
  python self_improve.py --threshold 3              # Stricter signal filter
  python self_improve.py --no-llm                   # Template lessons only
  python self_improve.py --report                   # Diagnostic report only
  python self_improve.py --min-occurrences 2        # Lower pattern threshold
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from harness_paths import HARNESS_HOME, LESSONS_DIR
from typing import Any, Optional

from state_io import (
    append_jsonl,
    append_jsonl_unlocked,
    atomic_write_text,
    exclusive_lock,
    load_jsonl_objects,
    rewrite_jsonl_unlocked,
)

# ── Paths ────────────────────────────────────────────────────────────────────

RATINGS_FILE  = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/ratings.jsonl"
FAILURES_DIR  = HARNESS_HOME / "MEMORY/LEARNING/FAILURES"
MEMORY_DIR    = LESSONS_DIR
DIAGNOSTICS   = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
LESSONS_LOG   = HARNESS_HOME / "MEMORY/LEARNING/lessons_log.jsonl"
EVAL_CANDIDATES_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/eval_candidates.jsonl"
EFFECTIVENESS_LOG = HARNESS_HOME / "MEMORY/LEARNING/effectiveness_log.jsonl"

# Phase M: cache of earliest historical lesson epochs (never reset on rewrite)
_HIST_EPOCH_CACHE: Optional[dict[str, str]] = None


def _iso_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value[:10]
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def historical_epoch(pattern: str) -> Optional[str]:
    """Earliest lesson_date ever recorded for pattern in effectiveness_log.

    Phase M (2026-07-17): prevents write_lesson_file from resetting first_seen
    to today after lesson wipe/regenerate (the after_n=1 forever bug).
    """
    global _HIST_EPOCH_CACHE
    if _HIST_EPOCH_CACHE is None:
        _HIST_EPOCH_CACHE = {}
        if EFFECTIVENESS_LOG.exists():
            # Stream line by line. read_text() pulled the entire log into memory
            # (379,399,893 bytes / 810,419 lines as of 2026-07-25) on every run, plus
            # a second copy for splitlines(). Only one date per pattern is needed.
            try:
                with EFFECTIVENESS_LOG.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(r, dict):
                            continue
                        p = r.get("pattern")
                        date_value = r.get("lesson_date") or r.get("baseline_date") or ""
                        ld = _iso_date(date_value)
                        if isinstance(p, str) and ld is not None:
                            prev = _HIST_EPOCH_CACHE.get(p)
                            if prev is None or ld < prev:
                                _HIST_EPOCH_CACHE[p] = ld
            except OSError:
                pass
    return _HIST_EPOCH_CACHE.get(pattern)

# ── Domain classifiers (for FAILURES-dir sources) ─────────────────────────────

PR_REVIEW_KEYWORDS = [
    "review", "approved", "approval", "pr #", "pull request",
    "comment", "finding", "inline", "posted", "merge",
]
DAG_KEYWORDS = [
    "dag", "airflow", "dataform", "sqlx", "pipeline", "bronze", "silver",
    "mysql", "bigquery", "bq", "datastream", "cdc", "schema",
]

# ── PR review failure patterns ────────────────────────────────────────────────

PR_PATTERN_KEYWORDS: dict[str, list[str]] = {
    "duplicate_approval": [
        "approved multiple times", "approved four times", "twice",
        "duplicate approval", "approved again", "approved.*already",
        "already approved", "approved twice", "redundant request",
    ],
    "approved_without_verification": [
        "approval status claimed incorrectly", "approved.*errors",
        "ignored errors", "approved pr anyway", "claiming pr approved",
        "pr approval status", "claimed.*approved",
    ],
    "draft_pr_blocked": [
        "draft status", "draft pr", "misread pr state",
        "refused.*draft", "incorrectly claimed",
    ],
    "stale_review": [
        "stale review", "stale diff", "outdated diff",
        "outdated comment", "analyzed outdated", "check revision",
        "without refetching", "variable name confusion",
    ],
    "missed_inline_vs_general": [
        "inline comment", "general comment",
        "should be inline", "referenced non-public",
        "non-public endpoints", "posted comment",
    ],
    "re_raised_resolved": [
        "already resolved", "rereview", "previous review inadequate",
        "re-review", "review inadequate", "missed findings in prior",
        "correction needed.*review", "pointing out potential error in review",
    ],
    "posted_without_approval": [
        "without permission", "acted without", "premature",
        "without asking", "without approval",
        "explicitly forbade", "forbidden this behavior",
        "provided already-merged", "already-merged pr",
    ],
    "wrong_tool_for_review": [
        "don't use ai agents review", "ai agents review mcp",
        "behavioral correction.*mcp", "wrong.*review tool",
        "gemini not claude", "tool misidentification",
    ],
    "missed_context": [
        "downstream pr still failing", "conflict blocking",
        "missed context", "missed.*pr", "failed to check",
    ],
    # promoted 2026-07-09 via pattern_promotion.py (LLM-discovered, human-ratified)
    # NOTE: single-token promotions dropped 2026-07-17 — they fire on almost every
    # PR session (approval/analysis/trust alone). Kept multi-token phrases only.
    "premature_approval_granted": [
        "premature approval", "approval granted too early",
        "approved before analysis", "approves without analysis",
    ],
    "premature_authority_delegation": [
        "premature authority", "delegating approval",
        "authority delegation", "trust signal approves",
    ],
    # Never put "other" in PR_PATTERN_KEYWORDS — healthcheck + promote_to_taxonomy
    # treat that as pollution (2026-07-10 regression). "other" is a residual bucket
    # only, assigned by classify_entry when nothing matches.
    "explicit_instruction_violation": [
        "explicit instruction violation", "ignored instruction",
        "you said not to", "explicitly forbade",
    ],
}

PR_LESSON_TEMPLATES: dict[str, str] = {
    "duplicate_approval": (
        "Run PR approval exactly once. Check `gh pr view` for reviewDecision first. "
        "If already APPROVED, skip — say you will not add a second approval. "
        "Never approve again / just in case / leave another approval."
    ),
    "unhelpful_troubleshooting": (
        "When troubleshooting fails: stop, restate the error, change approach. "
        "Never say 'works fine from here' or blame user quoting without a fresh "
        "repro in the current environment."
    ),
    "explicit_instruction_violation": (
        "When the user forbids a behavior, never do it again in the same or later "
        "turns. Re-read explicit constraints before acting. One violation = failure."
    ),
    "missing_dependency": (
        "After setup, verify imports/deps actually resolve (run the binary). "
        "Do not claim setup complete until a real import/run succeeds."
    ),
    "approved_without_verification": (
        "Never approve a PR when review findings or CI errors are present. "
        "Check `gh pr view --json reviewDecision,statusCheckRollup` before approving."
    ),
    "draft_pr_blocked": (
        "When user explicitly asks to approve, approve. GitHub API can misreport draft state. "
        "Only block on draft when there is NO explicit user instruction to proceed."
    ),
    "stale_review": (
        "Re-fetch the diff with `gh pr diff` immediately before posting any review comment. "
        "Branch may have been force-pushed since initial fetch. Never post from cached diff."
    ),
    "missed_inline_vs_general": (
        "Post findings as inline comments on the specific line, not as general review summary. "
        "Only use general comments when a finding has no specific line anchor."
    ),
    "re_raised_resolved": (
        "Read all existing PR comments before posting findings. "
        "If a concern appears in prior comments and is marked resolved, drop it."
    ),
    "posted_without_approval": (
        "Draft → Show → Ask → Wait → Post. Always show the full comment draft and "
        "ask 'Should I post this?' before submitting any PR comment."
    ),
    "missed_existing_comments": (
        "Read ALL existing PR comments (inline + general) before writing any finding. "
        "Missing one existing comment = review failure = re-review request."
    ),
    "wrong_tool_for_review": (
        "For PR reviews, use the ai-agents MCP (`trigger_review` + `get_review_result`). "
        "Never use a different review tool or spawn a custom pr-reviewer subagent. "
        "Check tool routing in CLAUDE.md before starting any review."
    ),
    "missed_context": (
        "Before concluding a PR fix is complete, verify the downstream PRs and checks. "
        "A merged upstream PR does not guarantee downstream PRs are unblocked. "
        "Check merge conflicts and CI status after every merge."
    ),
}

# ── DAG / Dataform failure patterns ──────────────────────────────────────────

DAG_PATTERN_KEYWORDS: dict[str, list[str]] = {
    "no_dry_run": [
        "dry run", "dry_run", "without validating", "without dry",
        "bq query", "schema check",
    ],
    "wrong_promotion_order": [
        "promotion order", "uat before", "prd before", "wrong environment",
        "skipped stg", "skipped uat",
    ],
    "blind_retry_airflow": [
        "same task", "rerun without", "retried task", "repeated failure",
        "ran again",
    ],
    "trigger_rule_issue": [
        "trigger rule", "upstream task", "task dependency", "before upstream",
        "ran before",
    ],
    "schema_validation_skipped": [
        "schema validation", "without schema", "without checking schema",
        "bq show", "column missing",
    ],
    "dev_data_quality": [
        "dev data", "dev environment", "zero rows", "synthetic data",
        "non-zero", "dev quality",
    ],
    "no_tilt_test": [
        "local tilt", "tilt run", "without tilt", "local test",
        "dag change without",
    ],
}

DAG_LESSON_TEMPLATES: dict[str, str] = {
    "no_dry_run": (
        "For any SQL change, always run `bq query --dry_run` before claiming done. "
        "For schema changes, run `bq show --schema` before and after to verify."
    ),
    "wrong_promotion_order": (
        "Strict promotion order: DEV → STG → UAT → PRD. "
        "Never approve a PRD promotion PR until UAT is verified. "
        "Never merge uat directly to prd — use promotion PR flow."
    ),
    "blind_retry_airflow": (
        "When an Airflow task fails, read the task logs before retrying. "
        "Never trigger a DAG re-run without first understanding the root cause."
    ),
    "trigger_rule_issue": (
        "Before concluding a Dataflow/Airflow job is not running, check `trigger_rule` "
        "on the task and verify all upstream tasks have completed."
    ),
    "schema_validation_skipped": (
        "Before approving any database PR that adds a column, verify the column "
        "exists in BigQuery PRD via `bq show --schema`. Never approve on assumption."
    ),
    "dev_data_quality": (
        "Do not require non-zero row counts or PRD-quality results from DEV runs. "
        "DEV data is synthetic — validate logic correctness, not data completeness."
    ),
    "no_tilt_test": (
        "For DAG file changes, always run a local Tilt test before creating the PR. "
        "Never create a DAG PR without at minimum a dry-run parse verification."
    ),
}

# ── Pattern taxonomy ─────────────────────────────────────────────────────────

PATTERN_KEYWORDS: dict[str, list[str]] = {
    "unverified_completion": [
        "claimed", "claiming done", "marking complete", "marked complete",
        "actually completed", "posted without", "verify work", "re-verification",
        "claiming completion", "complete", "claim complete", "falsely marked",
        "completion claim", "claimed completion",
        "picture is complete", "full picture", "everything is complete",
        "premature completion", "without verifying", "paper trace",
        # 2026-07-10 other-bucket language
        "still not working after fix", "incomplete fix", "fix incomplete",
        "falsely marked complete", "incomplete or inaccurate",
        "questioning completeness", "import fix incomplete",
    ],
    "blind_retry": [
        "repeated mistake", "repeated failure", "repeated action", "don't repeat",
        "multiple times", "same command", "retried", "same approach",
        "repeated the same", "keep retrying",
        "failed troubleshooting", "same failing",
    ],
    "scope_misunderstanding": [
        "misunderstood", "wrong scope", "misread", "scope of request",
        "misidentified", "clarifying actual intent", "wrong approach",
        "misunderstood the instruction", "understood task scope",
        # 2026-07-10 other-bucket language
        "wrong solution", "approach was incorrect", "rejection of proposed",
        "proposed solution", "actual intent", "not what i asked",
        "misunderstood the instruction", "clarifying actual",
    ],
    "acting_without_permission": [
        "without permission", "premature posting", "overstepped",
        "acted without", "auto-merged", "posted without approval",
        "colleague's work",
        # 2026-07-10
        "explicitly forbade", "forbade this behavior", "without asking",
        "did not ask", "posted anyway",
    ],
    "incomplete_analysis": [
        "incomplete analysis", "insufficient investigation", "before responding",
        "overlooked existing", "omission", "missed requirement",
        "research independently", "investigate first",
        "should have investigated", "needs thorough",
        "full picture", "partial read", "ticket scope", "without checking",
        # 2026-07-10 other-bucket language (largest other→here lift)
        "insufficient investigation", "investigation rigor",
        "missing validation", "lessons learned", "unexpected configuration",
        "unhelpful response", "troubleshooting", "questioning unexpected",
        "pointing out potential omission", "corrected omission",
        "missed requirement", "multi-model support", "partial",
        "works fine from here", "false positive", "dismissing",
        "dismissal after failed",
    ],
    "unverified_claims": [
        "hallucinating", "false claim", "inaccurate claim", "false statement",
        "incorrect claim", "inaccurate characterization", "fabricat",
        "incorrect assertion", "incorrect information",
        # 2026-07-10
        "claims no", "despite being configured", "inaccurate usage",
        "false claim about", "made incorrect claim", "incorrect claim",
        "hallucinating ticket",
    ],
    "pr_review_failure": [
        "missed critical", "pr review", "review inadequate", "previous review",
        "re-review", "unaddressed comment", "review missed",
        "missed detecting comments", "unaddressed",
    ],
    "regression_introduction": [
        "regression", "code regression", "regressions while fixing",
        "introduced regression",
        "service broken", "signature mismatch", "causing failures",
        "import error blocking",
    ],
    # NOTE (2026-07-17): removed catch-all "behavioral correction" / bare "don't use".
    # Those meta-labels tagged blind_retry, incomplete_analysis, etc. as tool_misuse,
    # which made the only regressed pattern a false-positive swamp (8/14 sessions
    # had zero tool signal). Keep tool-specific language only.
    "tool_misuse": [
        "tool misidentification", "wrong tool", "incorrect tool",
        "wrong tool choice", "jira tools access", "mcp__jira",
        "mcp__jira-context", "should use acli", "don't use ai agents review",
        "used the wrong tool", "wrong mcp", "forbidden tool",
        "should not use mcp", "incorrect tool choice",
    ],
    "stale_context": [
        "outdated comment", "outdated", "stale", "analyzed outdated",
        "non-public endpoints", "cached",
    ],
    "redundant_recommendation": [
        "existing capability", "already exists", "already implemented",
        "already configured", "already available", "redundant",
    ],
    # DAG/Dataform patterns — also mined from ratings.jsonl
    "no_dry_run_sql": [
        "dry run", "dry_run", "without validating sql", "bq query without",
        "schema check", "without bq show",
    ],
    "wrong_env_promotion": [
        "promotion order", "uat before", "prd before", "wrong environment",
        "skipped stg", "skipped uat", "merge.*prd", "master.*prd",
    ],
    "airflow_blind_retry": [
        "rerun without", "retried task", "trigger rule", "upstream task",
        "task dependency", "ran before upstream",
    ],
    "approved_without_verification": [
        "approved without checking", "approved.*errors", "ignored ci errors",
        "approved pr anyway", "approval.*unverified", "claimed.*approved",
    ],
    # 2026-07-10: patterns that were dumping into 'other'
    "unhelpful_troubleshooting": [
        "unhelpful", "failed troubleshooting", "still not working",
        "null was likely", "quoting issue", "try copying",
        "dismissal after failed troubleshooting",
    ],
    "explicit_instruction_violation": [
        "explicitly forbade", "forbade this behavior", "explicit instruction",
        "repeatedly told", "do not do this", "i said not to",
        "angry — jason", "explicitly forbidden",
    ],
    "missing_dependency": [
        "missing dependency", "dependency after setup", "import error",
        "module not found", "package not installed",
    ],
}

LESSON_TEMPLATES: dict[str, str] = {
    "unverified_completion": (
        "Never claim done/fixed/complete without a STRONG paper trace in the same "
        "response: fenced CLI/test output, exit codes, pass counts next to a runner, "
        "or a live URL. Bare file paths and bare 'N rows/tests' are NOT evidence. "
        "If you cannot fence proof, state what is still unverified — do not say done."
    ),
    "blind_retry": (
        "When an action fails, STOP immediately. Diagnose the root cause before "
        "trying again. Never re-run the exact same failing command. Understand why "
        "it failed, then try a materially different approach."
    ),
    "scope_misunderstanding": (
        "Before executing, confirm scope interpretation. When an instruction has "
        "multiple plausible meanings, state the interpretation chosen and why. "
        "Ambiguity = ask, never silently guess."
    ),
    "acting_without_permission": (
        "Draft → Show → Ask → Wait → Post. Never post, push, or commit without "
        "explicit user approval. Applies to all external writes, comments, and "
        "colleague-owned work."
    ),
    "incomplete_analysis": (
        "Before concluding, agreeing, or dismissing: read ALL relevant context "
        "(full diff, existing PR comments, ticket, related files, CLAUDE.md). "
        "Never say looks-unrelated / you're-right / same-issue / no-changes-needed "
        "without a research trace (I read X, gh pr diff, fenced tool output). "
        "Research first, respond second."
    ),
    "unverified_claims": (
        "Never assert system state without verifying with tools. 'I think', "
        "'probably', 'should be' = stop and verify with CLI/MCP. "
        "Confidence without evidence is hallucination."
    ),
    "pr_review_failure": (
        "Read ALL existing PR comments before posting any finding. "
        "Re-raising a resolved concern is a review failure. "
        "Read the full diff, not just changed lines."
    ),
    "regression_introduction": (
        "Touch only what the task requires. No bonus cleanup, no taste-based changes. "
        "Run tests on every file you modify before claiming done."
    ),
    "tool_misuse": (
        "Check CLAUDE.md tool routing before acting. Wrong tool = wasted work. "
        "When unsure which tool to use, re-read the routing rules first."
    ),
    "stale_context": (
        "Re-fetch current state immediately before acting. Cached or quoted code "
        "from prior turns may be outdated. Always work from the current file state."
    ),
    "redundant_recommendation": (
        "Check existing capabilities before suggesting new ones. "
        "If something already exists, don't recommend it as a solution. "
        "Search and verify before proposing additions."
    ),
    "no_dry_run_sql": (
        "For any SQL or schema change, run `bq query --dry_run` before claiming done. "
        "For schema changes, run `bq show --schema` before and after to verify."
    ),
    "wrong_env_promotion": (
        "Strict promotion order: master → STG → UAT → PRD via promotion PRs. "
        "Never merge directly to UAT or PRD. Never approve a PRD PR until UAT is verified."
    ),
    "airflow_blind_retry": (
        "When an Airflow task fails, read task logs before retrying. "
        "Check trigger_rule and all upstream task states. "
        "Never re-trigger a DAG run without first understanding the root cause."
    ),
    "unhelpful_troubleshooting": (
        "When troubleshooting fails: stop, restate the error, change approach. "
        "Never say 'works fine from here' or blame user quoting without a fresh "
        "repro in the current environment."
    ),
    "explicit_instruction_violation": (
        "When the user forbids a behavior, never do it again in the same or later "
        "turns. Re-read explicit constraints before acting. One violation = failure."
    ),
    "missing_dependency": (
        "After setup, verify imports/deps actually resolve (run the binary). "
        "Do not claim setup complete until a real import/run succeeds."
    ),
}

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class RatingEntry:
    timestamp: str
    rating: int
    session_id: str
    source: str
    sentiment_summary: str
    confidence: float
    response_preview: str
    comment: str
    patterns: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)   # #5 richer signal
    files_touched: list[str] = field(default_factory=list)
    repo: str = ""
    skill: str = ""                                        # primary skill/command in rated turn (attribution)
    skill_candidates: list[str] = field(default_factory=list)  # multi-label (Skill + /cmd + path)
    agent: str = ""                                        # claude | grok | pi | "" (unknown)
    eval_results: dict = field(default_factory=dict)       # binary evals on full response (from capture)


# ── Loading ───────────────────────────────────────────────────────────────────

def load_failures(
    failures_dir: Path,
    domain: str = "all",   # "pr_review" | "dag" | "all"
) -> list[RatingEntry]:
    """Load from FAILURES dir — each entry has a rich CONTEXT.md with full analysis."""
    entries: list[RatingEntry] = []
    if not failures_dir.exists():
        return entries

    for month_dir in sorted(failures_dir.iterdir()):
        if not month_dir.is_dir():
            continue
        for failure_dir in sorted(month_dir.iterdir()):
            if not failure_dir.is_dir():
                continue
            ctx = failure_dir / "CONTEXT.md"
            if not ctx.exists():
                continue

            try:
                content = ctx.read_text(errors="replace")
            except OSError:
                continue
            name = failure_dir.name

            # Domain filter
            name_lower = name.lower()
            is_pr = any(kw in name_lower for kw in ["review", "approved", "approval", "pr-", "comment", "finding", "merge"])
            is_dag = any(kw in name_lower for kw in ["dag", "airflow", "dataform", "sqlx", "pipeline", "bronze", "silver", "mysql", "bq", "bigquery", "schema"])

            if domain == "pr_review" and not is_pr:
                continue
            if domain == "dag" and not is_dag:
                continue

            # Extract rating from CONTEXT.md
            m = re.search(r"rating:\s*(\d+)", content, re.IGNORECASE)
            rating = int(m.group(1)) if m else 3
            if not 1 <= rating <= 10:
                rating = 3

            # Extract summary (description field or summary field)
            m2 = re.search(r"Summary:\s*(.+)", content, re.IGNORECASE)
            summary = m2.group(1).strip()[:120] if m2 else name.replace("-", " ").replace("_", " ")[19:]

            # Extract What Happened section as the rich context
            m3 = re.search(r"## What Happened\s*\n\n(.+?)(?=\n---|\n##)", content, re.DOTALL)
            what_happened = m3.group(1).strip()[:500] if m3 else ""

            entries.append(RatingEntry(
                timestamp=name[:19].replace("-", "T", 2).replace("-", ":"),
                rating=rating,
                session_id=name,
                source="failures_dir",
                sentiment_summary=summary,
                confidence=0.9,
                response_preview=what_happened[:200],
                comment="",
            ))

    return entries


def _string_field(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _string_list_field(record: dict[str, Any], name: str) -> list[str]:
    value = record.get(name)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def load_all_ratings(path: Path) -> list[RatingEntry]:
    """Load valid rating rows while isolating malformed or incompatible records."""
    entries: list[RatingEntry] = []
    for record in load_jsonl_objects(path).records:
        try:
            raw_rating = record["rating"]
            if isinstance(raw_rating, bool):
                continue
            rating = int(raw_rating)
            if float(raw_rating) != rating:
                continue
            confidence = float(record.get("confidence", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= rating <= 10:
            continue
        if not math.isfinite(confidence):
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        eval_results = record.get("eval_results")
        entries.append(
            RatingEntry(
                timestamp=_string_field(record, "timestamp"),
                rating=rating,
                session_id=_string_field(record, "session_id"),
                source=_string_field(record, "source"),
                sentiment_summary=_string_field(record, "sentiment_summary"),
                confidence=confidence,
                response_preview=_string_field(record, "response_preview"),
                comment=_string_field(record, "comment"),
                tools_used=_string_list_field(record, "tools_used"),
                files_touched=_string_list_field(record, "files_touched"),
                repo=_string_field(record, "repo"),
                skill=_string_field(record, "skill"),
                skill_candidates=_string_list_field(record, "skill_candidates"),
                agent=_string_field(record, "agent"),
                eval_results=eval_results if isinstance(eval_results, dict) else {},
            )
        )
    return entries


# ── Classification ────────────────────────────────────────────────────────────

# Persistent reclass labels from judge_outcomes --reclass-other (session|ts → patterns)
OTHER_RECLASS_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/other_reclass.jsonl"
_RECLASS_CACHE: dict[str, list[str]] | None = None


def _load_other_reclass() -> dict[str, list[str]]:
    """Map 'session_id|timestamp' → patterns from judge/LLM reclass.

    Only exact session|timestamp keys — session-only keys collide across turns.
    """
    global _RECLASS_CACHE
    if _RECLASS_CACHE is not None:
        return _RECLASS_CACHE
    out: dict[str, list[str]] = {}
    known_patterns = set(PATTERN_KEYWORDS) | set(PR_PATTERN_KEYWORDS) | set(DAG_PATTERN_KEYWORDS)
    for record in load_jsonl_objects(OTHER_RECLASS_FILE).records:
        patterns = record.get("patterns")
        if not isinstance(patterns, list):
            continue
        normalized = [
            pattern
            for pattern in patterns
            if isinstance(pattern, str) and pattern in known_patterns
        ]
        session_id = record.get("session_id")
        timestamp = record.get("timestamp")
        if normalized and isinstance(session_id, str) and session_id and isinstance(timestamp, str) and timestamp:
            out[f"{session_id}|{timestamp}"] = normalized
    _RECLASS_CACHE = out
    return out


def rating_entry_key(entry: RatingEntry) -> str:
    session_id = getattr(entry, "session_id", "")
    timestamp = getattr(entry, "timestamp", "")
    session_id = session_id if isinstance(session_id, str) else ""
    timestamp = timestamp if isinstance(timestamp, str) else ""
    if session_id and timestamp:
        return f"{session_id}|{timestamp}"
    if timestamp:
        return f"timestamp|{timestamp}"
    return session_id


def classify_entry(entry: RatingEntry) -> list[str]:
    """Classify a rating into failure pattern(s).

    2026-07-10: use response_preview + tools + skill (not just sentiment summary).
    55% of low ratings were 'other' because classify ignored the rated response text
    and keyword set missed common frustration language.
    """
    # 1) Prefer durable judge reclass labels (exact turn key only)
    reclass = _load_other_reclass()
    key = rating_entry_key(entry)
    if key and key in reclass:
        return reclass[key]

    # 2) Keyword match on full available text
    parts = [
        entry.sentiment_summary or "",
        entry.comment or "",
        entry.response_preview or "",
        entry.skill or "",
        " ".join(entry.tools_used or []),
        " ".join(entry.files_touched or []),
        entry.repo or "",
    ]
    text = " ".join(parts).lower()
    matched = [p for p, kws in PATTERN_KEYWORDS.items() if any(kw in text for kw in kws)]

    # 3) Eval failures on the rated response are hard objective labels
    if entry.eval_results:
        for _eid, res in entry.eval_results.items():
            if isinstance(res, dict) and res.get("passed") is False and res.get("pattern"):
                pat = res["pattern"]
                if pat not in matched:
                    matched.append(pat)

    return matched or ["other"]


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _apply_pai_settings_env() -> None:
    """settings.json PAI_* wins over stale process env (Grok long-lived sessions)."""
    try:
        settings_path = HARNESS_HOME / "settings.json"
        if not settings_path.is_file():
            return
        data = json.loads(settings_path.read_text())
        env = data.get("env") or {}
        if not isinstance(env, dict):
            return
        for k, v in env.items():
            if isinstance(v, str) and (k.startswith("PAI_") or k == "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
                os.environ[k] = v
    except Exception:
        pass


LAST_LLM_PROVIDER: Optional[str] = None
LAST_LLM_ERROR: Optional[str] = None

_PROVIDER_ALIASES = {
    "gemini": "gemini",
    "google": "gemini",
    "vertex": "gemini",
    "flash": "gemini",
    "opencode": "opencode",
    "haiku": "haiku",
    "anthropic": "haiku",
    "claude": "haiku",
}

def call_llm(
    prompt: str,
    model: str = "",
    max_tokens: int = 512,
    system: str = "",
) -> Optional[str]:
    """Background LLM for self-improve / judge / evolve / Inference fast tier."""
    global LAST_LLM_ERROR, LAST_LLM_PROVIDER
    import signal

    _apply_pai_settings_env()
    LAST_LLM_PROVIDER = None
    LAST_LLM_ERROR = None

    if os.environ.get("PAI_SELF_IMPROVE_LLM_DISABLED") == "1":
        LAST_LLM_ERROR = "disabled by PAI_SELF_IMPROVE_LLM_DISABLED"
        return None

    try:
        max_tokens = max(64, min(int(max_tokens), 16_384))
    except (TypeError, ValueError):
        max_tokens = 512

    configured = (os.environ.get("PAI_BACKGROUND_LLM_PROVIDER") or "gemini").strip().lower()
    provider = _PROVIDER_ALIASES.get(configured)
    if provider is None:
        LAST_LLM_ERROR = f"unknown background LLM provider: {configured or '(empty)'}"
        return None

    if provider == "haiku" and os.environ.get("PAI_HAIKU_BACKGROUND_DISABLED") == "1":
        provider = "gemini"
    # Never use Anthropic for background self-improve when headless Claude is off
    # (Grok primary / stop Sonnet burn). Force Vertex Gemini.
    headless_claude_off = (
        os.environ.get("PAI_CLAUDE_HEADLESS_DISABLED") == "1"
        or os.environ.get("GROK_AGENT") == "1"
    )
    if headless_claude_off:
        # Only override to gemini when provider is an Anthropic model.
        # Don't clobber opencode (separate non-Anthropic provider).
        if provider == "haiku":
            provider = "gemini"

    def _on_timeout(signum, frame):
        raise TimeoutError("LLM call exceeded hard timeout")

    old_handler = None
    try:
        try:
            old_handler = signal.signal(signal.SIGALRM, _on_timeout)
            signal.alarm(30)
        except (ValueError, AttributeError):
            old_handler = None

        if provider == "gemini":
            res = _call_llm_gemini(prompt, model=model, max_tokens=max_tokens, system=system)
            if res:
                LAST_LLM_PROVIDER = "gemini"
            else:
                LAST_LLM_ERROR = "gemini returned no content"
            return res
        if provider == "opencode":
            res = _call_llm_opencode(prompt, model=model, max_tokens=max_tokens, system=system)
            if res:
                LAST_LLM_PROVIDER = "opencode"
            else:
                LAST_LLM_ERROR = "opencode returned no content"
            return res
        res = _call_llm_haiku(prompt, model=model, max_tokens=max_tokens, system=system)
        if res:
            LAST_LLM_PROVIDER = "haiku"
        else:
            LAST_LLM_ERROR = "haiku returned no content"
        return res
    except Exception as exc:
        LAST_LLM_ERROR = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        try:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except Exception:
            pass
    return None


def _call_llm_gemini(
    prompt: str,
    model: str = "",
    max_tokens: int = 512,
    system: str = "",
) -> Optional[str]:
    """Cheap Vertex Gemini path (default for background self-improve / rating capture)."""
    m = model or os.environ.get("PAI_BACKGROUND_LLM_MODEL") or "gemini-2.5-flash"
    if "gemini" not in m.lower():
        m = "gemini-2.5-flash"
    project = (
        os.environ.get("PAI_BACKGROUND_LLM_PROJECT")
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        or os.environ.get("VERTEX_AI_PROJECT")
    )
    if not project:
        return None
    from google import genai
    from google.genai import types
    # Prefer PAI_BACKGROUND_LLM_LOCATION. Default global: gemini-3.1-flash-lite is
    # available on Vertex global for this project (404 on us-central1). Do not
    # blindly use CLOUD_ML_REGION from Claude Code Anthropic routing.
    location = (
        os.environ.get("PAI_BACKGROUND_LLM_LOCATION")
        or os.environ.get("VERTEX_AI_LOCATION")
        or "global"
    )
    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(timeout=20_000),
    )
    # thinking_budget=0 keeps flash cheap (no hidden thinking tokens).
    cfg_kwargs: dict = {
        "max_output_tokens": max(max_tokens, 64),
        "temperature": 0,
        "thinking_config": types.ThinkingConfig(thinking_budget=0),
    }
    if system:
        cfg_kwargs["system_instruction"] = system
    resp = client.models.generate_content(
        model=m,
        contents=prompt,
        config=types.GenerateContentConfig(**cfg_kwargs),
    )
    text = (getattr(resp, "text", None) or "").strip()
    return text or None


def _call_llm_opencode(
    prompt: str,
    model: str = "",
    max_tokens: int = 512,
    system: str = "",
) -> Optional[str]:
    """OpenCode provider via OpenAI-compatible API."""
    from openai import OpenAI

    m = model or os.environ.get("PAI_BACKGROUND_LLM_MODEL") or "deepseek-v4-flash"
    oai_token = os.environ.get("PAI_OPENAI_" + "API_" + "KEY")
    base_url = os.environ.get("PAI_OPENAI_BASE_URL")
    if not oai_token or not base_url:
        return None
    # Ensure max_tokens is at least 128 for reasoning models
    max_tokens = max(max_tokens, 128)
    client = OpenAI(api_key=oai_token, base_url=base_url)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=m,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0,
        timeout=20,
    )
    text = (resp.choices[0].message.content or "").strip()
    return text or None


def _call_llm_haiku(
    prompt: str,
    model: str = "",
    max_tokens: int = 512,
    system: str = "",
) -> Optional[str]:
    """Legacy Anthropic Haiku path (expensive; only if PAI_BACKGROUND_LLM_PROVIDER=haiku)."""
    project = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
    if not project:
        return None
    from anthropic import AnthropicVertex  # type: ignore[import-not-found]

    m = model or os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5@20251001")
    kwargs: dict = {
        "project_id": project,
        "region": os.environ.get("CLOUD_ML_REGION")
                  or os.environ.get("ANTHROPIC_VERTEX_REGION", "us-east5"),
        "timeout": 20.0,
        "max_retries": 1,
    }
    base = os.environ.get("ANTHROPIC_VERTEX_BASE_URL")
    if base:
        kwargs["base_url"] = base
    client = AnthropicVertex(**kwargs)
    messages = [{"role": "user", "content": prompt}]
    create_kwargs: dict = {"model": m, "max_tokens": max_tokens, "messages": messages}
    if system:
        create_kwargs["system"] = system
    msg = client.messages.create(**create_kwargs)
    return msg.content[0].text.strip()


def generate_lesson_llm(pattern: str, examples: list[RatingEntry]) -> Optional[str]:
    ex_lines = "\n".join(
        f"- [{e.rating}/10] {e.sentiment_summary}"
        + (f" | {e.comment}" if e.comment else "")
        for e in sorted(examples, key=lambda x: x.rating)[:8]
    )
    prompt = (
        f"Analyze these AI assistant failures (category: {pattern.replace('_', ' ')}) "
        f"and write ONE specific, actionable prevention rule (1-2 sentences). "
        f"Lead with what NOT to do, then what TO do instead. No headers or bullets.\n\n"
        f"Failures:\n{ex_lines}"
    )
    return call_llm(prompt)


def _parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    """Defensively extract a JSON object from an LLM reply."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _normalized_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:maximum]


def normalize_eval_candidate(pattern: str, value: Any) -> Optional[dict[str, str]]:
    if not isinstance(value, dict):
        return None
    eval_id = _normalized_text(value.get("id"), maximum=80)
    predicate = _normalized_text(value.get("predicate"), maximum=500)
    eval_pattern = _normalized_text(value.get("pattern"), maximum=100)
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", eval_id)
        or len(predicate) < 12
        or eval_pattern != pattern
    ):
        return None
    return {"id": eval_id, "predicate": predicate, "pattern": pattern}


def normalize_structured_lesson(
    value: Any,
    pattern: str,
) -> Optional[dict[str, Any]]:
    """Validate and normalize the model-authored lesson and optional eval candidate."""
    if not isinstance(value, dict):
        return None
    instruction = _normalized_text(value.get("instruction"), maximum=800)
    if not instruction:
        return None
    normalized: dict[str, Any] = {
        "instruction": instruction,
        "root_cause": _normalized_text(value.get("root_cause"), maximum=500),
        "what_went_wrong": _normalized_text(value.get("what_went_wrong"), maximum=500),
    }
    suggested = normalize_eval_candidate(pattern, value.get("suggested_eval"))
    if suggested is not None:
        normalized["suggested_eval"] = suggested
    return normalized


def generate_lesson_structured(pattern: str, examples: list[RatingEntry]) -> Optional[dict]:
    """LLM diagnostic → {root_cause, what_went_wrong, instruction, suggested_eval}.

    Mirrors the MindStudio diagnostic shape but adds a suggested binary eval (plain
    English) that feeds the eval-suite growth pipeline. Returns None if the LLM is
    unavailable or the reply isn't parseable — caller falls back to the template.
    """
    ex_lines = "\n".join(
        f"- [{e.rating}/10] {e.sentiment_summary}" + (f" | {e.comment}" if e.comment else "")
        for e in sorted(examples, key=lambda x: x.rating)[:8]
    )
    prompt = (
        f"You are reviewing recurring failures of an AI coding assistant "
        f"(category: {pattern.replace('_', ' ')}).\n\n"
        f"Failures:\n{ex_lines}\n\n"
        "Return STRICT JSON only (no prose, no code fence) with exactly these fields:\n"
        "{\n"
        '  "root_cause": "one sentence: why this keeps happening",\n'
        '  "what_went_wrong": "one sentence: what the assistant actually did",\n'
        '  "instruction": "one imperative sentence to follow next time; lead with what NOT '
        'to do, then what to do",\n'
        '  "suggested_eval": {"id": "snake_case_id", "predicate": "a binary, '
        'mechanically-checkable assertion in plain English that would catch this failure by '
        'inspecting the response text", "pattern": "' + pattern + '"}\n'
        "}"
    )
    raw = call_llm(prompt)
    return normalize_structured_lesson(_parse_json_object(raw), pattern) if raw else None


def append_eval_candidate(pattern: str, suggested: Any, dry_run: bool = False) -> bool:
    """Record an LLM-suggested binary eval for human ratification.

    SAFETY: the predicate is plain English and is NEVER executed. A human ratifies a
    candidate by hand-coding it into evals.py EVALS — that is how the suite grows.
    """
    candidate = normalize_eval_candidate(pattern, suggested)
    if dry_run or candidate is None:
        return False
    rec = {
        "proposed": datetime.now().strftime("%Y-%m-%d"),
        **candidate,
        "status": "proposed",
    }
    with exclusive_lock(EVAL_CANDIDATES_FILE):
        existing = load_jsonl_objects(EVAL_CANDIDATES_FILE).records
        if any(
            record.get("id") == candidate["id"]
            or (
                record.get("pattern") == pattern
                and record.get("predicate") == candidate["predicate"]
            )
            for record in existing
        ):
            return False
        append_jsonl_unlocked(EVAL_CANDIDATES_FILE, rec)
    return True


def classify_other_llm(entries: list[RatingEntry], batch_size: int = 20) -> dict[str, str]:
    """Ask LLM to label 'other' entries into the KNOWN taxonomy (or keep other).

    2026-07-10: constrained to PATTERN_KEYWORDS ids so labels feed lessons/evals.
    Includes response_preview snippet for alignment. Persists to other_reclass.jsonl.
    """
    if not entries:
        return {}
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    known = sorted(PATTERN_KEYWORDS.keys())
    known_set = set(known)
    result: dict[str, str] = {}
    for start in range(0, len(entries), batch_size):
        batch = entries[start:start + batch_size]
        lines = []
        for i, e in enumerate(batch):
            prev = (e.response_preview or "").replace("\n", " ")[:180]
            summ = (e.sentiment_summary or e.comment or "")[:120]
            lines.append(f"{i}: SUMMARY={summ} | RESPONSE={prev}")
        prompt = (
            "Classify each AI assistant failure into EXACTLY one pattern id from this list:\n"
            + ", ".join(known)
            + ", other\n"
            "Use 'other' only if none fit. Reply with one line per entry: INDEX: pattern_id\n\n"
            + "\n".join(lines)
        )
        raw = call_llm(prompt, max_tokens=400, system="You label failures. Only use listed pattern ids.")
        if not raw:
            continue
        for line in raw.splitlines():
            m = re.match(r"^(\d+):\s*([a-z_]+)", line.strip())
            if not m:
                continue
            idx = int(m.group(1))
            pat = m.group(2)
            if idx >= len(batch):
                continue
            if pat not in known_set and pat != "other":
                pat = "other"
            e = batch[idx]
            # Key by session|timestamp — session_id alone collides across turns
            key = rating_entry_key(e)
            if not key:
                continue
            result[key] = pat
            # Persist durable reclass (skip pure other)
            if pat != "other":
                _append_other_reclass(e, [pat], source="classify_other_llm")
    return result


def _append_other_reclass(entry: RatingEntry, patterns: list[str], source: str) -> bool:
    key = rating_entry_key(entry)
    known_patterns = set(PATTERN_KEYWORDS) | set(PR_PATTERN_KEYWORDS) | set(DAG_PATTERN_KEYWORDS)
    normalized = list(dict.fromkeys(pattern for pattern in patterns if pattern in known_patterns))
    if not key or not normalized:
        return False
    rec = {
        "timestamp": entry.timestamp,
        "session_id": entry.session_id,
        "patterns": normalized,
        "source": source,
        "rating": entry.rating,
        "summary": (entry.sentiment_summary or "")[:160],
    }
    with exclusive_lock(OTHER_RECLASS_FILE):
        existing = load_jsonl_objects(OTHER_RECLASS_FILE).records
        retained = [
            record
            for record in existing
            if not (
                record.get("session_id") == entry.session_id
                and record.get("timestamp") == entry.timestamp
            )
        ]
        rewrite_jsonl_unlocked(OTHER_RECLASS_FILE, [*retained, rec])
    global _RECLASS_CACHE
    _RECLASS_CACHE = None  # invalidate
    return True


# ── Memory writing ────────────────────────────────────────────────────────────

def _existing_rule_and_date(filepath: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (core rule text, last_updated, first_seen) from an existing lesson, or Nones.

    The 'core rule' is the first body paragraph (the prevention rule itself), excluding
    the volatile Why/evidence/counts that change every run. Used to decide whether the
    lesson MATERIALLY changed.

    first_seen anchors effectiveness before/after measurement — must NOT reset when the
    rule is rewritten (2026-07-09 bug: evolve → last_updated=today → after_n=0 forever).
    """
    if not filepath.exists():
        return None, None, None
    txt = filepath.read_text()
    m_last = re.search(r"^\s*last_updated:\s*(\d{4}-\d{2}-\d{2})", txt, re.M)
    m_first = re.search(r"^\s*first_seen:\s*(\d{4}-\d{2}-\d{2})", txt, re.M)
    last = _iso_date(m_last.group(1)) if m_last else None
    first = _iso_date(m_first.group(1)) if m_first else last
    parts = txt.split("---", 2)             # ['', frontmatter, body]
    body = (parts[2] if len(parts) >= 3 else txt).lstrip("\n")
    rule = body.split("\n\n", 1)[0].strip()  # first paragraph = the rule
    return rule, last, first


def validate_lesson_format(content: str) -> bool:
    """Validate autogenerated lesson formatting for structural correctness (Point 3: Mutation Validator)"""
    # 1. Check for at least two YAML delimiters
    yaml_delimiter_count = content.count("---")
    if yaml_delimiter_count < 2:
        print("[validation-error] Missing YAML frontmatter delimiters.")
        return False

    # 2. Check that the frontmatter contains required keys
    frontmatter_text = content.split("---", 2)[1]
    required_keys = ["name:", "description:", "metadata:"]
    for key in required_keys:
        if key not in frontmatter_text:
            print(f"[validation-error] Frontmatter missing required key '{key}'")
            return False

    # 3. Check for balanced backticks/code fences in body
    body_text = content.split("---", 2)[2] if yaml_delimiter_count >= 2 else content
    fence_count = body_text.count("```")
    if fence_count % 2 != 0:
        print(f"[validation-error] Unbalanced backticks/code fences in lesson body (count: {fence_count})")
        return False

    return True


def _write_lesson_file_unlocked(
    pattern: str,
    lesson: str,
    examples: list[RatingEntry],
    memory_dir: Path,
    dry_run: bool = False,
    structured: Optional[dict] = None,
) -> Optional[Path]:
    filename = f"lesson_autogen_{pattern}.md"
    filepath = memory_dir / filename

    avg_r = sum(e.rating for e in examples) / len(examples)
    today = datetime.now().strftime("%Y-%m-%d")
    # first_seen / baseline_date: immutable MEASUREMENT epoch (never reset when
    # content is reinforced). last_updated + content_version: content epoch only.
    # Phase M: also never reset below historical effectiveness_log epoch after wipe.
    prev_rule, prev_last, prev_first = _existing_rule_and_date(filepath)
    hist = historical_epoch(pattern)
    candidates = [d for d in (prev_first, prev_last, hist) if d]
    first_seen = min(candidates) if candidates else today
    material = not (prev_rule and prev_rule == lesson.strip())
    last_updated = today if material else (prev_last or today)
    content_version = hashlib.sha256(lesson.strip().encode()).hexdigest()[:10]
    bullets = "\n".join(
        f"- [{e.rating}] {e.sentiment_summary}"
        for e in sorted(examples, key=lambda x: x.rating)[:5]
    )

    # #5 richer signal: surface where this pattern concentrates (repo × tool).
    # Empty for pre-enrichment data → line omitted (backward compatible).
    repos = Counter(e.repo for e in examples if e.repo)
    tools = Counter(t for e in examples for t in e.tools_used)
    skills = Counter(e.skill for e in examples if e.skill)
    ctx_bits = []
    if repos:
        ctx_bits.append("repos: " + ", ".join(f"{r} (×{n})" for r, n in repos.most_common(3)))
    if tools:
        ctx_bits.append("tools: " + ", ".join(f"{t} (×{n})" for t, n in tools.most_common(3)))
    if skills:
        ctx_bits.append("skills: " + ", ".join(f"/{s} (×{n})" for s, n in skills.most_common(3)))
    ctx_line = ("**Where it happens:** " + "; ".join(ctx_bits) + "\n\n") if ctx_bits else ""

    # Structured LLM diagnostic (optional). Empty when --no-llm / LLM unavailable →
    # lesson format is identical to the template path (backward compatible).
    diag = ""
    if structured:
        bits = []
        rc = (structured.get("root_cause") or "").strip()
        ww = (structured.get("what_went_wrong") or "").strip()
        if rc:
            bits.append(f"**Root cause:** {rc}")
        if ww:
            bits.append(f"**What went wrong:** {ww}")
        if bits:
            diag = "\n\n".join(bits) + "\n\n"

    content = f"""---
name: lesson-autogen-{pattern}
description: Auto-generated from {len(examples)} low-rated sessions (avg {avg_r:.1f}) — {pattern.replace('_', ' ')}
metadata:
  type: feedback
  auto_generated: true
  pattern: {pattern}
  occurrence_count: {len(examples)}
  avg_rating: {avg_r:.2f}
  first_seen: {first_seen}
  baseline_date: {first_seen}
  content_version: {content_version}
  last_updated: {last_updated}
---

{lesson}

{diag}**Why:** Occurred {len(examples)} times in ratings data with avg rating {avg_r:.1f}/10.

{ctx_line}**How to apply:** Before every response where {pattern.replace('_', ' ')} could occur, check this rule.

**Evidence from ratings:**
{bullets}
"""

    if not validate_lesson_format(content):
        print(
            f"[validation-error] Deferred writing lesson file '{filepath.name}' "
            "due to failed format validation"
        )
        return None
    if not dry_run:
        atomic_write_text(filepath, content)
    return filepath


def write_lesson_file(
    pattern: str,
    lesson: str,
    examples: list[RatingEntry],
    memory_dir: Path,
    dry_run: bool = False,
    structured: Optional[dict] = None,
) -> Optional[Path]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", pattern):
        print(f"[validation-error] Invalid lesson pattern identifier: {pattern!r}")
        return None
    if not examples:
        print(f"[validation-error] No evidence rows for lesson '{pattern}'")
        return None
    filepath = memory_dir / f"lesson_autogen_{pattern}.md"
    normalized_structured = (
        normalize_structured_lesson(structured, pattern)
        if structured is not None
        else None
    )
    if dry_run:
        return _write_lesson_file_unlocked(
            pattern,
            lesson,
            examples,
            memory_dir,
            dry_run=True,
            structured=normalized_structured,
        )
    with exclusive_lock(filepath):
        return _write_lesson_file_unlocked(
            pattern,
            lesson,
            examples,
            memory_dir,
            structured=normalized_structured,
        )


def update_memory_index(
    new_entries: list[tuple[str, Path]],
    memory_dir: Path,
    dry_run: bool = False,
) -> int:
    index_path = memory_dir / "MEMORY.md"

    def update() -> int:
        if not index_path.exists():
            return 0
        content = index_path.read_text()
        # consolidate_memory.py owns the autogen index region once collapsed — don't
        # re-add individual lines (it regenerates one compact line from disk).
        if "[Auto-lessons (" in content:
            return 0
        added = 0
        for pattern, filepath in new_entries:
            if filepath.name not in content:
                line = (
                    f"- [Auto-lesson: {pattern.replace('_', ' ')}]({filepath.name}) "
                    "— auto-generated from ratings data\n"
                )
                content += line
                added += 1
        if not dry_run and added > 0:
            atomic_write_text(index_path, content)
        return added

    if dry_run:
        return update()
    with exclusive_lock(index_path):
        return update()


# ── Diagnostic report ─────────────────────────────────────────────────────────

def generate_report(
    all_entries: list[RatingEntry],
    low_entries: list[RatingEntry],
    pattern_data: dict,
    report_dir: Path,
    dry_run: bool = False,
    threshold: int = 4,
) -> Path:
    now = datetime.now()
    report_path = report_dir / f"diagnostic_{now.strftime('%Y-%m-%d')}.md"

    total = len(all_entries)
    low_n = len(low_entries)
    avg_all = sum(e.rating for e in all_entries) / total if total else 0

    dist = Counter(e.rating for e in all_entries)
    dist_str = "  ".join(f"{r}★:{dist[r]}" for r in sorted(dist))

    rows = "\n".join(
        f"| {p} | {d['count']} | {(d['count']/low_n*100) if low_n else 0:.0f}% "
        f"| {d['avg_rating']:.1f} | {d['action']} |"
        for p, d in sorted(pattern_data.items(), key=lambda x: -x[1]["count"])
    )

    top_sections = []
    for p, d in sorted(pattern_data.items(), key=lambda x: -x[1]["count"])[:6]:
        if p == "other":
            continue
        exx = "\n".join(f"- [{e.rating}] {e.sentiment_summary}" for e in d["examples"][:3])
        top_sections.append(f"### {p.replace('_', ' ').title()}\n{exx}\n")

    # Skill-failure concentration: which skills/commands the low-rated turns ran.
    # Drives the (future) skill-improvement layer; empty for pre-attribution data.
    skill_low = Counter(e.skill for e in low_entries if e.skill)
    skill_all = Counter(e.skill for e in all_entries if e.skill)
    if skill_low:
        skill_rows = "\n".join(
            f"| /{s} | {n} | {skill_all[s]} | "
            f"{sum(e.rating for e in low_entries if e.skill == s)/n:.1f} |"
            for s, n in skill_low.most_common(10)
        )
        skill_section = (
            "## Skill Failure Concentration\n\n"
            "Which skills/commands the low-rated turns invoked. High low-count + low "
            "avg = candidate for a skill-level fix, not just a behavioral lesson.\n\n"
            "| Skill | Low-rated | Total runs | Avg (low) |\n"
            "|-------|-----------|------------|----------|\n"
            f"{skill_rows}\n\n"
        )
    else:
        skill_section = (
            "## Skill Failure Concentration\n\n"
            "No skill attribution yet — accumulating sessions since attribution was added.\n\n"
        )

    content = f"""# Self-Improvement Diagnostic — {now.strftime('%Y-%m-%d %H:%M')}

## Signal Overview

| Metric | Value |
|--------|-------|
| Total rated entries | {total} |
| Low-rated (≤{threshold} threshold) | {low_n} ({(low_n/total*100) if total else 0:.0f}%) |
| Overall avg rating | {avg_all:.2f}/10 |
| Rating distribution | {dist_str} |

## Pattern Distribution

| Pattern | Count | % of Low | Avg Rating | Action |
|---------|-------|----------|------------|--------|
{rows}

{skill_section}## Top Failure Signatures

{"".join(top_sections)}
## Lessons Log

See `lessons_log.jsonl` for per-run history.
"""
    if not dry_run:
        atomic_write_text(report_path, content)
    return report_path


# ── Run logging ───────────────────────────────────────────────────────────────

def log_run(pattern_data: dict, lessons_log: Path):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "patterns": {
            p: {"count": d["count"], "avg_rating": round(d["avg_rating"], 2), "action": d["action"]}
            for p, d in pattern_data.items()
        },
    }
    append_jsonl(lessons_log, entry)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Self-improving AI feedback loop")
    ap.add_argument("--source",          default="ratings",
                    choices=["ratings", "pr_review", "dag", "failures"],
                    help="Signal source: ratings (default), pr_review, dag, failures")
    ap.add_argument("--dry-run", action="store_true",  help="Preview without writing files")
    ap.add_argument("--threshold",       type=int, default=4, help="Max rating to include (default: 4)")
    ap.add_argument("--min-occurrences", type=int, default=2, help="Min pattern hits for lesson (default: 2)")
    ap.add_argument("--no-llm",          action="store_true", help="Use template lessons only")
    ap.add_argument("--report",          action="store_true", help="Diagnostic report only, no writes")
    ap.add_argument("--classify-other",  action="store_true", help="LLM-classify 'other' entries too")
    args = ap.parse_args(argv)
    if not 1 <= args.threshold <= 10:
        ap.error("--threshold must be between 1 and 10")
    if args.min_occurrences <= 0:
        ap.error("--min-occurrences must be positive")

    # ── Pick source ───────────────────────────────────────────────────────────
    if args.source == "ratings":
        print("[self_improve] Source: ratings.jsonl")
        all_entries = load_all_ratings(RATINGS_FILE)
        low = [e for e in all_entries if e.rating <= args.threshold]
        active_patterns   = PATTERN_KEYWORDS
        active_templates  = LESSON_TEMPLATES
    else:
        domain = args.source if args.source != "failures" else "all"
        label  = {"pr_review": "PR review", "dag": "DAG/Dataform", "failures": "all failures"}[args.source]
        print(f"[self_improve] Source: FAILURES dir ({label})")
        low = load_failures(FAILURES_DIR, domain=domain)
        all_entries = low  # FAILURES entries are all already low-rated
        if args.source == "pr_review":
            active_patterns  = PR_PATTERN_KEYWORDS
            active_templates = PR_LESSON_TEMPLATES
        elif args.source == "dag":
            active_patterns  = DAG_PATTERN_KEYWORDS
            active_templates = DAG_LESSON_TEMPLATES
        else:
            active_patterns  = {**PATTERN_KEYWORDS, **PR_PATTERN_KEYWORDS, **DAG_PATTERN_KEYWORDS}
            active_templates = {**LESSON_TEMPLATES, **PR_LESSON_TEMPLATES, **DAG_LESSON_TEMPLATES}

    print(f"[self_improve] {len(all_entries)} total | {len(low)} in scope")

    # Classify — use source-specific pattern taxonomy
    def classify_for_source(entry: RatingEntry) -> list[str]:
        text = (entry.sentiment_summary + " " + entry.comment + " " + entry.response_preview).lower()
        matched = [p for p, kws in active_patterns.items() if any(kw in text for kw in kws)]
        return matched or ["other"]

    for e in low:
        e.patterns = classify_entry(e) if args.source == "ratings" else classify_for_source(e)

    # Optional: LLM-classify 'other' bucket
    # NOTE: intentionally independent of --no-llm. --no-llm only disables the
    # per-pattern LESSON-GENERATION LLM call (cost/latency at session-end); the
    # classify_other_llm() call is a single cheap Haiku pass, explicitly opted
    # into via --classify-other, and must not be silently gated by the
    # unrelated --no-llm flag (that gating made --classify-other structurally
    # dead in the claude-session-end hook, which always passes --no-llm).
    if args.classify_other:
        other_entries = [e for e in low if e.patterns == ["other"]]
        print(f"[self_improve] LLM-classifying {len(other_entries)} 'other' entries...")
        label_map = classify_other_llm(other_entries)
        for e in other_entries:
            key = rating_entry_key(e)
            if key and label_map.get(key) not in {None, "other"}:
                e.patterns = [label_map[key]]

    # Group
    pattern_groups: dict[str, list[RatingEntry]] = defaultdict(list)
    for e in low:
        for p in e.patterns:
            pattern_groups[p].append(e)

    pattern_data: dict[str, dict] = {
        p: {
            "count": len(exs),
            "avg_rating": sum(e.rating for e in exs) / len(exs),
            "examples": exs,
            "action": "pending",
        }
        for p, exs in sorted(pattern_groups.items(), key=lambda x: -len(x[1]))
    }

    if args.report:
        rpt = generate_report(
            all_entries,
            low,
            pattern_data,
            DIAGNOSTICS,
            args.dry_run,
            threshold=args.threshold,
        )
        print(f"[self_improve] Report → {rpt}")
        return 0

    # Generate lessons
    if not args.dry_run:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)

    new_files: list[tuple[str, Path]] = []

    for p, data in pattern_data.items():
        if p == "other":
            data["action"] = "skip_unclassified"
            continue
        if data["count"] < args.min_occurrences:
            data["action"] = f"skip_count_{data['count']}"
            print(f"  {p}: {data['count']} hits < {args.min_occurrences} threshold, skip")
            continue

        print(f"  {p}: {data['count']} hits, avg {data['avg_rating']:.1f} → generating lesson...")

        lesson = None
        structured = None
        if not args.no_llm:
            structured = generate_lesson_structured(p, data["examples"])
            if structured:
                lesson = structured["instruction"]
                print(f"    [llm] {lesson[:80]}...")

        if not lesson:
            # Prefer hand templates; else ACE Reflector distill (never the old
            # "Avoid X — verify before acting" stub — that polluted the playbook).
            lesson = active_templates.get(p)
            if not lesson:
                try:
                    sys.path.insert(0, str(HARNESS_HOME / "MEMORY/LEARNING"))
                    from ace_reflector import fallback_rule_from_examples
                    lesson = fallback_rule_from_examples(p, data["examples"])
                    print(f"    [reflector] {lesson[:80]}...")
                except Exception as exc:
                    phrase = p.replace("_", " ")
                    lesson = (
                        f"When {phrase} risk appears: stop, gather tool evidence, "
                        f"then act. Do not proceed on memory alone."
                    )
                    print(f"    [heuristic-fallback after {exc}] {lesson[:80]}...")
            else:
                print(f"    [template] {lesson[:80]}...")

        filepath = write_lesson_file(
            p,
            lesson,
            data["examples"],
            MEMORY_DIR,
            args.dry_run,
            structured=structured,
        )
        if filepath is None:
            data["action"] = "validation_failed"
            continue
        tag = "[DRY RUN] would write" if args.dry_run else "written"
        print(f"    → {tag}: {filepath.name}")

        # LLM-suggested binary eval → candidate for human ratification (NEVER auto-exec'd).
        if structured and structured.get("suggested_eval"):
            added_candidate = append_eval_candidate(
                p,
                structured["suggested_eval"],
                args.dry_run,
            )
            if added_candidate:
                print(f"    → eval candidate logged: {structured['suggested_eval'].get('id', '')}")

        data["action"] = "lesson_previewed" if args.dry_run else "lesson_written"
        new_files.append((p, filepath))

    # Update MEMORY.md index
    if new_files:
        added = update_memory_index(new_files, MEMORY_DIR, args.dry_run)
        tag = "[DRY RUN]" if args.dry_run else ""
        print(f"\n[self_improve] {tag} MEMORY.md: +{added or 0} new index entries")

    # Log + report
    if not args.dry_run:
        log_run(pattern_data, LESSONS_LOG)

    rpt = generate_report(
        all_entries,
        low,
        pattern_data,
        DIAGNOSTICS,
        args.dry_run,
        threshold=args.threshold,
    )
    print(f"[self_improve] Diagnostic → {rpt}")
    print(f"\n[self_improve] Done. {len(new_files)} lessons {'previewed' if args.dry_run else 'written'}.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
