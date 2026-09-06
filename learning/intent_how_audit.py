"""Audit procedural HOW scaffolding that lacks intent or outcome framing.

The command is diagnostic only: it never deletes or edits scanned skills. Safety
constraints remain explicitly protected from deprecation recommendations.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TypedDict

from harness_paths import HARNESS_HOME
from state_io import atomic_write_json, atomic_write_text

HOME = Path.home()
ROOTS = [
    HARNESS_HOME / "commands",
    HOME / ".agents/skills",
    HOME / ".pi/agent/skills",
]
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"

HOW_MARKERS = [
    re.compile(
        r"^\s*\d+\.\s+(run|execute|open|type|click|always first|must always)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"\bstep[- ]by[- ]step\b", re.IGNORECASE),
    re.compile(r"\bexactly this command\b", re.IGNORECASE),
    re.compile(r"\byou must first\b.*\bthen\b.*\bthen\b", re.IGNORECASE),
]
INTENT_MARKERS = [
    re.compile(r"\buse when\b", re.IGNORECASE),
    re.compile(r"\boutcome\b|\bacceptance\b|\bverify\b|\bsuccess criteria\b", re.IGNORECASE),
    re.compile(r"\bmust not\b|\bnever\b|\bprohibit", re.IGNORECASE),
]
CONSTRAINT_MARKERS = [
    re.compile(r"\binfra-before-app\b|\bnever post without approval\b|\bbq rm\b|\bforce-push\b", re.IGNORECASE),
    re.compile(r"\bblast radius\b|\bconfirm\b|\bhard gate\b", re.IGNORECASE),
]


class AuditItem(TypedDict):
    path: str
    lines: int
    how_hits: int
    intent_hits: int
    constraint_hits: int
    recommendation: str
    flagged: bool


def is_candidate(path: Path) -> bool:
    return path.name == "SKILL.md" or (
        path.suffix.lower() == ".md" and "skill" in path.name.lower()
    )


def iter_skills(roots: list[Path] | None = None) -> Iterator[Path]:
    """Yield unique regular Markdown candidates without following symlinks."""
    seen: set[Path] = set()
    for root in roots if roots is not None else ROOTS:
        try:
            if not root.exists() or not root.is_dir() or root.is_symlink():
                continue
            candidates = list(root.rglob("*")) + list(root.glob("*.md"))
        except OSError:
            continue
        for path in candidates:
            if path in seen or not is_candidate(path):
                continue
            if "gstack" in path.parts or "node_modules" in path.parts:
                continue
            try:
                safe = path.is_file() and not path.is_symlink()
            except OSError:
                safe = False
            if safe:
                seen.add(path)
                yield path


def score_file(path: Path) -> AuditItem | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    if len(text) < 80:
        return None
    how = sum(1 for pattern in HOW_MARKERS if pattern.search(text))
    intent = sum(1 for pattern in INTENT_MARKERS if pattern.search(text))
    constraint = sum(1 for pattern in CONSTRAINT_MARKERS if pattern.search(text))
    lines = text.count("\n") + 1
    flagged = lines > 120 and how >= 2 and intent == 0 and constraint == 0
    flagged = flagged or (how >= 3 and intent <= 1 and constraint == 0 and lines > 80)
    if not flagged and how < 2:
        return None
    return {
        "path": str(path),
        "lines": lines,
        "how_hits": how,
        "intent_hits": intent,
        "constraint_hits": constraint,
        "recommendation": "review_for_deprecation_or_intent_rewrite" if flagged else "monitor",
        "flagged": flagged,
    }


def build_report(items: list[AuditItem], scanned: int, timestamp: datetime, limit: int) -> dict:
    flagged = [item for item in items if item["flagged"]]
    ranked = sorted(flagged, key=lambda item: (-item["how_hits"], item["path"]))
    return {
        "ts": timestamp.isoformat(),
        "scanned_unique": scanned,
        "candidates": len(items),
        "flagged": len(flagged),
        "items": ranked[:limit],
        "remaining_flagged": max(0, len(ranked) - limit),
        "note": "Do not auto-delete. Convert HOW recipes to intent/outcome or remove if SOTA already satisfies.",
        "ref": "https://danielmiessler.com/blog/intent-engineering",
    }


def render_markdown(report: dict, day: str, limit: int) -> str:
    lines = [
        f"# Intent vs HOW audit — {day}\n",
        f"Flagged: {report['flagged']} / scanned files with HOW signals: {report['candidates']}\n\n",
        "Recommendation: rewrite as intent/outcome or delete if model defaults cover it. Keep safety constraints.\n\n",
    ]
    items = report.get("items")
    if isinstance(items, list):
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- **{item.get('recommendation')}** `{item.get('path')}` "
                f"(how={item.get('how_hits')} intent={item.get('intent_hits')} lines={item.get('lines')})\n"
            )
    remaining = report.get("remaining_flagged")
    if isinstance(remaining, int) and remaining > 0:
        lines.append(f"\n... and {remaining} additional flagged files.\n")
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)
    if args.limit <= 0:
        print("[intent_how_audit] --limit must be positive")
        return 2

    scanned = 0
    results: list[AuditItem] = []
    for path in iter_skills():
        scanned += 1
        result = score_file(path)
        if result is not None:
            results.append(result)

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    report = build_report(results, scanned, now, args.limit)
    markdown = render_markdown(report, day, min(args.limit, 25))
    json_path = DIAG / f"intent_how_audit_{day}.json"
    markdown_path = DIAG / f"intent_how_audit_{day}.md"
    atomic_write_json(json_path, report)
    atomic_write_json(DIAG / "intent_how_audit_latest.json", report)
    atomic_write_text(markdown_path, markdown)
    atomic_write_text(DIAG / "intent_how_audit_latest.md", markdown)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"flagged={report['flagged']} report={markdown_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
