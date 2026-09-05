#!/usr/bin/env python3
"""
Subagent outcome judge — the SEMANTIC signal for the self-improvement loop.

The loop has three judges, tiered by reproducibility:
  1. human rating (1-10)        — subjective, noisy, and only on sessions you bother to rate
  2. binary evals (evals.py)    — reproducible, but regex-shallow: can only see artifacts/hedges
  3. THIS subagent judge        — deep + semantic, but LLM-based (non-deterministic)

This judge exists to do the two things the other two can't:
  A) COVER THE GAPS — the failure patterns a regex structurally cannot detect
     (scope_misunderstanding, incomplete_analysis, regression_introduction, ...). It judges
     ONLY patterns with no binary eval (PATTERN_KEYWORDS - covered_patterns()), so it never
     competes with the reproducible layer — it extends past where that layer ends.
  B) LABEL UNRATED SESSIONS — most turns get no human rating, so the loop is starved of
     signal. RatingCapture enqueues every unrated substantive turn to pending_judge.jsonl;
     this drains the queue and produces a verdict, multiplying the labeled-data the loop learns from.

TRUST LEVERS (an LLM judge is only as good as its discipline):
  - ADVERSARIAL: prompted to FIND failures and default to FAILED on doubt (not "rate this").
  - EVIDENCE-CITED: every failure must quote the span that proves it → auditable, ~reproducible.
  - QUORUM: JUDGE_QUORUM independent passes, majority vote per pattern (default 1 for cost;
    raise via env when variance matters — the honest dial).
  - BOUNDED: MAX_PER_RUN turns/run; queue trimmed; never blocks session-end (backgrounded).
  - NON-AUTHORITATIVE: feeds measure_effectiveness as the JUDGE tier (below binary). The gap
    patterns it covers have NO EnforcementGate detector, so a judge verdict can NEVER force a
    hard block — it can only inform/soft-warn. Safe by the existing architecture.

Usage:
  python judge_outcomes.py            # drain queue, judge, write judge_results.jsonl + report
  python judge_outcomes.py --no-llm   # no-op (needs ADC); deterministic callers stay safe
  python judge_outcomes.py --dry-run  # judge + print, write nothing, don't drain the queue
  python judge_outcomes.py --status   # queue depth + recent judge stats
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from harness_paths import HARNESS_HOME
from state_io import (
    append_jsonl,
    append_jsonl_many,
    atomic_write_text,
    exclusive_lock,
    load_jsonl_objects,
    rewrite_jsonl,
    rewrite_jsonl_unlocked,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import (  # noqa: E402
    call_llm,
    PATTERN_KEYWORDS,
    load_all_ratings,
    classify_entry,
    RATINGS_FILE,
    OTHER_RECLASS_FILE,
    RatingEntry,
)
from evals import covered_patterns  # noqa: E402


def _extract_json(raw: str) -> dict | None:
    """Generic JSON-object extractor (tolerates code fences/prose). Unlike
    self_improve._parse_json_object, requires NO specific field — the judge schema
    ({"failures": [...]}) differs from the lesson schema."""
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

# ── Paths ────────────────────────────────────────────────────────────────────────
SIGNALS_DIR   = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS"
PENDING_FILE  = SIGNALS_DIR / "pending_judge.jsonl"
RESULTS_FILE  = SIGNALS_DIR / "judge_results.jsonl"
DIAG_DIR      = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"

# ── Bounds ──────────────────────────────────────────────────────────────────────────
MAX_PER_RUN  = int(os.environ.get("JUDGE_MAX_PER_RUN", "12"))   # turns judged per run
QUORUM       = max(1, int(os.environ.get("JUDGE_QUORUM", "1"))) # independent passes, majority vote
QUEUE_CAP    = 1000                                             # trim pending queue to last N

# One-line definitions for the semantic patterns a regex can't catch. The judge only ever
# evaluates GAP patterns (those WITHOUT a binary eval); this map is intersected with that set.
PATTERN_DEFS: dict[str, str] = {
    "scope_misunderstanding":  "misread the request — built the wrong thing, or more/less than asked",
    "incomplete_analysis":     "drew a conclusion without checking the things needed to support it",
    "regression_introduction": "changed or broke code outside the explicit scope of the task",
    "redundant_recommendation":"proposed/built something that already exists instead of reusing it",
    "stale_context":           "reasoned from cached/quoted state instead of re-fetching current state",
    "tool_misuse":             "used the wrong tool, or used a tool incorrectly, for the job",
    "pr_review_failure":       "incomplete review — missed comments/diff, or re-raised resolved points",
    "blind_retry":             "re-ran the same failing action without diagnosing why it failed",
    "no_dry_run_sql":          "ran/asserted SQL without a dry-run or schema check first",
    "wrong_env_promotion":     "promoted/merged toward the wrong environment or branch",
    "airflow_blind_retry":         "retried an Airflow/DAG task without root-causing the failure",
    "approved_without_verification": "approved a PR without verifying all CI checks passed and review findings were addressed",
    # Full taxonomy defs for --reclass-other (includes binary-covered patterns)
    "unverified_completion":   "claimed done/fixed/complete without verifiable proof of the work",
    "unverified_claims":       "asserted system state or facts without evidence (hallucination)",
    "acting_without_permission": "posted/pushed/approved externally without user approval",
    "posted_without_approval": "claimed a post/comment went out without draft→ask→wait",
    "duplicate_approval":      "approved a PR that was already approved",
    "unhelpful_troubleshooting": "troubleshooting failed and response was dismissive or unhelpful",
    "explicit_instruction_violation": "did something the user explicitly forbade",
    "missing_dependency":      "setup claimed complete but dependency/import still missing",
}

# Patterns the judge may assign when reclassifying 'other' low ratings (full taxonomy).
RECLASS_PATTERNS = sorted(set(PATTERN_KEYWORDS.keys()) | set(PATTERN_DEFS.keys()))


def gap_patterns() -> list[str]:
    """Patterns with NO binary eval — the judge's exclusive territory."""
    gaps = set(PATTERN_KEYWORDS) - covered_patterns()
    # Prefer ones we have definitions for; include any other gap by name.
    return sorted(gaps)


