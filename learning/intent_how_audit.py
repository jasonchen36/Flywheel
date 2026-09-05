#!/usr/bin/env python3
"""intent_how_audit.py — flag HOW scaffolding that may be bitter-lesson dead weight.

Scans skill/command markdown for procedural HOW patterns vs intent/outcome language.
Writes a report; does NOT auto-delete (humans own deprecation per editable_surfaces).

Usage:
  pyenv exec python3 intent_how_audit.py
  pyenv exec python3 intent_how_audit.py --json
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from state_io import atomic_write_json, atomic_write_text

HOME = Path.home()
ROOTS = [
    HARNESS_HOME / "commands",
    HOME / ".agents/skills",
    HOME / ".pi/agent/skills",
]
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"

# Heuristic: imperative multi-step recipes without outcome framing
HOW_MARKERS = [
    re.compile(r"(?i)^\s*\d+\.\s+(run|execute|open|type|click|always first|must always)\b"),
    re.compile(r"(?i)\bstep[- ]by[- ]step\b"),
    re.compile(r"(?i)\bexactly this command\b"),
    re.compile(r"(?i)\byou must first\b.*\bthen\b.*\bthen\b"),
]
INTENT_MARKERS = [
    re.compile(r"(?i)\buse when\b"),
    re.compile(r"(?i)\boutcome\b|\bacceptance\b|\bverify\b|\bsuccess criteria\b"),
    re.compile(r"(?i)\bmust not\b|\bnever\b|\bprohibit"),  # constraints stay
]
# Safety constraints should NOT be flagged for deletion
CONSTRAINT_MARKERS = [
    re.compile(r"(?i)\binfra-before-app\b|\bnever post without approval\b|\bbq rm\b|\bforce-push\b"),
    re.compile(r"(?i)\bblast radius\b|\bconfirm\b|\bhard gate\b"),
]


def iter_skills():
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.name in ("SKILL.md",) or p.suffix in (".md",) and "skill" in p.name.lower():
                if "gstack" in p.parts or "node_modules" in p.parts:
                    continue
                if p.is_file() and p.suffix == ".md":
                    yield p
        for p in root.glob("*.md"):
            yield p


def score_file(path: Path) -> dict | None:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return None
    if len(text) < 80:
        return None
    how = sum(1 for r in HOW_MARKERS if r.search(text))
    intent = sum(1 for r in INTENT_MARKERS if r.search(text))
    constraint = sum(1 for r in CONSTRAINT_MARKERS if r.search(text))
    lines = text.count("\n") + 1
    # Flag large files heavy on HOW, light on intent, not constraint-heavy
    flag = lines > 120 and how >= 2 and intent == 0 and constraint == 0
    flag = flag or (how >= 3 and intent <= 1 and constraint == 0 and lines > 80)
    if not flag and how < 2:
        return None
    return {
        "path": str(path),
        "lines": lines,
        "how_hits": how,
        "intent_hits": intent,
        "constraint_hits": constraint,
        "recommendation": (
            "review_for_deprecation_or_intent_rewrite"
            if flag
            else "monitor"
        ),
        "flagged": flag,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    seen = set()
    results = []
    for p in iter_skills():
        rp = str(p.resolve()) if p.exists() else str(p)
        if rp in seen:
            continue
        seen.add(rp)
        r = score_file(p)
        if r:
            results.append(r)
    flagged = [r for r in results if r["flagged"]]
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scanned_unique": len(seen),
        "candidates": len(results),
        "flagged": len(flagged),
        "items": sorted(flagged, key=lambda x: -x["how_hits"])[:40],
        "note": "Do not auto-delete. Convert HOW recipes to intent/outcome or remove if SOTA already satisfies.",
        "ref": "https://danielmiessler.com/blog/intent-engineering",
    }
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = DIAG / f"intent_how_audit_{day}.json"
    atomic_write_json(out, report)
    md = DIAG / f"intent_how_audit_{day}.md"
    lines = [
        f"# Intent vs HOW audit — {day}\n",
        f"Flagged: {len(flagged)} / scanned files with HOW signals: {len(results)}\n\n",
        "Recommendation: rewrite as intent/outcome or delete if model defaults cover it. Keep safety constraints.\n\n",
    ]
    for result in sorted(flagged, key=lambda item: -item["how_hits"])[:25]:
        lines.append(
            f"- **{result['recommendation']}** `{result['path']}` "
            f"(how={result['how_hits']} intent={result['intent_hits']} lines={result['lines']})\n"
        )
    atomic_write_text(md, "".join(lines))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"flagged={len(flagged)} report={md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
