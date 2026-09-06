from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import meeting_summary_ingest
import scrum_graphiti_ingest
import summary_ingest
from state_io import append_jsonl, atomic_write_json, load_jsonl_objects


def _config(tmp_path: Path) -> summary_ingest.SummaryIngestConfig:
    return summary_ingest.SummaryIngestConfig(
        root=tmp_path / "summaries",
        glob="*.summary.md",
        suffix=".summary.md",
        name_prefix="summary-",
        transcript_label="Meeting",
        source_description="test ingestion",
        group_id="group",
        pending=tmp_path / "state" / "pending.jsonl",
        archive=tmp_path / "state" / "archive.jsonl",
        ledger=tmp_path / "state" / "ledger.json",
        learning_dir=tmp_path / "learning",
        min_bytes=40,
    )


def _summary_text(label: str = "decision") -> str:
    return f"- one\n- two\n- three\n- four\n{label} with enough detail to ingest\n"


def test_loaders_normalize_ledger_and_archive_rows(tmp_path: Path):
    config = _config(tmp_path)
    assert summary_ingest.load_ledger(config.ledger) == {"ingested": {}}
    atomic_write_json(config.ledger, {"ingested": "bad"})
    assert summary_ingest.load_ledger(config.ledger) == {"ingested": {}}
    atomic_write_json(
        config.ledger,
        {"ingested": {"valid": {"name": "one"}, "invalid": [], "3": {"name": "three"}}},
    )
    assert summary_ingest.load_ledger(config.ledger) == {
        "ingested": {"valid": {"name": "one"}, "3": {"name": "three"}}
    }

    append_jsonl(config.archive, {"name": "one"})
    append_jsonl(config.archive, {"name": ""})
    append_jsonl(config.archive, {"name": 4})
    config.archive.write_text(config.archive.read_text() + "bad\n[]\n")
    assert summary_ingest.flushed_names(config.archive) == {"one"}


def test_high_signal_handles_missing_small_sparse_noisy_and_valid_files(tmp_path: Path):
    missing = tmp_path / "missing"
    assert summary_ingest.high_signal(missing, _summary_text(), 1) is False
    path = tmp_path / "summary.md"
    path.write_text("tiny")
    assert summary_ingest.high_signal(path, "- one\n- two\n- three\n- four", 40) is False
    path.write_text("x" * 100)
    assert summary_ingest.high_signal(path, "- one\n- two\n", 40) is False
    assert summary_ingest.high_signal(
        path,
        "- one\n- two\n- three\n- four\n" + "None detected\n" * 6,
        40,
    ) is False
    assert summary_ingest.high_signal(path, _summary_text(), 40) is True


def test_discover_candidates_filters_archive_ledger_unreadable_and_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _config(tmp_path)
    config.root.mkdir()
    archived = config.root / "archived.summary.md"
    ingested = config.root / "ingested.summary.md"
    unreadable = config.root / "a-unreadable.summary.md"
    low_signal = config.root / "low-signal.summary.md"
    first = config.root / "new-a.summary.md"
    second = config.root / "new-b.summary.md"
    for path in (archived, ingested, unreadable, first, second):
        path.write_text(_summary_text(path.stem))
    low_signal.write_text("large but no bullets " * 20)
    append_jsonl(config.archive, {"name": "summary-archived"})
    digest = summary_ingest.hashlib.sha256(ingested.read_text().encode()).hexdigest()[:16]
    atomic_write_json(
        config.ledger,
        {"ingested": {f"summary-ingested:{digest}": {"name": "summary-ingested"}}},
    )
    original_read = Path.read_text

    def selective_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == unreadable:
            raise OSError("unreadable")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", selective_read)
    candidates, unreadable_paths = summary_ingest.discover_candidates(config, limit=1)
    assert len(candidates) == 1
    assert candidates[0][1] == "summary-new-a"
    assert candidates[0][2]["group_id"] == "group"
    assert "[RATIFIED_DECISION]" in candidates[0][2]["episode_body"]
    assert unreadable_paths == [str(unreadable)]