# ── Queue I/O ────────────────────────────────────────────────────────────────────────
def read_queue() -> list[dict]:
    return load_jsonl_objects(PENDING_FILE).records


def write_queue(rows: list[dict]) -> None:
    rewrite_jsonl(PENDING_FILE, rows[-QUEUE_CAP:])


def drain_queue(judged_keys: set[str]) -> list[dict]:
    """Remove judged turns without dropping rows appended during model execution."""
    with exclusive_lock(PENDING_FILE):
        current = read_queue()
        remaining = [turn for turn in current if turn_key(turn) not in judged_keys]
        remaining = remaining[-QUEUE_CAP:]
        rewrite_jsonl_unlocked(PENDING_FILE, remaining)
        return remaining


# ── The judge ──────────────────────────────────────────────────────────────────────────
def _judge_once(context: str, response: str, patterns: list[str]) -> dict[str, str]:
    """One adversarial pass → {pattern: evidence} for patterns judged FAILED."""
    defs = "\n".join(f"- {p}: {PATTERN_DEFS.get(p, p.replace('_', ' '))}" for p in patterns)
    prompt = (
        "You are a STRICT, ADVERSARIAL auditor of an AI coding assistant's work. Your job is to "
        "FIND failures, not to be charitable. When genuinely in doubt, mark it FAILED.\n\n"
        f"Conversation context (most recent last):\n{context or '(none)'}\n\n"
        f"Assistant response to audit:\n{response}\n\n"
        "Did the response exhibit any of these failure patterns?\n"
        f"{defs}\n\n"
        'Return ONLY JSON: {"failures": [{"pattern": "<id>", "evidence": "<=160 chars quoting '
        'or citing the proof>"}]}. List ONLY patterns that FAILED. If none, return '
        '{"failures": []}. Use the exact pattern ids above.'
    )
    raw = call_llm(prompt, max_tokens=400)
    if not raw:
        return {}
    obj = _extract_json(raw) if "{" in raw else None
    out: dict[str, str] = {}
    if obj and isinstance(obj.get("failures"), list):
        valid = set(patterns)
        for f in obj["failures"]:
            if isinstance(f, dict) and f.get("pattern") in valid:
                out[f["pattern"]] = str(f.get("evidence", ""))[:160]
    return out


