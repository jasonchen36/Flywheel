#!/usr/bin/env python3
"""Queue high-signal meeting summaries for Graphiti ingestion.

Watches ``HARNESS_MEETING_DIR`` (default ``$HARNESS_HOME/meeting-summaries``)
for ``*.summary.md`` files and optionally flushes committed rows.

Usage:
  python3 meeting_summary_ingest.py --once --flush --limit 15
"""
from __future__ import annotations

from pathlib import Path

from harness_paths import GRAPHITI_GROUP_ID, LEARNING, MEETING_DIR, STATE
from summary_ingest import (
    SummaryIngestConfig,
    flushed_names as _flushed_names,
    high_signal as _high_signal,
    load_ledger as _load_ledger,
    now_iso as _now_iso,
    run,
)

REC = MEETING_DIR
PENDING = STATE / "graphiti_pending_episodes.jsonl"
ARCHIVE = STATE / "graphiti_flushed_archive.jsonl"
LEDGER = STATE / "meeting_graphiti_ingest_ledger.json"
MIN_BYTES = 900


def now_iso() -> str:
    """Compatibility wrapper for existing callers."""
    return _now_iso()


def load_ledger() -> dict:
    """Compatibility wrapper for existing callers."""
    return _load_ledger(LEDGER)


def flushed_names() -> set[str]:
    """Compatibility wrapper for existing callers."""
    return _flushed_names(ARCHIVE)


def high_signal(path: Path, text: str) -> bool:
    """Compatibility wrapper for existing callers."""
    return _high_signal(path, text, MIN_BYTES)


def config() -> SummaryIngestConfig:
    return SummaryIngestConfig(
        root=REC,
        glob="*.summary.md",
        suffix=".summary.md",
        name_prefix="meeting-summary-",
        transcript_label="Meeting",
        source_description="meeting_summary_ingest continuous",
        group_id=GRAPHITI_GROUP_ID,
        pending=PENDING,
        archive=ARCHIVE,
        ledger=LEDGER,
        learning_dir=LEARNING,
        min_bytes=MIN_BYTES,
    )


def main(argv: list[str] | None = None) -> int:
    return run(config(), argv)


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
