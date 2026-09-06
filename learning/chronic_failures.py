"""Detect repeated blocked regressions and rotate to an untried intervention class."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_config import load_enforcement_config  # noqa: E402
from harness_paths import LEARNING, SIGNALS, STATE  # noqa: E402
from state_io import (  # noqa: E402
    append_jsonl_many_unlocked,
    atomic_write_text,
    exclusive_locks,
    load_jsonl_objects,
    try_read_json_object,
)

DIAG = LEARNING / "DIAGNOSTICS"
REVIEW_FILE = SIGNALS / "pending_human_review.jsonl"
AUDIT_FILE = SIGNALS / "review_audit.jsonl"
SNAPSHOT_FILE = SIGNALS / "chronic_failures.jsonl"
CHRONIC_MIN = 5

INTERVENTION_CLASSES = [
    "lesson",
    "ace_bullet",
    "enforcement_block",
    "skill_guardrail",
    "session_priming",
    "human_pairing",
]


def safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return 0
    if not math.isfinite(number) or number < 0:
        return 0
    return int(number)


def object_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def load_state_rows(path: Path, field: str) -> list[dict[str, Any]]:
    data, _error = try_read_json_object(path)
    return object_rows(data.get(field))


def build_rows(
    *,
    scores: dict[str, dict[str, Any]],
    overrides: Mapping[str, str],
    bullets: list[dict[str, Any]],
    edits: list[dict[str, Any]],
    audit: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    today: str,
    minimum: int,
) -> list[dict[str, Any]]:
    audit_n = Counter(
        pattern
        for row in audit
        if isinstance((pattern := row.get("pattern")), str) and pattern
    )
    pending_n = Counter(
        pattern
        for row in pending
        if row.get("status") == "pending"
        and isinstance((pattern := row.get("pattern")), str)
        and pattern
    )
    last_snapshot: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        pattern = snapshot.get("pattern")
        if isinstance(pattern, str) and pattern:
            last_snapshot[pattern] = snapshot

    rows: list[dict[str, Any]] = []
    for pattern in sorted(scores):
        score = scores[pattern]
        if score.get("verdict") != "regressed":
            continue
        blocked = overrides.get(pattern) == "block"
        audit_count = audit_n.get(pattern, 0)
        chronic = blocked and audit_count >= minimum
        pattern_edits = [row for row in edits if row.get("pattern") == pattern]
        pattern_bullets = [row for row in bullets if row.get("pattern") == pattern]
        tried: set[str] = set()
        if audit_count:
            tried.add("lesson")
        if pattern_bullets:
            tried.add("ace_bullet")
        if blocked:
            tried.add("enforcement_block")
        if pattern_edits:
            tried.add("skill_guardrail")
        if pending_n.get(pattern):
            tried.add("human_pairing")
        untried = [name for name in INTERVENTION_CLASSES if name not in tried]
        previous = last_snapshot.get(pattern, {})
        previous_hits = safe_nonnegative_int(previous.get("top_hits"))
        if chronic:
            top_hits = previous_hits if previous.get("date") == today else previous_hits + 1
        else:
            top_hits = 0
        qualities = [safe_nonnegative_int(row.get("quality")) for row in pattern_bullets]
        rows.append(
            {
                "pattern": pattern,
                "chronic": chronic,
                "blocked": blocked,
                "audit_entries": audit_count,
                "pending_entries": pending_n.get(pattern, 0),
                "skill_edits": [
                    f"/{row.get('skill')}:{row.get('status')}" for row in pattern_edits
                ],
                "ace_quality": max(qualities) if qualities else None,
                "untried": untried,
                "next_intervention": untried[0] if untried else "human_pairing (repeat)",
                "top_hits": top_hits,
                "snapshot_current": previous.get("date") == today,
            }
        )
    return rows


def render_report(rows: list[dict[str, Any]], today: str, minimum: int) -> str:
    lines = [
        f"# Chronic failures — {today}",
        "",
        f"Patterns regressed under block-mode enforcement with >={minimum} review cycles.",
        "Stop-time blocks fire AFTER the failure; rotate to the next untried",
        "intervention class instead of re-queueing another lesson.",
        "",
    ]
    chronic_rows = [row for row in rows if row["chronic"]]
    if not chronic_rows:
        lines.append("No chronic patterns.")
    for row in chronic_rows:
        lines.append(
            f"## {row['pattern']} (top_hits={row['top_hits']}, audit={row['audit_entries']})"
        )
        lines.append(f"- skill edits: {row['skill_edits'] or 'none'}")
        lines.append(f"- ACE quality: {row['ace_quality']}")
        lines.append(f"- next intervention: **{row['next_intervention']}**")
        lines.append("")
    if chronic_rows:
        lines.extend(["## Session-priming checklist (inject at prompt time)", ""])
        for row in chronic_rows:
            lines.append(
                f"- BEFORE finishing: verify no {row['pattern']} — state evidence or say unverified"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-audit", type=int, default=CHRONIC_MIN)
    args = parser.parse_args(argv)
    if args.min_audit <= 0:
        print("[chronic_failures] --min-audit must be positive")
        return 2
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    effectiveness, _error = try_read_json_object(STATE / "effectiveness_scores.json")
    raw_scores = effectiveness.get("scores")
    scores = {
        pattern: row
        for pattern, row in raw_scores.items()
        if isinstance(pattern, str) and pattern and isinstance(row, dict)
    } if isinstance(raw_scores, dict) else {}
    enforcement = load_enforcement_config(STATE / "enforcement_config.json")
    bullets = load_state_rows(STATE / "ace_playbook.json", "bullets")
    edits = load_state_rows(STATE / "skill_autofix_ledger.json", "edits")
    audit = load_jsonl_objects(AUDIT_FILE).records
    pending = load_jsonl_objects(REVIEW_FILE).records
    daily_report = DIAG / f"chronic_failures_{today}.md"
    latest_report = DIAG / "chronic_failures_latest.md"

    try:
        with exclusive_locks([SNAPSHOT_FILE, daily_report, latest_report]):
            snapshots = load_jsonl_objects(SNAPSHOT_FILE).records
            rows = build_rows(
                scores=scores,
                overrides=enforcement.config.overrides,
                bullets=bullets,
                edits=edits,
                audit=audit,
                pending=pending,
                snapshots=snapshots,
                today=today,
                minimum=args.min_audit,
            )
            report = render_report(rows, today, args.min_audit)
            atomic_write_text(daily_report, report)
            atomic_write_text(latest_report, report)
            append_jsonl_many_unlocked(
                SNAPSHOT_FILE,
                [
                    {
                        "date": today,
                        "pattern": row["pattern"],
                        "top_hits": row["top_hits"],
                        "audit_entries": row["audit_entries"],
                    }
                    for row in rows
                    if row["chronic"] and not row["snapshot_current"]
                ],
            )
    except TimeoutError as exc:
        print(f"[chronic_failures] state busy: {exc}")
        return 1

    if args.json:
        print(json.dumps({"date": today, "regressed": rows}, indent=2))
    else:
        print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