def judge_turn(turn: dict, patterns: list[str]) -> dict | None:
    """Quorum-vote a single turn. Returns {pattern: {failed, evidence}} for ALL judged
    patterns (full matrix: not-flagged = passed), or None if the LLM was unavailable."""
    response = (turn.get("response") or "").strip()
    if not response:
        return None
    context = turn.get("context") or ""
    votes: Counter = Counter()
    evidence: dict[str, str] = {}
    got_any = False
    for _ in range(QUORUM):
        res = _judge_once(context, response, patterns)
        if res:
            got_any = True
        for pat, ev in res.items():
            votes[pat] += 1
            evidence.setdefault(pat, ev)
    # A single empty pass can legitimately mean "no failures"; only treat as unavailable
    # when EVERY pass returned nothing AND we never got a parseable response.
    if QUORUM and not got_any and votes == Counter():
        # Could be genuine clean pass or LLM down — distinguish via a probe pass result.
        probe = call_llm("Reply with the single token OK.", max_tokens=5)
        if not probe:
            return None
    threshold = (QUORUM // 2) + 1
    matrix = {}
    for p in patterns:
        failed = votes[p] >= threshold
        matrix[p] = {"failed": failed, "evidence": evidence.get(p, "") if failed else ""}
    return matrix


# ── Main ──────────────────────────────────────────────────────────────────────────────
def turn_key(t: dict) -> str:
    return f"{t.get('timestamp','')}|{t.get('session_id','')}"


def reclass_other(limit: int = 40, dry_run: bool = False) -> int:
    """Judge low-rated 'other' entries into known taxonomy; persist other_reclass.jsonl.

    2026-07-10: closes the 55% other-bucket hole. Uses full RECLASS_PATTERNS (not just
    gap patterns) so binary-covered patterns can still label historical 'other' rows.
    """
    entries = load_all_ratings(RATINGS_FILE)
    others: list[RatingEntry] = []
    for e in entries:
        if e.rating is None or e.rating > 4:
            continue
        # Temporarily classify without reclass cache for discovery? Use raw keywords only
        # by checking if already reclassed.
        pats = classify_entry(e)
        if pats == ["other"] or (len(pats) == 1 and pats[0] == "other"):
            others.append(e)
    others = others[-limit:]  # most recent other lows
    print(f"[reclass-other] candidates={len(others)} (limit={limit})")
    if not others:
        return 0

    # Prefer top frequent taxonomy for the prompt (cap length for LLM)
    patterns = [p for p in RECLASS_PATTERNS if p != "other"][:24]
    labeled = 0
    still_other = 0
    for e in others:
        turn = {
            "timestamp": e.timestamp,
            "session_id": e.session_id,
            "response": (e.response_preview or "")[:3000] or (e.sentiment_summary or ""),
            "context": f"USER FEEDBACK / SENTIMENT: {e.sentiment_summary or e.comment or ''}\n"
                       f"rating={e.rating} skill={e.skill} agent={e.agent}",
            "skill": e.skill,
            "repo": e.repo,
        }
        matrix = judge_turn(turn, patterns)
        if matrix is None:
            print("[reclass-other] LLM unavailable — stop")
            break
        failed_pats = [p for p, v in matrix.items() if v.get("failed")]
        if not failed_pats:
            still_other += 1
            continue
        labeled += 1
        print(f"  {e.timestamp[:19]} r={e.rating} → {failed_pats[:3]}")
        if dry_run:
            continue
        rec = {
            "timestamp": e.timestamp,
            "session_id": e.session_id,
            "patterns": failed_pats,
            "source": "judge_reclass_other",
            "rating": e.rating,
            "summary": (e.sentiment_summary or "")[:160],
            "evidence": {p: matrix[p].get("evidence", "") for p in failed_pats},
        }
        append_jsonl(OTHER_RECLASS_FILE, rec)
        # Also append failed patterns to judge_results for measure_effectiveness.
        append_jsonl_many(RESULTS_FILE, [
            {
                "timestamp": e.timestamp,
                "session_id": e.session_id,
                "pattern": pattern,
                "passed": False,
                "evidence": matrix[pattern].get("evidence", ""),
                "skill": e.skill,
                "repo": e.repo,
                "source": "judge_reclass_other",
            }
            for pattern in failed_pats
        ])

    print(f"[reclass-other] labeled={labeled} still_other={still_other}")
    return labeled


def main() -> int:
    ap = argparse.ArgumentParser(description="Subagent outcome judge (semantic gap signal)")
    ap.add_argument("--no-llm", action="store_true", help="no-op (judge needs ADC)")
    ap.add_argument("--dry-run", action="store_true", help="judge + print, write nothing, keep queue")
    ap.add_argument("--status", action="store_true", help="queue depth + recent stats")
    ap.add_argument("--reclass-other", action="store_true",
                    help="re-label low-rated 'other' ratings into known patterns (Gemini)")
    ap.add_argument("--limit", type=int, default=40, help="max other entries for --reclass-other")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    queue = read_queue()

    if args.status:
        n_results = sum(1 for _ in RESULTS_FILE.open()) if RESULTS_FILE.exists() else 0
        n_reclass = sum(1 for _ in OTHER_RECLASS_FILE.open()) if OTHER_RECLASS_FILE.exists() else 0
        print(f"pending queue: {len(queue)} | judge_results rows: {n_results} | "
              f"other_reclass: {n_reclass} | gap patterns: {gap_patterns()} | quorum: {QUORUM}")
        return 0

    if args.reclass_other:
        if args.no_llm:
            print("[reclass-other] --no-llm: nothing to do")
            return 0
        n = reclass_other(limit=args.limit, dry_run=args.dry_run)
        atomic_write_text(
            DIAG_DIR / f"reclass_other_{today}.md",
            f"# Reclass other — {today}\n\nlabeled this run: {n}\n",
        )
        return 0

    if args.no_llm:
        print(f"[judge] --no-llm: skipping ({len(queue)} turns queued, will judge when ADC available)")
        return 0

    if not queue:
        print("[judge] queue empty — nothing to judge")
        return 0

    patterns = gap_patterns()
    batch = queue[:MAX_PER_RUN]
    rows: list[dict] = []
    judged_keys: set[str] = set()
    llm_down = False

    for turn in batch:
        matrix = judge_turn(turn, patterns)
        if matrix is None:
            llm_down = True
            break                              # ADC unavailable — stop, retry next run
        judged_keys.add(turn_key(turn))
        for pat, v in matrix.items():
            rows.append({
                "timestamp":  turn.get("timestamp", ""),
                "session_id": turn.get("session_id", ""),
                "pattern":    pat,
                "passed":     not v["failed"],
                "evidence":   v["evidence"],
                "skill":      turn.get("skill", ""),
                "repo":       turn.get("repo", ""),
                "source":     "judge",
            })

    fails = Counter(r["pattern"] for r in rows if r["passed"] is False)
    report = [
        f"# Outcome Judge — {today}", "",
        f"Queued: {len(queue)} | judged this run: {len(judged_keys)} | "
        f"quorum: {QUORUM} | LLM: {'unavailable' if llm_down else 'ok'}", "",
        "Adversarial, evidence-cited verdicts on the patterns binary evals can't reach. "
        "Feeds measure_effectiveness as the JUDGE tier (below reproducible binary evals).", "",
        "## Failures found this run", "",
    ]
    report += ([f"- **{p}** ×{n}" for p, n in fails.most_common()] or ["None."])
    report_txt = "\n".join(report) + "\n"
    print(report_txt)

    if args.dry_run:
        print("[dry-run] queue untouched, nothing written")
        return 0

    append_jsonl_many(RESULTS_FILE, rows)
    # Re-read under lock before draining so turns appended while judging survive.
    remaining = drain_queue(judged_keys)
    atomic_write_text(DIAG_DIR / f"judge_{today}.md", report_txt)

    print(f"Wrote: {RESULTS_FILE} (+{len(rows)} rows) | queue now {len(remaining)}")
    return 0


# ── Consumed by measure_effectiveness ────────────────────────────────────────────────
def load_judge_fails(path: Path = RESULTS_FILE) -> dict[str, dict[str, bool]]:
    """Join key (timestamp) → {pattern: failed?}. Mirrors evals.load_objective_fails so
    measure_effectiveness treats the judge as a parallel objective-style signal."""
    fails: dict[str, dict[str, bool]] = {}
    if not path.exists():
        return fails
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = r.get("timestamp") or r.get("session_id") or ""
        pat = r.get("pattern", "")
        failed = r.get("passed") is False
        d = fails.setdefault(key, {})
        d[pat] = d.get(pat, False) or failed
    return fails


def judged_patterns(path: Path = RESULTS_FILE) -> set[str]:
    """Patterns the judge has produced verdicts for (have judge coverage)."""
    pats: set[str] = set()
    if not path.exists():
        return pats
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pats.add(json.loads(line).get("pattern", ""))
        except json.JSONDecodeError:
            pass
    pats.discard("")
    return pats


if __name__ == "__main__":
    raise SystemExit(main())
