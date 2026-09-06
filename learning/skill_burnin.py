#!/usr/bin/env python3
"""skill_burnin.py — measure-after for skill_autofix without waiting forever.

Modes:
  --status              show post_n / baseline for active edits
  --provisional-measure use ALL attributed sessions for skill (not only post-applied)
                        as a provisional signal; confirm/revert with status=provisional_*
  --apply               write ledger changes from provisional measure
  --json

Honest note: provisional measure uses historical sessions that may predate the
guardrail. Real post_n still increments on SessionEnd via skill_autofix.evaluate_active.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_autofix import (
    load_ledger, save_ledger, skill_sessions, fail_rate, MIN_AFTER,
    LEDGER_FILE, DIAG_DIR,
)
from measure_effectiveness import verdict_for
from self_improve import load_all_ratings, RATINGS_FILE
from state_io import atomic_write_text, exclusive_lock

STALL_DAYS = 14  # active edit older than this with zero post-apply traffic → park


def valid_edits(ledger: dict) -> list[dict]:
    edits = ledger.get("edits")
    if not isinstance(edits, list):
        return []
    return [
        edit
        for edit in edits
        if isinstance(edit, dict)
        and isinstance(edit.get("skill"), str)
        and edit["skill"]
        and isinstance(edit.get("pattern"), str)
        and edit["pattern"]
    ]


def safe_rate(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def resolve_stall(
    ledger: dict, entries: list, today: str, apply: bool, *, ledger_locked: bool = False
) -> list[str]:
    """Park active edits whose skill has had no rated sessions since apply.

    2026-08: 5 edits sat active 27d with post_n=0 because the rating sensor
    flatlined — they could never confirm or revert. Stalled edits park with an
    explicit reason and reactivate automatically once post-apply traffic exists.
    """
    from datetime import date
    changes: list[str] = []
    try:
        today_d = date.fromisoformat(today)
    except ValueError:
        return []
    for ed in valid_edits(ledger):
        st = ed.get("status")
        if st not in {"active", "stalled"}:
            continue
        post = skill_sessions(entries, ed["skill"], since=ed.get("applied"))
        if st == "active":
            try:
                age = (today_d - date.fromisoformat(str(ed.get("applied", ""))[:10])).days
            except ValueError:
                continue
            if age > STALL_DAYS and not post:
                all_s = skill_sessions(entries, ed["skill"])
                newest = max((str(getattr(e, "timestamp", "")) for e in all_s), default=None)
                changes.append(
                    f"STALL /{ed['skill']} ({ed['pattern']}) — active {age}d, "
                    f"0 post-apply sessions (newest skill session: {newest or 'never'})"
                )
                if apply:
                    ed["status"] = "stalled"
                    ed["stalled_at"] = today
                    ed["stalled_reason"] = (
                        f"no post-apply skill traffic in {age}d; newest session {newest or 'never'}"
                    )
        elif st == "stalled" and post:
            changes.append(
                f"REACTIVATE /{ed['skill']} ({ed['pattern']}) — {len(post)} post-apply sessions now exist"
            )
            if apply:
                ed["status"] = "active"
                ed["reactivated_at"] = today
                ed.pop("stalled_reason", None)
    if apply and changes:
        save_ledger(ledger, locked=ledger_locked)
    return changes


def _run(args: argparse.Namespace, *, ledger_locked: bool) -> int:
    ledger = load_ledger()
    entries = load_all_ratings(RATINGS_FILE)
    today = datetime.now().strftime("%Y-%m-%d")

    if args.resolve_stall:
        changes = resolve_stall(
            ledger, entries, today, args.apply, ledger_locked=ledger_locked
        )
        print("\n".join(changes) or "no stalled edits")
        if changes and not args.apply:
            print("[dry] re-run with --apply to write stall/reactivate transitions")
        return 0
    active = [edit for edit in valid_edits(ledger) if edit.get("status") == "active"]
    rows = []
    for ed in active:
        post = skill_sessions(entries, ed["skill"], since=ed.get("applied"))
        all_s = skill_sessions(entries, ed["skill"])
        post_rate, _ = fail_rate(post)
        all_rate, all_low = fail_rate(all_s)
        baseline_rate = safe_rate(ed.get("baseline_fail_rate"))
        prov_v = verdict_for(baseline_rate, all_rate, max(len(all_s), MIN_AFTER), MIN_AFTER)
        rows.append({
            "skill": ed["skill"], "pattern": ed["pattern"],
            "baseline_fail_rate": baseline_rate,
            "post_n": len(post), "post_fail_rate": round(post_rate, 3) if post else None,
            "all_n": len(all_s), "all_fail_rate": round(all_rate, 3),
            "provisional_verdict": prov_v,
            "needs_live_sessions": len(post) < MIN_AFTER,
        })
    if args.status or not args.provisional_measure:
        if args.json:
            print(json.dumps({"active": rows}, indent=2))
        else:
            print(f"Skill burn-in status — {today}")
            for r in rows:
                print(f"  /{r['skill']} pattern={r['pattern']} post={r['post_n']}/{MIN_AFTER} "
                      f"all_rate={r['all_fail_rate']} base={r['baseline_fail_rate']} "
                      f"prov={r['provisional_verdict']}")
        if not args.provisional_measure:
            return 0

    changes = []
    if args.provisional_measure and args.apply:
        for ed in active:
            all_s = skill_sessions(entries, ed["skill"])
            if len(all_s) < MIN_AFTER:
                changes.append(f"skip /{ed['skill']} — only {len(all_s)} total sessions")
                continue
            rate, _ = fail_rate(all_s)
            baseline_rate = safe_rate(ed.get("baseline_fail_rate"))
            v = verdict_for(baseline_rate, rate, len(all_s), MIN_AFTER)
            ed["provisional_fail_rate"] = round(rate, 3)
            ed["provisional_verdict"] = v
            ed["provisional_measured"] = today
            ed["provisional_n"] = len(all_s)
            if v in ("working", "improving", "resolved") and rate < baseline_rate - 0.005:
                ed["status"] = "confirmed"
                ed["confirmed"] = today
                ed["confirm_mode"] = "provisional_all_sessions"
                changes.append(
                    f"PROVISIONAL-CONFIRM /{ed['skill']} — {v} "
                    f"(all_rate={rate:.2f} < base={baseline_rate:.2f}, n={len(all_s)})"
                )
            else:
                changes.append(
                    f"HOLD /{ed['skill']} — provisional {v} "
                    f"(all_rate={rate:.2f} base={baseline_rate:.2f}); need live post sessions"
                )
        save_ledger(ledger, locked=ledger_locked)
        atomic_write_text(
            DIAG_DIR / f"skill_burnin_{today}.md",
            f"# Skill burn-in {today}\n\n" + "\n".join(f"- {c}" for c in changes) + "\n"
        )
        print("\n".join(changes) or "no changes")
        print(f"ledger → {LEDGER_FILE}")
    else:
        print("[dry] re-run with --apply to write provisional confirms")
        for r in rows:
            print(f"  would evaluate /{r['skill']} → {r['provisional_verdict']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--provisional-measure", action="store_true")
    ap.add_argument("--resolve-stall", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.apply:
        try:
            with exclusive_lock(LEDGER_FILE):
                return _run(args, ledger_locked=True)
        except TimeoutError as exc:
            print(f"skill burn-in ledger unavailable: {exc}", file=sys.stderr)
            return 1
    return _run(args, ledger_locked=False)

if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
