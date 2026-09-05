#!/usr/bin/env python3
"""Retire / archive zombie autogen lessons (Phase F).

Retires lesson_autogen_*.md when:
  - effectiveness verdict is pending or stale-pending, AND
  - occurrence_count is low OR after_n stuck with days_open >= retire_days, AND
  - pattern is NOT in PROTECTED (ALWAYS_ON / enforceable seeds)

Also backfills baseline_date from first_seen when missing.

Usage:
  python3 lesson_retire.py              # report only
  python3 lesson_retire.py --apply      # move to STATE/lesson_archive/
  python3 lesson_retire.py --backfill-baseline-only
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME, LESSONS_DIR

MEM = LESSONS_DIR
STATE = HARNESS_HOME / "MEMORY/STATE"
ARCHIVE = STATE / "lesson_archive"
SCORES = STATE / "effectiveness_scores.json"
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"

PROTECTED = frozenset({
    "unverified_completion", "unverified_claims", "incomplete_analysis",
    "blind_retry", "duplicate_approval", "tool_misuse", "guardrail_bypass",
    "silent_completion", "approved_without_verification", "pr_review_failure",
})


def load_scores() -> dict:
    if not SCORES.exists():
        return {}
    try:
        return json.loads(SCORES.read_text()).get("scores") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def parse_meta(path: Path) -> dict:
    txt = path.read_text(errors="replace")
    pat = path.stem.replace("lesson_autogen_", "")
    occ = 0
    m = re.search(r"occurrence_count:\s*(\d+)", txt)
    if m:
        occ = int(m.group(1))
    fs = ""
    m = re.search(r"first_seen:\s*(\d{4}-\d{2}-\d{2})", txt)
    if m:
        fs = m.group(1)
    has_base = bool(re.search(r"baseline_date:\s*\d{4}-\d{2}-\d{2}", txt))
    return {"pattern": pat, "path": path, "occ": occ, "first_seen": fs,
            "has_baseline": has_base, "text": txt}


def backfill_baseline(meta: dict) -> bool:
    if meta["has_baseline"] or not meta["first_seen"]:
        return False
    txt = meta["text"]
    txt2 = re.sub(
        r"(first_seen:\s*\d{4}-\d{2}-\d{2})",
        rf"\1\n  baseline_date: {meta['first_seen']}",
        txt,
        count=1,
    )
    if txt2 != txt:
        meta["path"].write_text(txt2)
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backfill-baseline-only", action="store_true")
    ap.add_argument("--retire-days", type=int, default=14)
    ap.add_argument("--max-occ", type=int, default=3,
                    help="max occurrence_count to consider a zombie")
    args = ap.parse_args()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scores = load_scores()
    files = sorted(MEM.glob("lesson_autogen_*.md")) if MEM.exists() else []
    metas = [parse_meta(f) for f in files]

    bf = 0
    for m in metas:
        if backfill_baseline(m):
            bf += 1
    if args.backfill_baseline_only:
        print(f"[lesson_retire] baseline backfill only: {bf} files")
        return 0

    retire = []
    for m in metas:
        if m["pattern"] in PROTECTED:
            continue
        sc = scores.get(m["pattern"]) or {}
        v = sc.get("verdict") or "pending"
        after_n = int(sc.get("after_n") or 0)
        days_open = int(sc.get("days_open") or 0)
        if v not in ("pending", "stale-pending", "no-baseline", "undated"):
            continue
        # zombie: stuck pending with little post traffic for long enough
        # OR very low occurrence noise pattern
        if v == "stale-pending" and days_open >= args.retire_days:
            retire.append({**m, "reason": f"stale-pending days_open={days_open}"})
        elif v == "pending" and after_n <= 1 and m["occ"] <= args.max_occ and days_open >= args.retire_days:
            retire.append({**m, "reason": f"pending zombie after_n={after_n} occ={m['occ']} days={days_open}"})

    print(f"[lesson_retire] scanned={len(metas)} backfill_baseline={bf} retire_candidates={len(retire)}")
    for r in retire[:40]:
        print(f"  • {r['pattern']}: {r['reason']}")

    DIAG.mkdir(parents=True, exist_ok=True)
    (DIAG / f"lesson_retire_{today}.md").write_text(
        "# Lesson retire — " + today + "\n\n"
        + "\n".join(f"- {r['pattern']}: {r['reason']}" for r in retire) + "\n"
    )

    if not args.apply:
        print("[lesson_retire] dry-run — pass --apply to archive")
        return 0

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for r in retire:
        dest = ARCHIVE / f"{r['pattern']}_{today}.md"
        shutil.move(str(r["path"]), str(dest))
        print(f"  archived {r['pattern']} → {dest}")
    print(f"[lesson_retire] archived {len(retire)} lessons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
