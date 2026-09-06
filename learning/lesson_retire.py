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
import errno
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME, LESSONS_DIR
from state_io import atomic_write_text, try_read_json_object

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


def load_scores() -> dict[str, dict]:
    data, _error = try_read_json_object(SCORES)
    scores = data.get("scores")
    if not isinstance(scores, dict):
        return {}
    return {
        str(pattern): value
        for pattern, value in scores.items()
        if isinstance(value, dict)
    }


def safe_int(value: object) -> int:
    if not isinstance(value, (str, int, float)):
        return 0
    try:
        return int(value or 0)
    except (OverflowError, ValueError):
        return 0


def archive_path(pattern: str, today: str) -> Path:
    candidate = ARCHIVE / f"{pattern}_{today}.md"
    counter = 1
    while candidate.exists():
        candidate = ARCHIVE / f"{pattern}_{today}.{counter}.md"
        counter += 1
    return candidate


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
        atomic_write_text(meta["path"], txt2)
        return True
    return False


def archive_lesson(source: Path, destination: Path) -> None:
    try:
        os.replace(source, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        atomic_write_text(destination, source.read_text(errors="replace"))
        source.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backfill-baseline-only", action="store_true")
    ap.add_argument("--retire-days", type=int, default=14)
    ap.add_argument("--max-occ", type=int, default=3,
                    help="max occurrence_count to consider a zombie")
    args = ap.parse_args(argv)
    if args.retire_days < 0 or args.max_occ < 0:
        print("[lesson_retire] retire-days and max-occ must be non-negative")
        return 2
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
        after_n = safe_int(sc.get("after_n"))
        days_open = safe_int(sc.get("days_open"))
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

    atomic_write_text(
        DIAG / f"lesson_retire_{today}.md",
        "# Lesson retire — " + today + "\n\n"
        + "\n".join(f"- {r['pattern']}: {r['reason']}" for r in retire) + "\n"
    )

    if not args.apply:
        print("[lesson_retire] dry-run — pass --apply to archive")
        return 0

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for r in retire:
        dest = archive_path(r["pattern"], today)
        archive_lesson(r["path"], dest)
        print(f"  archived {r['pattern']} → {dest}")
    print(f"[lesson_retire] archived {len(retire)} lessons")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