def test_commit_candidates_is_transactional_idempotent_and_name_unique(tmp_path: Path):
    config = _config(tmp_path)
    assert summary_ingest.commit_candidates(config, []) == []
    row = {"name": "summary-one", "content_hash": "aaa", "status": "pending"}
    other = {"name": "summary-one", "content_hash": "bbb", "status": "pending"}
    candidates = [
        ("summary-one:aaa", "summary-one", row),
        ("summary-one:bbb", "summary-one", other),
    ]
    assert summary_ingest.commit_candidates(config, candidates) == ["summary-one"]
    assert summary_ingest.commit_candidates(config, candidates) == []
    assert load_jsonl_objects(config.pending).records == [row]
    ledger = summary_ingest.load_ledger(config.ledger)["ingested"]
    assert ledger["summary-one:aaa"]["content_hash"] == "aaa"

    append_jsonl(config.archive, {"name": "summary-two"})
    late = {"name": "summary-two", "content_hash": "ccc", "status": "pending"}
    assert summary_ingest.commit_candidates(
        config,
        [("summary-two:ccc", "summary-two", late)],
    ) == []
    assert load_jsonl_objects(config.pending).records == [row]


def test_run_flush_uses_active_interpreter_and_reports_spawn_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _config(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(command, 0, "flushed\n", "")

    monkeypatch.setattr(summary_ingest.subprocess, "run", fake_run)
    result = summary_ingest.run_flush(config, 3)
    assert result.returncode == 0
    assert seen["command"] == [
        sys.executable,
        str(config.learning_dir / "flush_graphiti_pending.py"),
        "--limit",
        "50",
    ]
    assert seen["cwd"] == str(config.learning_dir)

    monkeypatch.setattr(
        summary_ingest.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing interpreter")),
    )
    result = summary_ingest.run_flush(config, 100)
    assert result.returncode == 127
    assert "missing interpreter" in result.stderr


def test_run_validates_limit_missing_root_and_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    config = _config(tmp_path)
    assert summary_ingest.run(config, ["--limit", "0"]) == 2
    assert "limit must be positive" in capsys.readouterr().err
    assert summary_ingest.run(config, []) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["candidates_queued"] == 0
    assert "missing" in missing["error"]

    config.root.mkdir()
    (config.root / "one.summary.md").write_text(_summary_text())
    assert summary_ingest.run(config, ["--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["names"] == ["summary-one"]
    assert not config.pending.exists()


def test_run_flushes_only_committed_rows_and_propagates_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    config = _config(tmp_path)
    config.root.mkdir()
    (config.root / "one.summary.md").write_text(_summary_text())
    results = [subprocess.CompletedProcess([], 0, "flushed", "")]
    monkeypatch.setattr(summary_ingest, "run_flush", lambda *_args: results.pop(0))
    assert summary_ingest.run(config, ["--flush"]) == 0
    output = capsys.readouterr().out
    assert '"candidates_queued": 1' in output
    assert output.endswith("flushed\n")

    # The same summary is already committed, so --flush does not invoke the child.
    monkeypatch.setattr(
        summary_ingest,
        "run_flush",
        lambda *_args: pytest.fail("flush must require newly committed rows"),
    )
    assert summary_ingest.run(config, ["--flush"]) == 0
    assert '"candidates_queued": 0' in capsys.readouterr().out

    second = config.root / "two.summary.md"
    second.write_text(_summary_text("second"))
    monkeypatch.setattr(
        summary_ingest,
        "run_flush",
        lambda *_args: subprocess.CompletedProcess([], 9, "partial\n", "failed"),
    )
    assert summary_ingest.run(config, ["--flush"]) == 9
    captured = capsys.readouterr()
    assert "partial" in captured.out
    assert "failed" in captured.err

    third = config.root / "three.summary.md"
    third.write_text(_summary_text("third"))
    monkeypatch.setattr(
        summary_ingest,
        "run_flush",
        lambda *_args: subprocess.CompletedProcess([], 7, "", ""),
    )
    assert summary_ingest.run(config, ["--flush"]) == 7


def test_wrappers_preserve_distinct_configs_and_compatibility_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    meeting = meeting_summary_ingest.config()
    scrum = scrum_graphiti_ingest.config()
    assert meeting.glob == "*.summary.md"
    assert meeting.group_id
    assert scrum.glob == "*_scrum.txt.summary.md"
    assert scrum.name_prefix == "scrum-summary-"

    target = tmp_path / "summary.md"
    target.write_text(_summary_text())
    monkeypatch.setattr(meeting_summary_ingest, "MIN_BYTES", 1)
    monkeypatch.setattr(scrum_graphiti_ingest, "MIN_BYTES", 1)
    assert meeting_summary_ingest.high_signal(target, target.read_text()) is True
    assert scrum_graphiti_ingest.high_signal(target, target.read_text()) is True
    assert meeting_summary_ingest.now_iso().endswith("Z")
    assert scrum_graphiti_ingest.now_iso().endswith("Z")
