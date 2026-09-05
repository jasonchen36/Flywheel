#!/usr/bin/env python3
"""
review_queue.py — human review gate for self-improvement regressions.

When measure_effectiveness.py detects a new first-time regression it gates the pattern
from hard escalation (Stop hook) and queues it here. Use this to inspect queued patterns
and mark them approved (escalate to enforcement) or rejected (flag lesson for revision).

Patterns auto-escalate after 14 days with no review — the gate has a time limit.

Usage:
  python review_queue.py                          # same as --list
  python review_queue.py --list
  python review_queue.py --stats
  python review_queue.py --history <pattern>
  python review_queue.py --approve <pattern>
  python review_queue.py --reject <pattern> [--reason "reason text"]
  python review_queue.py --bulk-approve [--min-age <days>] [--yes]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from harness_config import EnforcementConfig, load_enforcement_config, save_enforcement_config
from harness_paths import HARNESS_HOME
from review_store import (
    ClaimResult,
    claim_review,
    finalize_claim,
    load_reviews,
    reject_review,
    review_source,
)
from state_io import (
    append_jsonl_unlocked,
    exclusive_lock,
)

REVIEW_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"
SCORES_FILE = HARNESS_HOME / "MEMORY/STATE/effectiveness_scores.json"
AUDIT_FILE  = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/review_audit.jsonl"
REVIEW_EXPIRE_DAYS = 14
AUDIT_MAX_LINES    = 5000   # rotate audit log when it exceeds this line count


# ── I/O helpers ──────────────────────────────────────────────────────────────

def load_records() -> list[dict[str, Any]]:
    return load_reviews(REVIEW_FILE)


def load_scores() -> dict:
    if not SCORES_FILE.exists():
        return {}
    try:
        return json.loads(SCORES_FILE.read_text()).get("scores", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _rotate_audit_if_needed() -> None:
    """Rename audit log to .1 (overwrites previous backup) when it hits AUDIT_MAX_LINES."""
    if not AUDIT_FILE.exists():
        return
    if AUDIT_FILE.read_text().count("\n") >= AUDIT_MAX_LINES:
        AUDIT_FILE.rename(AUDIT_FILE.with_suffix(".jsonl.1"))


def log_audit(action: str, pattern: str, reviewer: str, today: str,
              reason: str = "", delta: float = 0.0, after_n: int = 0) -> None:
    entry = {
        "timestamp": today, "pattern": pattern, "action": action,
        "reviewer": reviewer, "reason": reason,
        "delta": delta, "after_n": after_n,
    }
    with exclusive_lock(AUDIT_FILE):
        _rotate_audit_if_needed()
        append_jsonl_unlocked(AUDIT_FILE, entry)


def _days_old(detected: str, today: str) -> int:
    try:
        return (datetime.strptime(today, "%Y-%m-%d") -
                datetime.strptime(detected, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return 0


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_list(records: list[dict], today: str) -> int:
    pending = [r for r in records if r.get("status") == "pending"]
    expiring = [r for r in pending if _days_old(r.get("detected_at", ""), today) > REVIEW_EXPIRE_DAYS]
    active = [r for r in pending if r not in expiring]
    processing = [r for r in records if r.get("status") == "processing"]
    failed = [r for r in records if r.get("status") == "action_failed"]
    approved_ct = sum(1 for r in records if r.get("status") == "approved")
    rejected_ct = sum(1 for r in records if r.get("status") == "rejected")
    auto_ct = sum(1 for r in records if r.get("status") == "auto-escalated")

    print(f"Review queue — {today}")
    print(f"  pending: {len(active)} | auto-expiring: {len(expiring)} | "
          f"processing: {len(processing)} | failed: {len(failed)} | "
          f"approved: {approved_ct} | rejected: {rejected_ct} | auto-escalated: {auto_ct}")

    scores = load_scores()

    if active:
        print("\nPending review (gated from enforcement):\n")
        for rec in active:
            age = _days_old(rec.get("detected_at", ""), today)
            expires_in = REVIEW_EXPIRE_DAYS - age
            sc = scores.get(rec["pattern"], {})
            src = rec.get("source") or "base"
            print(f"  [{rec['pattern']}] (source: {src})")
            print(f"    detected: {rec.get('detected_at')} ({age}d ago, auto-escalates in {expires_in}d)")
            d_val = rec.get("delta")
            d_str = f"{d_val:+.3f}" if isinstance(d_val, (int, float)) else "n/a"
            print(f"    delta: {d_str} | after_n: {rec.get('after_n', '?')} sessions")
            print(f"    obj_verdict: {rec.get('obj_verdict', '?')} | judge_verdict: {rec.get('judge_verdict', '?')}")
            if sc:
                print(f"    current_scores: subj={sc.get('verdict')} obj={sc.get('obj_verdict')} jdg={sc.get('judge_verdict')}")
            print(f"    → approve: python review_queue.py --approve {rec['pattern']} --source {src}")
            print(f"    → reject:  python review_queue.py --reject {rec['pattern']} --source {src} --reason '...'")
            print()

    if expiring:
        print(f"Auto-expiring (>{REVIEW_EXPIRE_DAYS}d pending — escalates next measurement run):\n")
        for rec in expiring:
            print(f"  [{rec['pattern']}] detected {rec.get('detected_at')} "
                  f"({_days_old(rec.get('detected_at', ''), today)}d ago)")

    if processing:
        print("\nApproval actions still processing:\n")
        for rec in processing:
            print(f"  [{rec.get('pattern', '?')}] source={review_source(rec)} "
                  f"started={rec.get('action_started_at', '?')}")

    if failed:
        print("\nApproval actions requiring operator resolution:\n")
        for rec in failed:
            pattern = rec.get("pattern", "?")
            source = review_source(rec)
            print(f"  [{pattern}] source={source} attempts={rec.get('action_attempts', 0)}")
            print(f"    error: {rec.get('action_error', 'unknown failure')}")
            print(f"    → retry:  python review_queue.py --approve {pattern} --source {source} --retry-failed")
            print(f"    → reject: python review_queue.py --reject {pattern} --source {source} --retry-failed --reason '...'")

    if not active and not expiring and not processing and not failed:
        print("\nQueue empty — no patterns awaiting review.")

    return 0


def cmd_stats(records: list[dict], today: str) -> int:
    """Aggregate stats and recidivism (patterns that keep recurring)."""
    by_status: Counter = Counter(r.get("status", "unknown") for r in records)
    by_pattern: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_pattern[rec.get("pattern", "?")].append(rec)

    print(f"Review queue stats — {today}")
    print(f"  total records: {len(records)}")
    for status, ct in sorted(by_status.items()):
        print(f"  {status}: {ct}")

    # Recidivism: patterns queued more than once
    recidivists = {p: recs for p, recs in by_pattern.items() if len(recs) > 1}
    if recidivists:
        print(f"\nRecidivist patterns (queued {'>1'} time):")
        for pat, recs in sorted(recidivists.items(), key=lambda x: -len(x[1])):
            statuses = " → ".join(r.get("status", "?") for r in sorted(recs, key=lambda r: r.get("detected_at", "")))
            print(f"  {pat} ×{len(recs)}: {statuses}")
    else:
        print("\nNo recidivism — each pattern queued at most once.")

    # Pending summary
    pending = [r for r in records if r.get("status") == "pending"]
    if pending:
        # delta may be null for non-measure sources (lesson_dedup, pattern_promotion, …)
        deltas = [float(r.get("delta") or 0.0) for r in pending]
        ages = [_days_old(r.get("detected_at", ""), today) for r in pending]
        print(f"\nPending summary ({len(pending)} items):")
        print(f"  avg delta: {sum(deltas)/len(deltas):+.3f} | max age: {max(ages)}d | oldest: "
              f"{min((r.get('detected_at') or '?') for r in pending)}")

    # Audit trail summary
    if AUDIT_FILE.exists():
        audit = []
        for line in AUDIT_FILE.read_text().splitlines():
            if line.strip():
                try:
                    audit.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if audit:
            audit_by_action: Counter = Counter(a.get("action") for a in audit)
            print(f"\nAudit trail ({len(audit)} entries): {dict(audit_by_action)}")

    return 0


def cmd_history(pattern: str, records: list[dict], today: str) -> int:
    """Full history for one pattern across queue + audit log."""
    pat_records = [r for r in records if r.get("pattern") == pattern]
    scores = load_scores()

    print(f"History for pattern: {pattern}\n")

    if not pat_records:
        print("  (no queue records found)")
    else:
        print(f"Queue records ({len(pat_records)}):")
        for rec in sorted(pat_records, key=lambda r: r.get("detected_at", "")):
            age = _days_old(rec.get("detected_at", ""), today)
            dlt = rec.get("delta")
            dlt_s = f"{float(dlt):+.3f}" if dlt is not None else "n/a"
            print(f"  [{rec.get('detected_at')}] status={rec.get('status')} "
                  f"delta={dlt_s} after_n={rec.get('after_n', '?')} "
                  f"age={age}d")
            if rec.get("reviewed_at"):
                print(f"    reviewed_at={rec.get('reviewed_at')} reviewer={rec.get('reviewer')} "
                      f"reason={rec.get('reason', '')!r}")

    if AUDIT_FILE.exists():
        audit_recs = []
        for line in AUDIT_FILE.read_text().splitlines():
            if line.strip():
                try:
                    a = json.loads(line)
                    if a.get("pattern") == pattern:
                        audit_recs.append(a)
                except json.JSONDecodeError:
                    pass
        if audit_recs:
            print(f"\nAudit log ({len(audit_recs)} entries):")
            for a in sorted(audit_recs, key=lambda x: x.get("timestamp", "")):
                print(f"  [{a.get('timestamp')}] {a.get('action')} by {a.get('reviewer')} "
                      f"delta={a.get('delta', 'n/a'):+.3f} reason={a.get('reason', '')!r}")

    sc = scores.get(pattern)
    if sc:
        print("\nCurrent effectiveness scores:")
        print(f"  verdict={sc.get('verdict')} obj={sc.get('obj_verdict')} jdg={sc.get('judge_verdict')}")
        print(f"  delta={sc.get('delta', 'n/a'):+.3f} obj_delta={sc.get('obj_delta', 'n/a')} "
              f"jdg_delta={sc.get('judge_delta', 'n/a')}")
        print(f"  after_n={sc.get('after_n')} eval_covered={sc.get('eval_covered')} "
              f"judge_covered={sc.get('judge_covered')}")
    else:
        print(f"\nNo current effectiveness scores for {pattern}.")

    return 0


def _print_lookup_failure(result: ClaimResult, target: str, source: str | None) -> int:
    if result.status == "ambiguous":
        print(f"AMBIGUOUS: {len(result.matches)} active records match pattern '{target}'. "
              "Re-run with --source to disambiguate:")
        for record in result.matches:
            print(f"  --source {review_source(record)!r}  "
                  f"(detected {record.get('detected_at')}, "
                  f"note: {(record.get('note') or '')[:80]})")
    elif source is not None:
        print(f"No eligible record found for pattern '{target}' with source '{source}'.")
    else:
        print(f"No eligible record found for pattern: {target}")
    return 1


def _audit_values(record: dict) -> tuple[float, int]:
    try:
        delta = float(record.get("delta") or 0.0)
    except (TypeError, ValueError):
        delta = 0.0
    try:
        after_n = int(record.get("after_n") or 0)
    except (TypeError, ValueError):
        after_n = 0
    return delta, after_n


def _run_approval_side_effect(record: dict, variant: int, today: str) -> bool:
    target = str(record.get("pattern") or "")
    source = review_source(record)
    note = str(record.get("note") or "")
    if source == "enforcement_promotion":
        return _promote_config_only_pattern(target, today)
    if source == "lesson_evolve":
        return _apply_lesson_variant(target, variant, today)
    if source == "pattern_promotion":
        return _promote_pattern_to_taxonomy(target, note, today)
    if source == "lesson_dedup":
        return _merge_dedup_pattern(target, note, today)
    return True


def cmd_approve_reject(records: list[dict], target: str, action: str,
                       reason: str, today: str, variant: int = 0,
                       source: str | None = None, *, retry_failed: bool = False,
                       reviewer: str = "USER") -> int:
    # Keep the records argument for API compatibility; the transactional store always
    # re-reads under its own lock before mutating the queue.
    del records
    if action == "rejected":
        result = reject_review(
            REVIEW_FILE, target, source=source, reviewer=reviewer, reason=reason,
            retry_failed=retry_failed,
        )
        if result.status != "claimed" or result.record is None:
            return _print_lookup_failure(result, target, source)
        record = result.record
        delta, after_n = _audit_values(record)
        log_audit("rejected", target, reviewer, today, reason=reason,
                  delta=delta, after_n=after_n)
        print(f"Rejected: {target} (reviewed_at: {today})")
        record_source = review_source(record)
        if record_source == "enforcement_promotion":
            print("Left at 'warn' in enforcement_config.json — no lesson to revise for config-only patterns.")
        elif record_source == "pattern_promotion":
            print(f"Pattern '{target}' NOT promoted to PATTERN_KEYWORDS. Ledger entries stay pending.")
        elif record_source == "held_out_regression":
            print(f"No action taken on lesson_autogen_{target}.md — flagged side-effect dismissed as noise.")
        elif record_source == "lesson_dedup":
            print(f"No merge performed for '{target}'. Both lesson files kept as-is.")
        elif record_source == "lesson_evolve":
            print(f"No variant applied to lesson_autogen_{target}.md. Lesson text unchanged.")
        else:
            print(f"NOTE: revise lesson_autogen_{target}.md, then re-run self_improve.py --regen {target}")
        return 0

    claim = claim_review(
        REVIEW_FILE,
        target,
        source=source,
        reviewer=reviewer,
        retry_failed=retry_failed,
    )
    if claim.status != "claimed" or claim.record is None:
        return _print_lookup_failure(claim, target, source)
    record = claim.record
    claim_id = str(record["claim_id"])
    error = ""
    try:
        success = _run_approval_side_effect(record, variant, today)
        if not success:
            error = f"{review_source(record)} approval side effect returned failure"
    except Exception as exc:
        success = False
        error = f"{type(exc).__name__}: {exc}"
    finalized = finalize_claim(
        REVIEW_FILE, claim_id, success=success, error=error
    )
    if finalized is None:
        print(f"ERROR: review claim {claim_id} disappeared before finalization.")
        return 2
    delta, after_n = _audit_values(finalized)
    if not success:
        log_audit("action-failed", target, reviewer, today, reason=error,
                  delta=delta, after_n=after_n)
        print(f"ACTION FAILED: {target}: {error}. Re-run with --retry-failed after fixing the cause.")
        return 2

    log_audit("approved", target, reviewer, today, reason=reason,
              delta=delta, after_n=after_n)
    print(f"Approved: {target} (reviewed_at: {today})")
    record_source = review_source(finalized)
    if record_source == "held_out_regression":
        print(f"NOTE: review lesson_autogen_{target}.md for the side-effect described above — "
              "revise or revert manually, this queue only flags.")
    elif record_source == "base":
        print("Pattern enters escalation on next measurement run (session end).")
    return 0


def _promote_config_only_pattern(pattern: str, today: str) -> bool:
    """Approving a CONFIG_ONLY_PATTERNS record (see enforcement_promotion.py) has no
    escalate[] consumer to act on — the mode lives only in enforcement_config.json
    overrides, so approval must edit that file directly to take effect."""
    from enforcement_promotion import CONFIG_ONLY_PATTERNS, CONFIG_JSON  # local import: avoid cycle at module load
    if pattern not in CONFIG_ONLY_PATTERNS:
        print(f"WARNING: {pattern} marked source=enforcement_promotion but not in "
              f"CONFIG_ONLY_PATTERNS — not editing enforcement_config.json. Check for drift.")
        return False
    config_result = load_enforcement_config(CONFIG_JSON)
    if not config_result.ok:
        print("WARNING: invalid enforcement_config.json; refusing promotion: "
              + "; ".join(config_result.errors))
        return False
    overrides = dict(config_result.config.overrides)
    overrides[pattern] = "block"
    save_enforcement_config(
        CONFIG_JSON, EnforcementConfig(config_result.config.enabled, overrides)
    )
    log_audit("config-promoted", pattern, "USER", today,
              reason="overrides[pattern] warn -> block via review approval")
    print(f"enforcement_config.json: overrides['{pattern}'] = 'block' (was 'warn'). Effective immediately — no restart needed, EnforcementGate reads this file fresh every Stop hook invocation.")
    return True


def _promote_pattern_to_taxonomy(pattern: str, note: str, today: str) -> bool:
    """Approving a pattern_promotion.py record: mechanically append the new pattern to
    PATTERN_KEYWORDS in self_improve.py. Keywords are parsed back out of the review note
    (the same list the human just reviewed) so approval doesn't silently re-derive a
    DIFFERENT keyword list than what was shown."""
    import re as _re
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pattern_promotion import promote_to_taxonomy, load_ledger, write_ledger  # local import: avoid cycle

    m = _re.search(r"Suggested keywords for PATTERN_KEYWORDS:\s*(\[.*?\])", note)
    keywords: list[str] = []
    if m:
        try:
            keywords = json.loads(m.group(1).replace("'", '"'))
        except (json.JSONDecodeError, ValueError):
            keywords = []
    if not keywords:
        keywords = [pattern]  # fallback: at minimum match the pattern name itself

    ok = promote_to_taxonomy(pattern, keywords)
    if not ok:
        print(f"WARNING: could not locate PATTERN_KEYWORDS marker in self_improve.py — "
              f"pattern '{pattern}' NOT appended. Add it manually.")
        return False

    # Mark this label's ledger entries as promoted so pattern_promotion.py stops
    # re-queuing it and self_improve.py's classifier picks it up on next run.
    ledger = load_ledger()
    for rec in ledger:
        if rec.get("label") == pattern and rec.get("status") == "pending":
            rec["status"] = "promoted"
    write_ledger(ledger)

    log_audit("taxonomy-promoted", pattern, "USER", today,
              reason=f"appended to PATTERN_KEYWORDS with keywords={keywords}")
    print(f"self_improve.py: PATTERN_KEYWORDS['{pattern}'] = {keywords} (appended, effective next run).")
    return True


def _apply_lesson_variant(target: str, variant: int, today: str) -> bool:
    """Approving a lesson_evolve.py record: apply the chosen candidate variant
    (default 0) to the lesson file. See lesson_evolve.apply_variant for the mechanics."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lesson_evolve import apply_variant  # local import: avoid cycle
    ok = apply_variant(target, variant, today)
    if ok:
        log_audit("lesson-evolved", target, "USER", today,
                  reason=f"applied variant {variant}")
    return ok


