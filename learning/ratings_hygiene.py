#!/usr/bin/env python3
"""ratings_hygiene.py — purge/tag junk ratings that starve skill attribution.

Marks (does not delete by default) entries that are system/graph noise.
With --apply: rewrites ratings.jsonl excluding junk (backup first).

Usage:
  pyenv exec python3 ratings_hygiene.py --stats
  pyenv exec python3 ratings_hygiene.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SIGNALS = Path.home() / ".claude/MEMORY/LEARNING/SIGNALS"
RATINGS = SIGNALS / "ratings.jsonl"
BACKUP_DIR = SIGNALS / "backups"

JUNK_PATTERNS = [
    re.compile(r'"extracted_entities"', re.I),
    re.compile(r'"summaries"\s*:', re.I),
    re.compile(r"PAI harness graph sync", re.I),
    re.compile(r"INFERENCE_FAILED", re.I),
    re.compile(r"Above data unverified — sourced from a system message", re.I),
]


def is_junk(row: dict) -> bool:
    if row.get("source") == "explicit":
        return False  # never drop human scores
    # Null-rating rows starve measure_effectiveness after_n (2026-07-13: 70 nulls)
    if row.get("rating") is None:
        return True
    preview = (row.get("response_preview") or "") + " " + (row.get("sentiment_summary") or "")
    if row.get("confidence") == 0 and "INFERENCE_FAILED" in preview:
        return True
    if len(preview.strip()) < 40 and row.get("source") == "implicit":
        return True
    return any(p.search(preview) for p in JUNK_PATTERNS)


def load_rows() -> list[dict]:
    if not RATINGS.exists():
        return []
    out = []
    for line in RATINGS.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--apply", action="store_true", help="rewrite ratings.jsonl without junk")
    args = ap.parse_args()
    rows = load_rows()
    junk = [r for r in rows if is_junk(r)]
    clean = [r for r in rows if not is_junk(r)]
    skill_n = sum(1 for r in clean if r.get("skill") and r.get("skill") != "general-session")
    multi_n = sum(1 for r in clean if isinstance(r.get("skill_candidates"), list)
                  and len(r.get("skill_candidates") or []) > 1)
    agent_n = sum(1 for r in clean if r.get("agent"))
    skill_rate = round(skill_n / len(clean), 3) if clean else 0.0
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "junk": len(junk),
        "clean": len(clean),
        "clean_with_skill_non_general": skill_n,
        "clean_with_skill_non_general_rate": skill_rate,
        "clean_with_multi_skill_candidates": multi_n,
        "clean_with_agent": agent_n,
        "health_warn_skill_rate_below": 0.30,
        "skill_attribution_healthy": skill_rate >= 0.30 if clean else False,
        "junk_samples": [
            (r.get("sentiment_summary") or r.get("response_preview") or "")[:80] for r in junk[:5]
        ],
    }
    print(json.dumps(report, indent=2))
    if clean and skill_rate < 0.30:
        print(
            f"[ratings_hygiene] WARNING: clean_with_skill_non_general rate "
            f"{skill_rate:.1%} < 30% — skill_autofix cannot target real skills"
        )
    if args.apply and junk:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = BACKUP_DIR / f"ratings.jsonl.{stamp}.bak"
        shutil.copy2(RATINGS, bak)
        with RATINGS.open("w") as f:
            for r in clean:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"rewrote {RATINGS} clean={len(clean)} backup={bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
