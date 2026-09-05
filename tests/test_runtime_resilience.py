from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import flush_graphiti_pending
from self_improve import load_all_ratings
from state_io import load_jsonl_objects


def test_rating_loader_isolates_corrupt_and_incompatible_rows(tmp_path: Path):
    ratings = tmp_path / "ratings.jsonl"
    ratings.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-09-05T10:00:00Z",
                        "rating": "4",
                        "confidence": "0.75",
                        "session_id": "valid",
                        "tools_used": ["Bash", 3, None],
                        "skill_candidates": ["review", False],
                        "eval_results": {"proof": {"passed": False}},
                    }
                ),
                "not-json",
                "[]",
                json.dumps({"rating": "not-a-number"}),
                json.dumps({"rating": 11}),
                json.dumps({"confidence": 0.2}),
                json.dumps(
                    {
                        "rating": 8,
                        "confidence": None,
                        "tools_used": "Bash",
                        "eval_results": [],
                    }
                ),
            ]
        )
        + "\n"
    )

    entries = load_all_ratings(ratings)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.session_id == "valid"
    assert entry.rating == 4
    assert entry.confidence == 0.75
    assert entry.tools_used == ["Bash"]
    assert entry.skill_candidates == ["review"]
    assert entry.eval_results == {"proof": {"passed": False}}


def test_concurrent_meeting_ingest_commits_one_queue_row(tmp_path: Path):
    harness = tmp_path / "harness"
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    summary = meetings / "architecture.summary.md"
    summary.write_text(
        "# Architecture decision\n\n"
        + "\n".join(
            [
                "- The team ratified a schema migration sequence.",
                "- Infrastructure must deploy before the application.",
                "- Rollback keeps the previous contract available.",
                "- Monitoring verifies the new partition after deploy.",
            ]
        )
        + "\n\n"
        + ("Confirmed rationale and operational constraint. " * 30)
    )
    env = {
        **os.environ,
        "HARNESS_HOME": str(harness),
        "HARNESS_MEETING_DIR": str(meetings),
    }
    processes = [
        subprocess.Popen(
            [sys.executable, str(LEARNING / "meeting_summary_ingest.py"), "--once"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(6)
    ]
    reports: list[dict] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, f"{stdout}\n{stderr}"
        reports.append(json.loads(stdout))

    assert sum(report["candidates_queued"] for report in reports) == 1
    state = harness / "MEMORY" / "STATE"
    queued = load_jsonl_objects(state / "graphiti_pending_episodes.jsonl").records
    assert [row["name"] for row in queued] == ["meeting-summary-architecture"]
    ledger = json.loads((state / "meeting_graphiti_ingest_ledger.json").read_text())
    assert len(ledger["ingested"]) == 1


def _configure_graphiti_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr(flush_graphiti_pending, "STATE", state)
    monkeypatch.setattr(flush_graphiti_pending, "PENDING", state / "pending.jsonl")
    monkeypatch.setattr(flush_graphiti_pending, "ARCHIVE", state / "archive.jsonl")
    monkeypatch.setattr(flush_graphiti_pending, "DIAG", tmp_path / "diagnostics")


def test_graphiti_flush_preserves_failed_and_unprocessed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_graphiti_paths(tmp_path, monkeypatch)
    pending = flush_graphiti_pending.PENDING
    pending.parent.mkdir(parents=True)
    rows = [
        {"name": "a", "episode_body": "A", "status": "pending"},
        {"name": "b", "episode_body": "B", "status": "pending"},
        {"name": "c", "episode_body": "C", "status": "pending"},
    ]
    pending.write_text("".join(json.dumps(row) + "\n" for row in rows))

    class FakeClient:
        def __init__(self, url: str):
            self.url = url

        def connect(self) -> None:
            return None

        def add_memory(self, **kwargs: str) -> dict:
            if kwargs["name"] == "b":
                raise RuntimeError("temporary failure")
            return {"ok": True}

    monkeypatch.setattr(flush_graphiti_pending, "GraphitiMCPHttp", FakeClient)
    args = argparse.Namespace(dry_run=False, url="http://127.0.0.1:8000/mcp", limit=2)

    assert flush_graphiti_pending.flush_pending(args) == 1
    archived = load_jsonl_objects(flush_graphiti_pending.ARCHIVE).records
    assert [row["name"] for row in archived] == ["a"]
    assert archived[0]["status"] == "flushed"

    remaining = load_jsonl_objects(pending).records
    assert [row["name"] for row in remaining] == ["b", "c"]
    assert remaining[0]["status"] == "pending"
    assert remaining[0]["last_error"] == "temporary failure"
    diagnostic = next(flush_graphiti_pending.DIAG.glob("flush_graphiti_pending_*.json"))
    report = json.loads(diagnostic.read_text())
    assert report["flushed"] == ["a"]
    assert report["failed"] == [{"error": "temporary failure", "name": "b"}]


def test_graphiti_dry_run_and_connect_failure_leave_queue_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure_graphiti_paths(tmp_path, monkeypatch)
    pending = flush_graphiti_pending.PENDING
    pending.parent.mkdir(parents=True)
    row = {"name": "a", "episode_body": "A", "status": "pending"}
    pending.write_text(json.dumps(row) + "\n")

    dry = argparse.Namespace(dry_run=True, url="http://127.0.0.1:8000/mcp", limit=20)
    assert flush_graphiti_pending.flush_pending(dry) == 0
    assert load_jsonl_objects(pending).records == [row]

    class FailingClient:
        def __init__(self, url: str):
            self.url = url

        def connect(self) -> None:
            raise RuntimeError("offline")

    monkeypatch.setattr(flush_graphiti_pending, "GraphitiMCPHttp", FailingClient)
    live = argparse.Namespace(dry_run=False, url="http://127.0.0.1:8000/mcp", limit=20)
    assert flush_graphiti_pending.flush_pending(live) == 2
    assert load_jsonl_objects(pending).records == [row]


def test_graphiti_empty_and_deduplicated_queues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _configure_graphiti_paths(tmp_path, monkeypatch)
    args = argparse.Namespace(dry_run=False, url="http://127.0.0.1:8000/mcp", limit=20)
    assert flush_graphiti_pending.flush_pending(args) == 0

    rows = [
        {"name": "same", "episode_body": "old", "status": "pending"},
        {"name": "done", "episode_body": "ignored", "status": "flushed"},
        {"name": "same", "episode_body": "new", "status": "pending"},
        {"name": "", "episode_body": "ignored", "status": "pending"},
    ]
    assert flush_graphiti_pending.dedupe_latest(rows) == [rows[2]]