def _merge_dedup_pattern(target: str, note: str, today: str) -> bool:
    """Approving a lesson_dedup.py record: parse survivor/loser out of the review note
    (target is 'survivor<-loser') and perform the file merge via lesson_dedup.merge_lessons."""
    import re as _re
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lesson_dedup import merge_lessons  # local import: avoid cycle

    m = _re.match(r"^(.+)<-(.+)$", target)
    if not m:
        print(f"WARNING: could not parse survivor/loser from target '{target}'. No merge performed.")
        return False
    survivor, loser = m.group(1), m.group(2)
    ok = merge_lessons(survivor, loser, today)
    if ok:
        log_audit("lesson-merged", target, "USER", today,
                  reason=f"merged lesson_autogen_{loser}.md into lesson_autogen_{survivor}.md")
    return ok


# Sources safe for unattended approval (mutations are local harness files only —
# never posts, never touches prod, never edits shared repos).
AUTO_DRAIN_SOURCES = frozenset({
    "held_out_regression",  # flag-only; no lesson edit
    "lesson_dedup",         # merge near-duplicate lesson files
    "lesson_evolve",        # apply best candidate variant to lesson text
    "pattern_promotion",    # append PATTERN_KEYWORDS in self_improve.py
    "base",                 # allow measure_effectiveness escalation path
    "enforcement_promotion",  # config-only warn→block for known patterns
})


