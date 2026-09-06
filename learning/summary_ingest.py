"""Shared, transactional ingestion for meeting and scrum summary files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from state_io import (
    append_jsonl_unlocked,
    atomic_write_json,
    exclusive_locks,
    load_jsonl_objects,
    try_read_json_object,
)

DEFAULT_MIN_BYTES = 900


@dataclass(frozen=True)
class SummaryIngestConfig:
    root: Path
    glob: str
    suffix: str
    name_prefix: str
    transcript_label: str
    source_description: str
    group_id: str
    pending: Path
    archive: Path
    ledger: Path
    learning_dir: Path
    min_bytes: int = DEFAULT_MIN_BYTES


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_ledger(path: Path) -> dict[str, Any]:
    data, _error = try_read_json_object(path)
    ingested = data.get("ingested")
    return {
        "ingested": {
            str(key): value
            for key, value in ingested.items()
            if isinstance(value, dict)
        } if isinstance(ingested, dict) else {}
    }


def flushed_names(path: Path) -> set[str]:
    return {
        str(record["name"])
        for record in load_jsonl_objects(path).records
        if isinstance(record.get("name"), str) and record["name"]
    }


def high_signal(path: Path, text: str, min_bytes: int = DEFAULT_MIN_BYTES) -> bool:
    try:
        if path.stat().st_size < min_bytes:
            return False
    except OSError:
        return False
    if len(re.findall(r"^- ", text, re.M)) < 4:
        return False
    return text.count("None detected") < 6


def _episode_body(config: SummaryIngestConfig, path: Path, text: str) -> str:
    return (
        f"{config.transcript_label} transcript extract (tribal knowledge & team context).\n"
        f"File: {path}\n"
        "PROVENANCE & HEDGING DIRECTIVE:\n"
        "- Preserve speaker identity, role, and authority.\n"
        "- Tag tentative proposals, brainstorming, or unconfirmed remarks as "
        "[TENTATIVE_PROPOSAL].\n"
        "- Only tag confirmed decisions or established team conventions as "
        "[RATIFIED_DECISION].\n"
        "- Store underlying rationale, trade-offs, and constraints for decisions when "
        "present.\n\n"
        f"{text[:3500]}"
    )


def discover_candidates(
    config: SummaryIngestConfig,
    *,
    limit: int,
) -> tuple[list[tuple[str, str, dict[str, Any]]], list[str]]:
    ledger = load_ledger(config.ledger)
    ingested = ledger["ingested"]
    already_names = flushed_names(config.archive) | {
        str(value.get("name"))
        for value in ingested.values()
        if isinstance(value.get("name"), str)
    }
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    unreadable: list[str] = []
    for path in sorted(config.root.glob(config.glob)):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            unreadable.append(str(path))
            continue
        if not high_signal(path, text, config.min_bytes):
            continue
        stem = path.name.removesuffix(config.suffix)
        name = f"{config.name_prefix}{stem}"
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        key = f"{name}:{digest}"
        if name in already_names or key in ingested:
            continue
        row = {
            "ts": now_iso(),
            "name": name,
            "episode_body": _episode_body(config, path, text),
            "source": "text",
            "source_description": config.source_description,
            "group_id": config.group_id,
            "status": "pending",
            "content_hash": digest,
        }
        candidates.append((key, name, row))
        if len(candidates) >= limit:
            break
    return candidates, unreadable


def commit_candidates(
    config: SummaryIngestConfig,
    candidates: list[tuple[str, str, dict[str, Any]]],
) -> list[str]:
    committed: list[str] = []
    if not candidates:
        return committed
    with exclusive_locks((config.pending, config.ledger)):
        current = load_ledger(config.ledger)
        ingested = current["ingested"]
        committed_names = flushed_names(config.archive) | {
            str(value.get("name"))
            for value in ingested.values()
            if isinstance(value.get("name"), str)
        }
        for key, name, row in candidates:
            if key in ingested or name in committed_names:
                continue
            append_jsonl_unlocked(config.pending, row)
            ingested[key] = {
                "name": name,
                "ts": now_iso(),
                "content_hash": row["content_hash"],
            }
            committed_names.add(name)
            committed.append(name)
        atomic_write_json(config.ledger, current)
    return committed


def run_flush(config: SummaryIngestConfig, limit: int) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(config.learning_dir / "flush_graphiti_pending.py"),
        "--limit",
        str(max(limit, 50)),
    ]
    try:
        return subprocess.run(
            command,
            cwd=str(config.learning_dir),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def run(config: SummaryIngestConfig, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="scan once and queue new")
    parser.add_argument("--flush", action="store_true", help="flush committed summaries")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        print("[summary_ingest] limit must be positive", file=sys.stderr)
        return 2
    if not config.root.exists():
        print(json.dumps({
            "ts": now_iso(),
            "error": f"missing {config.root}",
            "candidates_queued": 0,
            "names": [],
            "unreadable": [],
        }))
        return 0

    candidates, unreadable = discover_candidates(config, limit=args.limit)
    report: dict[str, Any] = {
        "ts": now_iso(),
        "candidates_queued": len(candidates),
        "names": [name for _key, name, _row in candidates],
        "unreadable": unreadable,
    }
    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0

    committed = commit_candidates(config, candidates)
    report["candidates_queued"] = len(committed)
    report["names"] = committed
    print(json.dumps(report, indent=2))

    if args.flush and committed:
        result = run_flush(config, args.limit)
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.returncode
    return 0
