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
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME

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


def load_review_queue() -> list[dict]:
    if not REVIEW_FILE.exists():
        return []
    out = []
    for line in REVIEW_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_review_queue(records: list[dict]) -> None:
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_FILE, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="queue flagged pairs for human review")
    ap.add_argument("--min-side-n", type=int, default=MIN_SIDE_N)
    args = ap.parse_args()

    entries = load_all_ratings(RATINGS_FILE)
    for e in entries:
        e.patterns = classify_entry(e)

    lessons = discover_lessons(MEMORY_DIR)   # pattern -> {baseline_date, ...}
    if not lessons:
        print("[held_out_regression] No lessons found — run self_improve.py first.")
        return 0

    all_patterns = sorted({p for e in entries for p in e.patterns if p != "other"})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    flagged: list[dict] = []
    for p, meta in sorted(lessons.items()):
        # Compat: discover_lessons used to return str dates; now returns meta dicts.
        ldate = meta.get("baseline_date") if isinstance(meta, dict) else meta
        if not ldate:
            continue
        before_pool = [e for e in entries if entry_date(e.timestamp) < ldate]
        after_pool = [e for e in entries if entry_date(e.timestamp) >= ldate]
        if len(before_pool) < args.min_side_n or len(after_pool) < args.min_side_n:
            continue

        for q in all_patterns:
            if q == p:
                continue

            def rate(pool, side_effect_pattern=q):
                if not pool:
                    return 0.0
                hits = sum(
                    1
                    for entry in pool
                    if entry.rating <= LOW and side_effect_pattern in entry.patterns
                )
                return hits / len(pool)

            b, a = rate(before_pool), rate(after_pool)
            is_regression = (
                (b > 0 and a >= b * REL_REGRESSION and (a - b) >= MIN_ABS_DELTA)
                or (b == 0 and a >= NEW_PATTERN_FLOOR)
            )
            if is_regression:
                flagged.append({
                    "offending_lesson": p, "side_effect_pattern": q,
                    "lesson_date": ldate, "before_rate": round(b, 4),
                    "after_rate": round(a, 4), "delta": round(a - b, 4),
                    "before_n": len(before_pool), "after_n": len(after_pool),
                })

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

    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS / f"held_out_regression_{today}.md").write_text(report)

    if not args.apply or not flagged:
        if not args.apply and flagged:
            print("[held_out_regression] Flagged pairs found. Re-run with --apply to queue for human review.")
        return 0

    # Queue for human review — one entry per OFFENDING lesson (dedup: worst side-effect wins).
    records = load_review_queue()
    already = {r["pattern"] for r in records
              if r.get("status") == "pending" and r.get("source") == "held_out_regression"}
    worst_by_lesson: dict[str, dict] = {}
    for f in flagged:
        cur = worst_by_lesson.get(f["offending_lesson"])
        if cur is None or f["delta"] > cur["delta"]:
            worst_by_lesson[f["offending_lesson"]] = f

    queued = []
    for p, f in worst_by_lesson.items():
        if p in already:
            continue
        records.append({
            "pattern": p, "detected_at": today, "delta": f["delta"], "after_n": f["after_n"],
            "obj_verdict": "n/a", "judge_verdict": "n/a", "status": "pending",
            "reviewed_at": None, "reviewer": None, "source": "held_out_regression",
            "note": f"Lesson for '{p}' (landed {f['lesson_date']}) coincides with a side-effect "
                    f"regression on unrelated pattern '{f['side_effect_pattern']}' "
                    f"({f['before_rate']:.3f} -> {f['after_rate']:.3f}). Review whether the "
                    f"lesson text for '{p}' should be revised or reverted.",
        })
        queued.append(p)

    if queued:
        write_review_queue(records)
        print(f"[held_out_regression] Queued {len(queued)} lesson(s) for human review: {queued}")
    else:
        print("[held_out_regression] All flagged pairs already queued or reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
