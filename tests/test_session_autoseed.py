from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import session_graphiti_autoseed as autoseed
from state_io import append_jsonl, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    state = tmp_path / "state"
    projects = tmp_path / "projects"
    diagnostics = tmp_path / "diagnostics"
    state.mkdir()
    paths = {
        "state": state,
        "projects": projects,
        "diagnostics": diagnostics,
        "pending": state / "pending.jsonl",
        "last_response": state / "last-response.txt",
    }
    monkeypatch.setattr(autoseed, "STATE", state)
    monkeypatch.setattr(autoseed, "PROJECTS", projects)
    monkeypatch.setattr(autoseed, "DIAG", diagnostics)
    monkeypatch.setattr(autoseed, "PENDING", paths["pending"])
    monkeypatch.setattr(autoseed, "LAST_RESPONSE", paths["last_response"])
    return paths


def _durable_text(label: str = "decision") -> str:
    return (
        f"The team {label} that the schema migration must deploy infrastructure before the app. "
        "The root cause was a partition contract regression, so monitoring must verify the data. "
        "Always preserve rollback behavior and never claim completion without evidence. "
    ) * 3


def _write_session(path: Path, text: str, *, write_tool: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        {"type": "tool_use", "name": "WebSearch"},
        {"type": "text", "text": text},
    ]
    if write_tool:
        blocks.append({"type": "tool_use", "name": "mcp__graphiti-memory__add_memory"})
    path.write_text(
        "not-json\n"
        + json.dumps([])
        + "\n"
        + json.dumps({"type": "assistant", "message": "bad"})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"content": [1, *blocks]}})
        + "\n"
    )


def test_find_latest_session_prefers_explicit_and_workspace_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    explicit = tmp_path / "explicit.jsonl"
    explicit.write_text("{}\n")
    assert autoseed.find_latest_session(explicit) == explicit
    assert autoseed.find_latest_session(tmp_path / "missing") is None

    preferred = paths["projects"] / "-USER-"
    other = paths["projects"] / "other"
    preferred.mkdir(parents=True)
    other.mkdir()
    preferred_session = preferred / "preferred.jsonl"
    other_session = other / "other.jsonl"
    preferred_session.write_text("x" * 2100)
    other_session.write_text("x" * 60_000)
    assert autoseed.find_latest_session() == preferred_session


def test_extract_session_signals_tolerates_bad_rows_and_detects_tools(
    tmp_path: Path
):
    session = tmp_path / "session.jsonl"
    _write_session(session, _durable_text(), write_tool=True)
    signal = autoseed.extract_session_signals(session, max_assistant_chars=300)
    assert signal["had_write"] is True
    assert signal["research_n"] >= 2
    assert signal["tools"] == ["WebSearch", "mcp__graphiti-memory__add_memory"]
    assert len(signal["text"]) == 300
    assert signal["session"] == str(session)

    missing = autoseed.extract_session_signals(tmp_path / "missing.jsonl")
    assert missing["error"]
    assert missing["text"] == ""


def test_durable_filter_and_compression_cover_signal_and_fallback_paths():
    assert autoseed.is_durable("") is False
    assert autoseed.is_durable("thanks") is False
    assert autoseed.is_durable("ordinary prose " * 40) is False
    durable = _durable_text()
    assert autoseed.is_durable(durable) is True
    compressed = autoseed.compress_episode(durable, max_chars=500)
    assert len(compressed) <= 500
    assert "schema migration" in compressed
    fallback = autoseed.compress_episode("plain paragraph " * 50, max_chars=80)
    assert fallback == ("plain paragraph " * 50)[-80:]


def test_queue_episode_is_transactionally_deduplicated_by_hash_and_legacy_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    body = _durable_text()
    digest = autoseed.content_hash(body)
    assert autoseed.queue_episode("one", body, "source", digest, dry=True) is True
    assert "would queue" in capsys.readouterr().out
    assert not paths["pending"].exists()

    assert autoseed.queue_episode("one", body, "source", digest, dry=False) is True
    assert autoseed.queue_episode("two", body, "source", digest, dry=False) is False
    records = load_jsonl_objects(paths["pending"]).records
    assert len(records) == 1
    assert records[0]["content_hash"] == digest

    paths["pending"].write_text("")
    archive = paths["state"] / "graphiti_flushed_archive.jsonl"
    append_jsonl(archive, {"episode_body": f"legacy\n<!-- content_hash:{digest} -->"})
    assert autoseed.already_queued_similar(digest) is True
    assert autoseed.already_queued_similar("0000000000000000") is False


def test_main_queues_durable_session_once_and_reports_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    session = tmp_path / "session.jsonl"
    _write_session(session, _durable_text())

    assert autoseed.main(["--session", str(session), "--max-episodes", "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert len(first["queued"]) == 1
    assert load_jsonl_objects(paths["pending"]).records[0]["origin"] == "session_graphiti_autoseed"
    assert list(paths["diagnostics"].glob("session_graphiti_autoseed_*.json"))

    assert autoseed.main(["--session", str(session), "--max-episodes", "1"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["queued"] == []
    assert second["skipped"][0].startswith("dup:")


def test_main_skips_existing_graph_write_and_handles_last_response_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    session = tmp_path / "session.jsonl"
    _write_session(session, _durable_text(), write_tool=True)
    paths["last_response"].write_text(_durable_text("decided"))

    assert autoseed.main(["--session", str(session), "--dry-run"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output[output.index("{"):])
    assert "session_already_wrote_graphiti" in report["skipped"]
    assert report["queued"] == [f"last-response-autoseed-{autoseed.today()}"]
    assert not paths["pending"].exists()


def test_main_reports_no_candidates_and_unreadable_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    assert autoseed.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skipped"] == ["no_candidates"]

    session = tmp_path / "session.jsonl"
    session.write_text("{}\n")
    monkeypatch.setattr(
        autoseed,
        "extract_session_signals",
        lambda _path: {"error": "unreadable"},
    )
    assert autoseed.main(["--session", str(session)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "session_error:unreadable" in report["skipped"]