def cmd_auto_drain(records: list[dict], min_age: int, sources_csv: str, today: str) -> int:
    """Autonomous review-queue drain — closes the self-improvement loop without a human click.

    Safe by construction:
      - only status=pending records aged ≥ min_age
      - only sources in AUTO_DRAIN_SOURCES ∩ --auto-sources
      - never auto-rejects (false-positive lessons stay until human kills them)
      - each approval reuses cmd_approve_reject (same mechanical side-effects)
      - audit log records reviewer='auto-drain'
    """
    allowed = {s.strip() for s in sources_csv.split(",") if s.strip()}
    allowed &= AUTO_DRAIN_SOURCES
    if not allowed:
        print("auto-drain: no allowed sources after filter. Nothing to do.")
        return 0

    pending = [r for r in records if r.get("status") == "pending"]
    eligible = []
    for rec in pending:
        src = rec.get("source") or "base"
        if src not in allowed:
            continue
        if _days_old(rec.get("detected_at", ""), today) < min_age:
            continue
        eligible.append(rec)

    if not eligible:
        print(f"auto-drain: 0 eligible (min_age={min_age}d, sources={sorted(allowed)})")
        return 0

    print(f"auto-drain: approving {len(eligible)} record(s) "
          f"(min_age={min_age}d, sources={sorted(allowed)})")
    ok = 0
    fail = 0
    for rec in eligible:
        pattern = rec.get("pattern", "")
        source = review_source(rec)
        rc = cmd_approve_reject(
            [], pattern, "approved", "auto-drain", today,
            variant=0, source=source, reviewer="auto-drain",
        )
        if rc == 0:
            ok += 1
        else:
            fail += 1
            print(f"  skip {pattern} source={source} rc={rc}")
    print(f"auto-drain: done ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


def cmd_bulk_approve(records: list[dict], min_age: int, yes: bool, today: str) -> int:
    """Approve all pending patterns at least min_age days old."""
    pending = [r for r in records if r.get("status") == "pending"]
    eligible = [r for r in pending if _days_old(r.get("detected_at", ""), today) >= min_age]

    if not eligible:
        print(f"No pending patterns aged ≥{min_age}d. Nothing to approve.")
        return 0

    print(f"Bulk approve {len(eligible)} pattern(s) (aged ≥{min_age}d):")
    for rec in eligible:
        age = _days_old(rec.get("detected_at", ""), today)
        dlt = float(rec.get("delta") or 0.0)
        print(f"  [{rec['pattern']}] detected {rec.get('detected_at')} ({age}d, delta={dlt:+.3f})")

    if not yes:
        try:
            confirm = input(f"\nApprove all {len(eligible)}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if confirm not in ("y", "yes"):
            print("Aborted.")
            return 1

    approved_pats: list[str] = []
    failed_pats: list[str] = []
    for rec in eligible:
        pattern = str(rec.get("pattern") or "")
        rc = cmd_approve_reject(
            [], pattern, "approved", "bulk-approve", today,
            source=review_source(rec), reviewer="USER",
        )
        if rc == 0:
            approved_pats.append(pattern)
        else:
            failed_pats.append(pattern)

    print(f"Approved {len(approved_pats)}: {approved_pats}")
    if failed_pats:
        print(f"Failed {len(failed_pats)}: {failed_pats}")
    return 0 if not failed_pats else 1


def cmd_summary(records: list[dict], today: str) -> int:
    """Compact dashboard: status counts + pending sorted by urgency + top action."""
    pending = [r for r in records if r.get("status") == "pending"]
    expiring = [r for r in pending if _days_old(r.get("detected_at", ""), today) > REVIEW_EXPIRE_DAYS]
    active = sorted(
        [r for r in pending if r not in expiring],
        key=lambda r: _days_old(r.get("detected_at", ""), today),
        reverse=True,   # oldest (fewest days left) first
    )
    by_pattern: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_pattern[rec.get("pattern", "?")].append(rec)
    recidivists = sum(1 for recs in by_pattern.values() if len(recs) > 1)

    approved_ct = sum(1 for r in records if r.get("status") == "approved")
    rejected_ct = sum(1 for r in records if r.get("status") == "rejected")
    processing_ct = sum(1 for r in records if r.get("status") == "processing")
    failed_ct = sum(1 for r in records if r.get("status") == "action_failed")
    auto_ct = sum(1 for r in records if r.get("status") == "auto-escalated")

    audit_ct = 0
    if AUDIT_FILE.exists():
        audit_ct = AUDIT_FILE.read_text().count("\n")

    w = 56
    print("=" * w)
    print(f"  Self-Improvement Review Queue  —  {today}")
    print("=" * w)
    print(f"  pending={len(active)}  expiring={len(expiring)}  processing={processing_ct}"
          f"  failed={failed_ct}  approved={approved_ct}  rejected={rejected_ct}  auto={auto_ct}")
    print(f"  recidivists={recidivists}  audit_entries={audit_ct}")
    print("-" * w)

    if active or expiring:
        print("  PENDING  (sorted by urgency — fewest days left first)\n")
        for rec in active[:5]:   # cap at 5 in summary view; --list shows all
            age = _days_old(rec.get("detected_at", ""), today)
            days_left = REVIEW_EXPIRE_DAYS - age
            verdict_tag = rec.get("obj_verdict") or rec.get("judge_verdict") or "?"
            print(f"  {rec['pattern']:<32} δ={rec.get('delta', 0.0):+.3f}  "
                  f"{days_left}d left  [{verdict_tag}]")
        if len(active) > 5:
            print(f"  ... +{len(active) - 5} more (run --list)")
        for rec in expiring:
            age = _days_old(rec.get("detected_at", ""), today)
            print(f"  {rec['pattern']:<32} ⚡ AUTO-EXPIRING ({age}d old)")

        # Most urgent action
        most_urgent = (expiring or active)[0]
        action_verb = "--approve" if not expiring else "--approve (or it auto-escalates)"
        print()
        print(f"  Next:  python review_queue.py {action_verb} {most_urgent['pattern']}")
    else:
        print("  Queue empty — nothing awaiting review.")

    print("=" * w)
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Human review gate for first-time regressions")
    ap.add_argument("--list", dest="list_", action="store_true", help="show queue items (default)")
    ap.add_argument("--summary", action="store_true", help="compact dashboard: counts + top pending")
    ap.add_argument("--stats", action="store_true", help="aggregate stats and recidivism")
    ap.add_argument("--history", metavar="PATTERN", help="full history for one pattern")
    ap.add_argument("--approve", metavar="PATTERN", help="approve pattern for escalation")
    ap.add_argument("--reject", metavar="PATTERN", help="reject pattern (flag lesson for revision)")
    ap.add_argument("--reason", default="", help="reason for rejection (used with --reject)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retry an action_failed approval after fixing its cause")
    ap.add_argument("--variant", type=int, default=0,
                    help="for lesson_evolve approvals: which candidate variant to apply (default 0)")
    ap.add_argument("--source", default=None,
                    help="disambiguate when multiple pending records share a pattern name "
                         "(e.g. 'base', 'lesson_evolve', 'lesson_dedup', 'pattern_promotion', "
                         "'held_out_regression', 'enforcement_promotion')")
    ap.add_argument("--bulk-approve", action="store_true", help="approve all eligible pending patterns")
    ap.add_argument("--min-age", type=int, default=0,
                    help="with --bulk-approve/--auto-drain: only act on patterns pending ≥N days (default: 0)")
    ap.add_argument("--yes", action="store_true", help="skip confirmation for bulk operations")
    ap.add_argument(
        "--auto-drain",
        action="store_true",
        help=(
            "Autonomous drain: auto-approve safe sources without human click. "
            "Tiers: held_out_regression (flag-only), lesson_dedup (merge near-dupes), "
            "lesson_evolve (apply variant 0), pattern_promotion (taxonomy append), "
            "base (enforcement escalation). Never auto-rejects. "
            "Respects --min-age (default 0 when used alone)."
        ),
    )
    ap.add_argument(
        "--auto-sources",
        default="held_out_regression,lesson_dedup,lesson_evolve,pattern_promotion,base",
        help="comma list of sources --auto-drain may approve (default: all known safe tiers)",
    )
    args = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    records = load_records()

    if args.approve or args.reject:
        target = args.approve or args.reject
        action = "approved" if args.approve else "rejected"
        return cmd_approve_reject(
            records, target, action, args.reason, today, args.variant, args.source,
            retry_failed=args.retry_failed,
        )

    if args.auto_drain:
        return cmd_auto_drain(records, args.min_age, args.auto_sources, today)

    if args.bulk_approve:
        return cmd_bulk_approve(records, args.min_age, args.yes, today)

    if args.summary:
        return cmd_summary(records, today)

    if args.stats:
        return cmd_stats(records, today)

    if args.history:
        return cmd_history(args.history, records, today)

    return cmd_list(records, today)


if __name__ == "__main__":
    raise SystemExit(main())
