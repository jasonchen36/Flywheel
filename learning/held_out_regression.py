#!/usr/bin/env python3
"""
held_out_regression.py — held-out safety check for the self-improvement loop.

measure_effectiveness.py already checks whether a lesson's OWN target pattern
recurred less after the lesson was written (before/after on THAT pattern). It never
checks the held-out set: did some OTHER, unrelated pattern get WORSE after this lesson
landed? A verbose or conflicting instruction can crowd context, push out an unrelated
guardrail, or contradict another rule — Self-Harness (Zhang et al. 2026) is explicit
about this: "candidates accepted only if no regression on both held-in AND held-out
splits." This script is the held-out half; measure_effectiveness.py is the held-in half.

METHOD (per lesson pattern P with date D, for every OTHER observed pattern Q != P):
  before_rate(Q) = low-rated sessions matching Q, date < D,  / all sessions before D
  after_rate(Q)  = low-rated sessions matching Q, date >= D, / all sessions after  D
  flagged if:
    - before_rate(Q) > 0 and after_rate(Q) >= before_rate(Q) * REL_REGRESSION
      and after_rate(Q) - before_rate(Q) >= MIN_ABS_DELTA        (relative jump, not noise)
    - OR before_rate(Q) == 0 and after_rate(Q) >= NEW_PATTERN_FLOOR   (pattern emerged fresh)
  Requires >= min-side-n sessions on both sides of D to avoid small-sample noise.

SAFE BY CONSTRUCTION: report-only by default. --apply queues flagged (P, Q) pairs into
the SAME pending_human_review.jsonl review_queue.py already manages (source=
"held_out_regression", pattern=P — the OFFENDING lesson, since P's edit is the candidate
to revise/revert). Never edits, reverts, or auto-escalates a lesson.

Usage:
  python3 held_out_regression.py             # report only, no writes
  python3 held_out_regression.py --apply     # also queue flagged pairs for human review
  python3 held_out_regression.py --min-side-n 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from review_store import enqueue_pending
from state_io import atomic_write_text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import (  # noqa: E402
    load_all_ratings, classify_entry, RATINGS_FILE, MEMORY_DIR, DIAGNOSTICS,
)
from measure_effectiveness import discover_lessons, entry_date  # noqa: E402

REVIEW_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"

LOW = 4
MIN_SIDE_N = 5            # min sessions on each side of D to trust a rate
REL_REGRESSION = 1.5      # after >= before * this factor
MIN_ABS_DELTA = 0.05      # ...and the absolute jump must be at least this
NEW_PATTERN_FLOOR = 0.10  # pattern never seen before D, now at least this rate after


def valid_date(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def failure_rate(pool: list, pattern: str) -> float:
    if not pool:
        return 0.0
    hits = sum(
        1
        for entry in pool
        if isinstance(getattr(entry, "rating", None), int)
        and entry.rating <= LOW
        and pattern in getattr(entry, "patterns", [])
    )
    return hits / len(pool)


def find_regressions(entries: list, lessons: dict, min_side_n: int) -> list[dict]:
    all_patterns = sorted({
        pattern
        for entry in entries
        for pattern in getattr(entry, "patterns", [])
        if isinstance(pattern, str) and pattern != "other"
    })
    flagged: list[dict] = []
    for lesson_pattern, meta in sorted(lessons.items()):
        raw_date = meta.get("baseline_date") if isinstance(meta, dict) else meta
        lesson_date = valid_date(raw_date)
        if lesson_date is None:
            continue
        before_pool = [
            entry for entry in entries if entry_date(getattr(entry, "timestamp", "")) < lesson_date
        ]
        after_pool = [
            entry for entry in entries if entry_date(getattr(entry, "timestamp", "")) >= lesson_date
        ]
        if len(before_pool) < min_side_n or len(after_pool) < min_side_n:
            continue
        for side_effect_pattern in all_patterns:
            if side_effect_pattern == lesson_pattern:
                continue
            before_rate = failure_rate(before_pool, side_effect_pattern)
            after_rate = failure_rate(after_pool, side_effect_pattern)
            is_regression = (
                before_rate > 0
                and after_rate >= before_rate * REL_REGRESSION
                and (after_rate - before_rate) >= MIN_ABS_DELTA
            ) or (
                before_rate == 0 and after_rate >= NEW_PATTERN_FLOOR
            )
            if is_regression:
                flagged.append({
                    "offending_lesson": lesson_pattern,
                    "side_effect_pattern": side_effect_pattern,
                    "lesson_date": lesson_date,
                    "before_rate": round(before_rate, 4),
                    "after_rate": round(after_rate, 4),
                    "delta": round(after_rate - before_rate, 4),
                    "before_n": len(before_pool),
                    "after_n": len(after_pool),
                })
    return flagged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="queue flagged pairs for human review")
    ap.add_argument("--min-side-n", type=int, default=MIN_SIDE_N)
    args = ap.parse_args(argv)
    if args.min_side_n <= 0:
        print("[held_out_regression] min-side-n must be positive")
        return 2

    entries = load_all_ratings(RATINGS_FILE)
    for e in entries:
        e.patterns = classify_entry(e)

    lessons = discover_lessons(MEMORY_DIR)   # pattern -> {baseline_date, ...}
    if not lessons:
        print("[held_out_regression] No lessons found — run self_improve.py first.")
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    flagged = find_regressions(entries, lessons, args.min_side_n)

    # ── report ────────────────────────────────────────────────────────────────
    lines = [f"# Held-Out Regression Check — {today}", "",
             "Did an unrelated pattern get WORSE after a lesson landed for a "
             "different pattern? (held-out half of the self-improvement safety check; "
             "measure_effectiveness.py covers the held-in half.)", "",
             f"Lessons checked: {len(lessons)} | Flagged pairs: {len(flagged)}", ""]
    if flagged:
        lines += ["| offending lesson | side-effect pattern | lesson date | before | after | delta |",
                  "|---|---|---|---|---|---|"]
        for f in sorted(flagged, key=lambda x: -x["delta"]):
            lines.append(f"| {f['offending_lesson']} | {f['side_effect_pattern']} | "
                        f"{f['lesson_date']} | {f['before_rate']:.3f} | {f['after_rate']:.3f} | "
                        f"{f['delta']:+.3f} |")
    else:
        lines.append("No held-out regressions detected.")
    report = "\n".join(lines) + "\n"
    print(report)

    atomic_write_text(DIAGNOSTICS / f"held_out_regression_{today}.md", report)

    if not args.apply or not flagged:
        if not args.apply and flagged:
            print("[held_out_regression] Flagged pairs found. Re-run with --apply to queue for human review.")
        return 0

    # Queue for human review — one entry per OFFENDING lesson (dedup: worst side-effect wins).
    worst_by_lesson: dict[str, dict] = {}
    for f in flagged:
        cur = worst_by_lesson.get(f["offending_lesson"])
        if cur is None or f["delta"] > cur["delta"]:
            worst_by_lesson[f["offending_lesson"]] = f

    pending_rows: list[dict] = []
    for p, f in worst_by_lesson.items():
        pending_rows.append({
            "pattern": p, "detected_at": today, "delta": f["delta"], "after_n": f["after_n"],
            "obj_verdict": "n/a", "judge_verdict": "n/a", "status": "pending",
            "reviewed_at": None, "reviewer": None, "source": "held_out_regression",
            "note": f"Lesson for '{p}' (landed {f['lesson_date']}) coincides with a side-effect "
                    f"regression on unrelated pattern '{f['side_effect_pattern']}' "
                    f"({f['before_rate']:.3f} -> {f['after_rate']:.3f}). Review whether the "
                    f"lesson text for '{p}' should be revised or reverted.",
        })
    added = enqueue_pending(REVIEW_FILE, pending_rows)
    queued = [record["pattern"] for record in added]
    if queued:
        print(f"[held_out_regression] Queued {len(queued)} lesson(s) for human review: {queued}")
    else:
        print("[held_out_regression] All flagged pairs already queued or reviewed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
