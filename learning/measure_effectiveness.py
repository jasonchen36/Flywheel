#!/usr/bin/env python3
"""
Effectiveness scorer — the hill-climbing measurement step.

For every auto-generated lesson, measure whether its failure pattern recurred
LESS after the lesson was written. This is the gradient signal the loop was
missing: it converts "describe failures" into "verify the fix worked."

Method (per pattern P with lesson dated D):
  before_rate = low-rated sessions matching P, with date <  D, / all sessions before D
  after_rate  = low-rated sessions matching P, with date >= D, / all sessions after  D
  delta       = after_rate - before_rate     (negative = improvement)
  verdict     = resolved | working | improving | flat | regressed | pending

Outputs (never mutates lesson files):
  DIAGNOSTICS/effectiveness_{date}.md   human report
  STATE/effectiveness_scores.json       machine-readable, consumed by injection hook
  effectiveness_log.jsonl               time series (one row per pattern per run)

Escalation candidates = lessons that exist but whose pattern is flat/regressed
with enough post-lesson data → these need HARD enforcement (hook block), not a
soft memory note.

Usage:
  python measure_effectiveness.py            # full run
  python measure_effectiveness.py --dry-run  # print report, write nothing
  python measure_effectiveness.py --min-after 5   # min post-lesson sessions to judge
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_paths import BUNGRAPH_DB, HARNESS_HOME
from review_store import enqueue_pending, expire_pending, load_reviews
from state_io import (
    append_jsonl_many,
    atomic_write_json,
    atomic_write_text,
    try_read_json_object,
)

# Reuse the EXACT classifier the generator uses — no attribution drift.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import (  # noqa: E402
    load_all_ratings,
    classify_entry,
    RATINGS_FILE,
    MEMORY_DIR,
    rating_entry_key,
    DIAGNOSTICS,
)
from evals import load_objective_fails, covered_patterns  # noqa: E402
from judge_outcomes import load_judge_fails, judged_patterns  # noqa: E402

STATE_DIR = HARNESS_HOME / "MEMORY/STATE"
SCORES_JSON = STATE_DIR / "effectiveness_scores.json"
EFFECT_LOG  = HARNESS_HOME / "MEMORY/LEARNING/effectiveness_log.jsonl"
REVIEW_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"

LOW = 4              # rating <= LOW counts as a failure session
MIN_AFTER = 5        # need this many post-lesson sessions before judging
# Cap pending lifetime: if the measurement window still has after_n < min_after
# after this many calendar days, mark stale-pending (force merge or traffic).
STALE_PENDING_DAYS = 14
REVIEW_EXPIRE_DAYS = 14
# Verdicts that are real hill-climb signals (injectable / escalatable).
# pending / stale-pending / no-* are measurement states, not outcomes.
REAL_VERDICTS = frozenset({
    "resolved", "working", "improving", "flat", "regressed",
})

# Patterns that have a high-precision detector in EnforcementGate.hook.ts and can
# therefore graduate to hard enforcement. MUST mirror DETECTORS in that hook.
# Escalated patterns NOT in this set stay soft-only (semantic → not auto-detectable).
# 2026-07-09: expanded to full detector registry (skip first-time human gate).
ENFORCEABLE_PATTERNS = {
    "unverified_completion",
    "unverified_claims",
    "incomplete_analysis",  # 2026-07-10: high-precision detector + ALWAYS_ON
    "duplicate_approval",
    "blind_retry",
    "tool_misuse",
    "guardrail_bypass",
}


def entry_date(ts: str) -> str:
    """ISO8601 → 'YYYY-MM-DD'. Lexicographic compare works for ISO dates."""
    return (ts or "")[:10]


def push_to_bungraph(pattern: str, verdict: str, delta: float, today: str):
    """Push learning verdicts into bungraph (both MCP + LEARNING DB paths).

    2026-07-09: previous fire-and-forget bunx without BUNGRAPH_DB_PATH left
    stale HAS_VERDICT edges (e.g. unverified_completion stuck at 'improving').
    Write both DBs used by agents; swallow errors (non-blocking).
    """
    if verdict in ("pending", "stale-pending", "no-baseline", "undated",
                   "no-eval", "no-judge"):
        return

    fact_text = (
        f"Behavioral pattern '{pattern}' has verdict '{verdict}' (delta: {delta:+.3f}) "
        f"as of {today} based on objective/subjective session evaluations."
    )
    dbs = [
        BUNGRAPH_DB,
        HARNESS_HOME / "MEMORY/LEARNING/bungraph.db",
    ]
    for db in dbs:
        env = {**os.environ, "BUNGRAPH_DB_PATH": str(db)}
        try:
            subprocess.Popen(
                [
                    "bunx", "bungraph", "triplet",
                    f"lesson_{pattern}", "HAS_VERDICT", verdict,
                    "--fact", fact_text,
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"[bungraph-loopback] failed for {db}: {e}")


def discover_lessons(memory_dir: Path) -> dict[str, dict]:
    """Map pattern → measurement metadata for before/after scoring.

    Measurement epoch (frozen):
      baseline_date > first_seen > last_updated (last only if undated).
    Content epoch (ignored for measurement):
      content_version / last_updated — may bump when text is reinforced.

    Evolving lesson text must NOT reset the measurement window
    (2026-07-09: last_updated=today → after_n=0 forever).
    """
    out: dict[str, dict] = {}
    for f in sorted(memory_dir.glob("lesson_autogen_*.md")):
        pattern = f.stem.replace("lesson_autogen_", "")
        txt = f.read_text(errors="replace")
        m_base = re.search(r"^\s*baseline_date:\s*(\d{4}-\d{2}-\d{2})", txt, re.M)
        m_first = re.search(r"^\s*first_seen:\s*(\d{4}-\d{2}-\d{2})", txt, re.M)
        m_last = re.search(r"^\s*last_updated:\s*(\d{4}-\d{2}-\d{2})", txt, re.M)
        m_cver = re.search(r"^\s*content_version:\s*(\S+)", txt, re.M)
        baseline = (
            m_base.group(1) if m_base else
            m_first.group(1) if m_first else
            (m_last.group(1) if m_last else "")
        )
        out[pattern] = {
            "baseline_date": baseline,
            "first_seen": m_first.group(1) if m_first else "",
            "last_updated": m_last.group(1) if m_last else "",
            "content_version": m_cver.group(1) if m_cver else "",
            "path": str(f),
        }
    return out


def days_between(start: str, end: str) -> int:
    """Calendar days between YYYY-MM-DD strings; 999 on parse failure."""
    try:
        return (datetime.strptime(end, "%Y-%m-%d") -
                datetime.strptime(start, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return 999


def verdict_for(before_rate: float, after_rate: float, after_n: int, min_after: int,
                days_open: int | None = None,
                stale_after_days: int = STALE_PENDING_DAYS) -> str:
    if after_n < min_after:
        # Cap pending lifetime so lessons don't sit forever waiting for traffic
        # that never arrives (bulk-generated first_seen → after_n stuck at 0/1).
        if days_open is not None and days_open >= stale_after_days:
            return "stale-pending"
        return "pending"            # too soon — not enough post-lesson sessions
    if before_rate == 0:
        return "no-baseline"        # pattern never seen before lesson
    if after_rate == 0:
        return "resolved"
    if after_rate <= before_rate * 0.5:
        return "working"
    if after_rate < before_rate:
        return "improving"
    if after_rate <= before_rate * 1.2:
        return "flat"
    return "regressed"


def is_real_verdict(v: str) -> bool:
    """True when the verdict is a hill-climb outcome (not a measurement state)."""
    return v in REAL_VERDICTS


def rating_failure_rate(pool: list[Any], pattern: str) -> tuple[float, int]:
    if not pool:
        return 0.0, 0
    hits = sum(
        1
        for entry in pool
        if isinstance(getattr(entry, "rating", None), int)
        and entry.rating <= LOW
        and pattern in getattr(entry, "patterns", [])
    )
    return hits / len(pool), hits


def objective_failure_rate(
    pool: list[Any],
    failures: dict[str, dict[str, bool]],
    pattern: str,
) -> float:
    if not pool:
        return 0.0
    hits = 0
    for entry in pool:
        exact_key = rating_entry_key(entry)
        pattern_results = failures.get(exact_key)
        if pattern_results is None:
            timestamp = getattr(entry, "timestamp", "")
            pattern_results = failures.get(timestamp, {}) if isinstance(timestamp, str) else {}
        if pattern_results.get(pattern):
            hits += 1
    return hits / len(pool)


def judge_failure_rate(
    failures: dict[str, dict[str, bool]],
    pattern: str,
    lesson_date: str,
    *,
    before: bool,
) -> tuple[float, int]:
    rows = [
        patterns[pattern]
        for timestamp, patterns in failures.items()
        if pattern in patterns and ((entry_date(timestamp) < lesson_date) == before)
    ]
    if not rows:
        return 0.0, 0
    return sum(1 for failed in rows if failed) / len(rows), len(rows)


def load_prior_scores(path: Path = SCORES_JSON) -> dict[str, dict[str, Any]]:
    state, _ = try_read_json_object(path)
    scores = state.get("scores")
    if not isinstance(scores, dict):
        return {}
    return {
        pattern: value
        for pattern, value in scores.items()
        if isinstance(pattern, str) and isinstance(value, dict)
    }


def safe_nonnegative_int(value: object) -> int:
    if not isinstance(value, (str, int, float)):
        return 0
    try:
        return max(0, int(value))
    except (OverflowError, ValueError):
        return 0


def escalation_verdict(result: dict[str, Any], min_after: int) -> str:
    if result.get("eval_covered"):
        objective = str(result.get("obj_verdict") or "no-eval")
        subjective = str(result.get("verdict") or "pending")
        if (
            subjective in {"flat", "regressed"}
            and safe_nonnegative_int(result.get("after_n")) >= min_after
            and result.get("pattern") in ENFORCEABLE_PATTERNS
            and objective not in {"regressed", "flat"}
        ):
            return subjective
        return objective
    if result.get("judge_covered"):
        return str(result.get("judge_verdict") or "no-judge")
    return str(result.get("verdict") or "pending")


def active_review_patterns(
    records: list[dict[str, Any]],
    today: str,
    max_age_days: int = REVIEW_EXPIRE_DAYS,
) -> set[str]:
    active: set[str] = set()
    for record in records:
        status = record.get("status")
        pattern = record.get("pattern")
        if not isinstance(pattern, str):
            continue
        if status in {"processing", "action_failed"}:
            active.add(pattern)
            continue
        if status != "pending":
            continue
        detected = record.get("detected_at", "")
        try:
            days_old = (
                datetime.strptime(today, "%Y-%m-%d")
                - datetime.strptime(detected, "%Y-%m-%d")
            ).days
        except (TypeError, ValueError):
            days_old = max_age_days + 1
        if days_old <= max_age_days:
            active.add(pattern)
    return active


def escalation_driver(
    result: dict[str, Any],
    verdict: str,
) -> tuple[str, float, int]:
    if verdict == result.get("verdict") and verdict in {"flat", "regressed"}:
        return "subj", float(result.get("delta") or 0.0), safe_nonnegative_int(result.get("after_n"))
    if result.get("eval_covered") and verdict == result.get("obj_verdict"):
        return "obj", float(result.get("obj_delta") or 0.0), safe_nonnegative_int(result.get("after_n"))
    if result.get("judge_covered") and verdict == result.get("judge_verdict"):
        return "jdg", float(result.get("judge_delta") or 0.0), safe_nonnegative_int(result.get("judge_after_n"))
    if result.get("eval_covered"):
        return "obj", float(result.get("obj_delta") or 0.0), safe_nonnegative_int(result.get("after_n"))
    return "subj", float(result.get("delta") or 0.0), safe_nonnegative_int(result.get("after_n"))


def notify(message: str) -> bool:
    try:
        subprocess.Popen(
            [
                "curl", "-s", "-X", "POST", "http://localhost:8888/notify",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({
                    "message": message,
                    "voice_id": "fTtv3eikoepIosk8dTZ5",
                    "voice_enabled": True,
                }),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(f"[effectiveness] notification unavailable: {exc}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-after", type=int, default=MIN_AFTER)
    args = ap.parse_args(argv)
    min_after = args.min_after
    if min_after <= 0:
        print("[effectiveness] min-after must be positive")
        return 2

    entries = load_all_ratings(RATINGS_FILE)
    for e in entries:
        e.patterns = classify_entry(e)

    # Objective signal: reproducible binary-eval fails, joined on exact rating turn.
    obj_fails = load_objective_fails()
    covered = covered_patterns()
    # Judge signal: semantic verdicts on the judge's OWN labeled turns (mostly unrated
    # sessions). Separate population from ratings.jsonl, so it carries its own sample size.
    judge_fails = load_judge_fails()
    judged = judged_patterns()

    lessons = discover_lessons(MEMORY_DIR)
    if not lessons:
        print("No auto-generated lessons found — run self_improve.py first.")
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: list[dict[str, Any]] = []

    for pattern, meta in sorted(lessons.items()):
        ldate = meta.get("baseline_date") or ""
        if not ldate:
            results.append({
                "pattern": pattern,
                "verdict": "undated",
                "before_rate": 0.0,
                "after_rate": 0.0,
                "delta": 0.0,
                "before_n": 0,
                "after_n": 0,
                "lesson_date": "",
                "baseline_date": "",
                "content_version": meta.get("content_version") or "",
                "last_updated": meta.get("last_updated") or "",
                "days_open": 0,
                "injectable": False,
                "eval_covered": False,
                "obj_before": 0.0,
                "obj_after": 0.0,
                "obj_delta": 0.0,
                "obj_verdict": "no-eval",
                "judge_covered": False,
                "judge_before": 0.0,
                "judge_after": 0.0,
                "judge_delta": 0.0,
                "judge_verdict": "no-judge",
                "judge_after_n": 0,
            })
            continue

        before = [e for e in entries if entry_date(e.timestamp) < ldate]
        after = [e for e in entries if entry_date(e.timestamp) >= ldate]
        days_open = days_between(ldate, today)

        before_rate, _ = rating_failure_rate(before, pattern)
        after_rate, _ = rating_failure_rate(after, pattern)
        v = verdict_for(before_rate, after_rate, len(after), min_after,
                        days_open=days_open)

        # Objective verdict from binary evals — reproducible, when the pattern has coverage.
        is_covered = pattern in covered
        if is_covered:
            obj_before = objective_failure_rate(before, obj_fails, pattern)
            obj_after = objective_failure_rate(after, obj_fails, pattern)
            obj_v = verdict_for(obj_before, obj_after, len(after), min_after,
                                days_open=days_open)
        else:
            obj_before = obj_after = 0.0
            obj_v = "no-eval"

        # Judge verdict (semantic tier) — measured over the judge's OWN labeled turns,
        # split on the lesson date. Its own population + sample size (judged turns are
        # mostly unrated, so they are NOT in `entries`; never join on entry timestamps).
        is_judged = pattern in judged
        if is_judged:
            jdg_before, _ = judge_failure_rate(
                judge_fails, pattern, ldate, before=True
            )
            jdg_after, jdg_after_n = judge_failure_rate(
                judge_fails, pattern, ldate, before=False
            )
            jdg_v = verdict_for(jdg_before, jdg_after, jdg_after_n, min_after,
                                days_open=days_open)
        else:
            jdg_before = jdg_after = 0.0
            jdg_after_n = 0
            jdg_v = "no-judge"

        results.append({
            "pattern": pattern,
            "lesson_date": ldate,  # == baseline_date (compat for consumers)
            "baseline_date": ldate,
            "content_version": meta.get("content_version") or "",
            "last_updated": meta.get("last_updated") or "",
            "days_open": days_open,
            "before_rate": round(before_rate, 4),
            "after_rate": round(after_rate, 4),
            "delta": round(after_rate - before_rate, 4),
            "before_n": len(before),
            "after_n": len(after),
            "verdict": v,
            "injectable": is_real_verdict(v) or (
                is_covered and is_real_verdict(obj_v)
            ) or (
                is_judged and is_real_verdict(jdg_v)
            ),
            "eval_covered": is_covered,
            "obj_before": round(obj_before, 4),
            "obj_after": round(obj_after, 4),
            "obj_delta": round(obj_after - obj_before, 4),
            "obj_verdict": obj_v,
            "judge_covered": is_judged,
            "judge_before": round(jdg_before, 4),
            "judge_after": round(jdg_after, 4),
            "judge_delta": round(jdg_after - jdg_before, 4),
            "judge_verdict": jdg_v,
            "judge_after_n": jdg_after_n,
        })

    # Escalate on the most reproducible signal available, in tiers:
    #   binary eval (reproducible) > subagent judge (semantic) > subjective rating (noisy).
    # Dual-signal (2026-07): if subjective is regressed/flat with enough post data BUT
    # objective looks fine, still escalate for ENFORCEABLE patterns. Real failure mode:
    # evals measured path-as-artifact while users still rated 3/10 for premature "done".
    # Only escalate patterns with a REAL outcome (flat/regressed) — never the
    # pending / stale-pending sea. Escalation drives EnforcementGate + injection.
    escalate = [
        r for r in results
        if escalation_verdict(r, min_after) in ("flat", "regressed")
        and is_real_verdict(escalation_verdict(r, min_after))
    ]
    stale_pending = [r for r in results if r["verdict"] == "stale-pending"]

    # Load prior scores to identify first-time regressions (file may not exist yet).
    prior_scores = load_prior_scores(SCORES_JSON)

    # Pending, processing, and failed-action reviews remain gated. Overdue pending
    # rows are treated as expired for this decision and committed transactionally below.
    _pending_review = active_review_patterns(load_reviews(REVIEW_FILE), today)

    # Enforceable patterns already have detectors + ALWAYS_ON/block config —
    # never gate them behind first-time human review (2026-07-09: UC kept
    # dropping out of escalate[] despite regressed + EnforcementGate ready).
    first_time_regressed = [
        r for r in escalate
        if escalation_verdict(r, min_after) == "regressed"
        and r["pattern"] not in ENFORCEABLE_PATTERNS
        and r["pattern"] not in _pending_review
        and prior_scores.get(r["pattern"], {}).get("verdict") != "regressed"
    ]
    # Patterns still under active review (pending, not expired) stay out of escalate
    # — except enforceable (detectors already live).
    under_review = [
        r for r in escalate
        if r["pattern"] in _pending_review
        and r["pattern"] not in ENFORCEABLE_PATTERNS
        and r not in first_time_regressed
    ]
    escalate = [r for r in escalate if r not in first_time_regressed and r not in under_review]

    # ── report ────────────────────────────────────────────────────────────────
    order = {"regressed": 0, "flat": 1, "improving": 2, "working": 3,
             "resolved": 4, "stale-pending": 5, "pending": 6, "no-baseline": 7,
             "no-eval": 8, "no-judge": 8, "undated": 9}
    rows = sorted(
        results,
        key=lambda result: (
            order.get(escalation_verdict(result, min_after), 10),
            result["delta"],
        ),
    )

    # Coverage gaps: patterns recurring in low-rated sessions with no binary eval yet.
    low = [e for e in entries if e.rating <= LOW]
    observed = {p for e in low for p in e.patterns if p != "other"}
    gaps = sorted(observed - covered)

    n_real = sum(1 for r in results if r.get("injectable"))
    n_pending = sum(1 for r in results if r["verdict"] == "pending")
    lines = [f"# Lesson Effectiveness — {today}", "",
             f"Lessons: {len(results)} | Real verdicts: {n_real} | "
             f"Pending: {n_pending} | Stale-pending: {len(stale_pending)} | "
             f"Escalation: {len(escalate)} | min-after: {min_after} | "
             f"stale-after: {STALE_PENDING_DAYS}d", "",
             "verdict = did the failure pattern recur LESS after the lesson was written?",
             "Measurement epoch = baseline_date/first_seen (frozen). Content rewrites "
             "bump last_updated/content_version only — they do NOT reset after_n.",
             "stale-pending = after_n < min-after for ≥14 calendar days → merge into "
             "parent pattern or force traffic attribution; do NOT inject.",
             "subj = subjective (1-10 ratings); obj = reproducible binary evals; "
             "jdg = subagent judge (semantic, unrated turns). Escalation tier: "
             "obj if eval-covered, else jdg if judged, else subj. Only real "
             "verdicts (regressed|flat|improving|working|resolved) escalate/inject.", "",
             "| pattern | subj | subjΔ | obj | objΔ | jdg | jdgΔ | n(sub/jdg) | days |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        obj = r["obj_verdict"]
        objd = f"{r['obj_delta']:+.3f}" if r["eval_covered"] else "—"
        jdg = r["judge_verdict"]
        jdgd = f"{r['judge_delta']:+.3f}" if r["judge_covered"] else "—"
        lines.append(
            f"| {r['pattern']} | {r['verdict']} | {r['delta']:+.3f} | "
            f"{obj} | {objd} | {jdg} | {jdgd} | {r['after_n']}/{r['judge_after_n']} | "
            f"{r.get('days_open', 0)} |"
        )
    if escalate:
        lines += ["", "## Escalation candidates (lesson exists, pattern persists)",
                  "[enforceable] → auto-graduates to EnforcementGate hard block. "
                  "[soft-only] → semantic, no detector; stays a memory note.", ""]
        for r in escalate:
            tag = "enforceable" if r["pattern"] in ENFORCEABLE_PATTERNS else "soft-only"
            # Report the signal that actually drove escalation (may be subj dual-signal)
            ev = escalation_verdict(r, min_after)
            driver, d, n = escalation_driver(r, ev)
            lines.append(f"- **{r['pattern']}** [{tag}] (via {driver}, Δ {d:+.3f}, "
                         f"{n} sessions since lesson)")
    review_section = first_time_regressed + under_review
    if review_section:
        lines += ["", "## Gated for human review (NOT in escalate[])",
                  f"Run `python3 review_queue.py --list` to inspect. Auto-escalates after {REVIEW_EXPIRE_DAYS} days.", ""]
        for r in review_section:
            tag = "new" if r in first_time_regressed else "pending"
            lines.append(f"- **{r['pattern']}** [{tag}] (Δ {r['delta']:+.3f}, {r['after_n']} sessions since lesson)")
    if stale_pending:
        lines += ["", "## Stale-pending (window open ≥14d, after_n still < min-after)",
                  "These lessons never accumulated enough post-lesson traffic. "
                  "Action: merge into a parent near-dupe via lesson_dedup.py, or "
                  "force skill/path attribution so more sessions land in after_n.", ""]
        for r in sorted(stale_pending, key=lambda x: -x.get("days_open", 0))[:40]:
            lines.append(
                f"- **{r['pattern']}** (baseline {r.get('baseline_date')}, "
                f"after_n={r['after_n']}, days_open={r.get('days_open')})"
            )
        if len(stale_pending) > 40:
            lines.append(f"- … and {len(stale_pending) - 40} more")
    if gaps:
        lines += ["", "## Eval coverage gaps (recurring pattern, no binary eval)",
                  "Add an eval in evals.py to make these measurable objectively:", ""]
        lines += [f"- {p}" for p in gaps]
    report = "\n".join(lines) + "\n"
    print(report)

    if args.dry_run:
        print("[dry-run] no files written")
        return 0

    # ── write artifacts ─────────────────────────────────────────────────────────
    atomic_write_text(DIAGNOSTICS / f"effectiveness_{today}.md", report)

    scores = {r["pattern"]: {
        "verdict": r["verdict"], "delta": r["delta"],
        "after_n": r["after_n"], "eval_covered": r["eval_covered"],
        "obj_verdict": r["obj_verdict"], "obj_delta": r["obj_delta"],
        "judge_covered": r["judge_covered"],
        "judge_verdict": r["judge_verdict"], "judge_delta": r["judge_delta"],
        "judge_after_n": r["judge_after_n"],
        "baseline_date": r.get("baseline_date", r.get("lesson_date", "")),
        "content_version": r.get("content_version", ""),
        "days_open": r.get("days_open", 0),
        "injectable": bool(r.get("injectable")),
    } for r in results}
    atomic_write_json(
        SCORES_JSON,
        {"measured_at": today, "scores": scores,
         "escalate": [r["pattern"] for r in escalate],
         "stale_pending": [r["pattern"] for r in stale_pending],
         "injectable": [r["pattern"] for r in results if r.get("injectable")],
         "eval_coverage_gaps": gaps,
         "min_after": min_after,
         "stale_pending_days": STALE_PENDING_DAYS},
    )

    # Programmatically commit verdicts to bungraph.db (Point 1: Loopback)
    for r in results:
        push_to_bungraph(
            r["pattern"], escalation_verdict(r, min_after), r["delta"], today
        )

    # Human review queue — expire and enqueue under transactional store locks.
    auto_expired_pats = expire_pending(
        REVIEW_FILE,
        today=datetime.strptime(today, "%Y-%m-%d").date(),
        max_age_days=REVIEW_EXPIRE_DAYS,
    )
    review_rows = [
        {
            "pattern": result["pattern"], "detected_at": today,
            "delta": result["delta"], "after_n": result["after_n"],
            "obj_verdict": result["obj_verdict"],
            "judge_verdict": result["judge_verdict"],
            "status": "pending", "reviewed_at": None, "reviewer": None,
            "source": "base",
        }
        for result in first_time_regressed
    ]
    added_reviews = enqueue_pending(REVIEW_FILE, review_rows)
    if auto_expired_pats:
        print(f"Auto-escalated (>14 days no review): {auto_expired_pats}")
    if added_reviews:
        print(f"Queued for human review (+{len(added_reviews)}): "
              f"{[record['pattern'] for record in added_reviews]}")

    # Voice alerts: first-time regressions (urgent/new) then known regressions (informational).
    if first_time_regressed:
        msg = (f"NEW regression queued for review: "
               f"{', '.join(r['pattern'] for r in first_time_regressed)}")
        notify(msg)
    known_regressed = [
        result["pattern"]
        for result in escalate
        if escalation_verdict(result, min_after) == "regressed"
    ]
    if known_regressed:
        msg = (f"Self-improvement: {len(known_regressed)} known regression(s) still active — "
               f"{', '.join(known_regressed)}")
        notify(msg)

    append_jsonl_many(
        EFFECT_LOG,
        [{**result, "measured_at": today} for result in results],
    )

    print(f"Wrote: {DIAGNOSTICS / f'effectiveness_{today}.md'}")
    print(f"Wrote: {SCORES_JSON}")
    print(f"Appended: {EFFECT_LOG}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
