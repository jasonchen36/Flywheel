#!/usr/bin/env python3
"""scrum_graphiti_ingest.py — continuous tribal knowledge: new scrum summaries → Graphiti.

Watches ~/.claude/scrum-recordings/*.txt.summary.md, queues high-signal files
not yet in graphiti_flushed_archive.jsonl, then optionally flushes.

Usage:
  pyenv exec python3 scrum_graphiti_ingest.py --once
  pyenv exec python3 scrum_graphiti_ingest.py --once --flush
  pyenv exec python3 scrum_graphiti_ingest.py --once --flush --limit 15
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import LEARNING, SCRUM_DIR, STATE

REC = SCRUM_DIR
PENDING = STATE / "graphiti_pending_episodes.jsonl"
ARCHIVE = STATE / "graphiti_flushed_archive.jsonl"
LEDGER = STATE / "scrum_graphiti_ingest_ledger.json"
MIN_BYTES = 900


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_ledger() -> dict:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text())
        except Exception:
            pass
    return {"ingested": {}}


def flushed_names() -> set[str]:
    names: set[str] = set()
    if not ARCHIVE.exists():
        return names
    for line in ARCHIVE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            names.add(json.loads(line).get("name") or "")
        except Exception:
            continue
    return names


def high_signal(path: Path, text: str) -> bool:
    if path.stat().st_size < MIN_BYTES:
        return False
    bullets = len(re.findall(r"^- ", text, re.M))
    if bullets < 4:
        return False
    if text.count("None detected") >= 6:
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="scan once and queue new")
    ap.add_argument("--flush", action="store_true", help="run flush_graphiti_pending after queue")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.once:
        args.once = True

    ledger = load_ledger()
    ingested = ledger.setdefault("ingested", {})
    already = flushed_names() | set(ingested.keys())
    queued = []
    for path in sorted(REC.glob("*_scrum.txt.summary.md")):
        text = path.read_text(errors="replace")
        if not high_signal(path, text):
            continue
        stem = path.name.replace(".txt.summary.md", "")
        name = f"scrum-summary-{stem}"
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        key = f"{name}:{h}"
        if name in already or key in ingested:
            continue
        body = (
            f"Scrum transcript extract (tribal knowledge & team context).\n"
            f"File: {path}\n"
            f"PROVENANCE & HEDGING DIRECTIVE:\n"
            f"- Preserve speaker identity, role, and authority.\n"
            f"- Tag tentative proposals, brainstorming, or unconfirmed remarks as [TENTATIVE_PROPOSAL].\n"
            f"- Only tag confirmed decisions or established team conventions as [RATIFIED_DECISION].\n"
            f"- Store underlying rationale, trade-offs, and constraints for decisions when present.\n\n"
            f"{text[:3500]}"
        )
        row = {
            "ts": now_iso(),
            "name": name,
            "episode_body": body,
            "source": "text",
            "source_description": "scrum_graphiti_ingest continuous",
            "group_id": "main",
            "status": "pending",
        }
        queued.append((key, name, row))
        if len(queued) >= args.limit:
            break

    report = {"ts": now_iso(), "candidates_queued": len(queued), "names": [n for _, n, _ in queued]}
    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0

    if queued:
        STATE.mkdir(parents=True, exist_ok=True)
        with PENDING.open("a") as f:
            for key, name, row in queued:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                ingested[key] = {"name": name, "ts": now_iso()}
        LEDGER.write_text(json.dumps(ledger, indent=2))
    print(json.dumps(report, indent=2))

    if args.flush and queued:
        cmd = [
            "pyenv",
            "exec",
            "python3",
            str(LEARNING / "flush_graphiti_pending.py"),
            "--limit",
            str(max(args.limit, 50)),
        ]
        r = subprocess.run(cmd, cwd=str(LEARNING), capture_output=True, text=True)
        print(r.stdout)
        if r.returncode not in (0,):
            print(r.stderr, file=sys.stderr)
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
