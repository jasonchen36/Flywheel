from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import sync_graph_memory as graph_sync
from state_io import atomic_write_json, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    state.mkdir()
    paths = {
        "state": state,
        "scores": state / "scores.json",
        "ace": state / "ace.json",
        "pending": state / "pending.jsonl",
        "preflight": state / "preflight.md",
        "diagnostics": diagnostics,
        "db": tmp_path / "bungraph.db",
    }
    monkeypatch.setattr(graph_sync, "STATE", state)
    monkeypatch.setattr(graph_sync, "SCORES", paths["scores"])
    monkeypatch.setattr(graph_sync, "ACE", paths["ace"])
    monkeypatch.setattr(graph_sync, "PENDING_GRAPHITI", paths["pending"])
    monkeypatch.setattr(graph_sync, "GRAPH_PREFLIGHT", paths["preflight"])
    monkeypatch.setattr(graph_sync, "DIAG", diagnostics)
    monkeypatch.setattr(graph_sync, "BUNGRAPH_DB", paths["db"])
    return paths


def test_loaders_normalize_missing_malformed_and_dynamic_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    assert graph_sync.load_scores() == {"scores": {}, "escalate": []}
    atomic_write_json(
        paths["scores"],
        {
            "scores": {"good": {"verdict": "working"}, "bad": []},
            "escalate": ["good", 1],
            "measured_at": "now",
        },
    )
    assert graph_sync.load_scores() == {
        "scores": {"good": {"verdict": "working"}},
        "escalate": ["good"],
        "measured_at": "now",
    }
    atomic_write_json(paths["scores"], {"scores": [], "escalate": "bad"})
    assert graph_sync.load_scores() == {"scores": {}, "escalate": []}

    assert graph_sync.load_ace_top() == []
    atomic_write_json(paths["ace"], {"bullets": "bad"})
    assert graph_sync.load_ace_top() == []
    atomic_write_json(paths["ace"], {"bullets": [{"pattern": "a"}, "bad", {"pattern": "b"}]})
    assert graph_sync.load_ace_top(1) == [{"pattern": "a"}]


def test_build_status_episode_filters_pending_and_includes_ace(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(graph_sync, "today", lambda: "2026-09-05")
    body = graph_sync.build_status_episode(
        {
            "scores": {
                "pending": {"verdict": "pending"},
                "broken": {
                    "verdict": "regressed",
                    "delta": -0.5,
                    "obj_verdict": "fail",
                    "after_n": 8,
                },
            }
        },
        ["broken"],
        [{"pattern": "broken", "verdict": "regressed", "description": "Verify outputs."}],
    )
    assert "broken: subj=regressed" in body
    assert "pending: subj" not in body
    assert "Top ACE bullets" in body
    assert "Verify outputs" in body


def test_queue_graphiti_episode_supports_dry_and_live_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    graph_sync.queue_graphiti_episode("dry", "body", dry=True)
    assert "dry-run" in capsys.readouterr().out
    assert not paths["pending"].exists()
    graph_sync.queue_graphiti_episode("live", "body", dry=False)
    row = load_jsonl_objects(paths["pending"]).records[0]
    assert row["name"] == "live"
    assert row["status"] == "pending"


def test_spawn_bungraph_reports_all_process_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    assert graph_sync.spawn_bungraph(["search", "x"], dry=True, wait=False) is False
    assert "dry-run" in capsys.readouterr().out

    seen: dict[str, object] = {}

    def successful_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(graph_sync.subprocess, "run", successful_run)
    assert graph_sync.spawn_bungraph(["search", "x"], dry=False, wait=True) is True
    assert seen["command"] == ["bunx", "bungraph", "search", "x"]
    assert str(graph_sync.BUNGRAPH_DB) == seen["env"]["BUNGRAPH_DB_PATH"]

    monkeypatch.setattr(
        graph_sync.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 3, "", "failed"),
    )
    assert graph_sync.spawn_bungraph(["search"], dry=False, wait=True) is False
    assert "command failed (3)" in capsys.readouterr().out

    monkeypatch.setattr(graph_sync.subprocess, "Popen", lambda *_args, **_kwargs: object())
    assert graph_sync.spawn_bungraph(["search"], dry=False, wait=False) is True

    def explode(*_args: object, **_kwargs: object) -> object:
        raise OSError("missing bunx")

    monkeypatch.setattr(graph_sync.subprocess, "Popen", explode)
    assert graph_sync.spawn_bungraph(["search"], dry=False, wait=False) is False
    assert "spawn failed" in capsys.readouterr().out


def test_main_escalates_regression_and_persists_graph_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(
        paths["scores"],
        {
            "scores": {
                "regressed": {
                    "verdict": "regressed",
                    "after_n": 8,
                    "delta": -0.2,
                    "obj_verdict": "fail",
                },
                "flat": {"verdict": "flat", "after_n": 6, "delta": 0.0},
                "pending": {"verdict": "pending", "after_n": 10},
            },
            "escalate": [],
        },
    )
    atomic_write_json(
        paths["ace"],
        {"bullets": [{"pattern": "regressed", "verdict": "regressed", "description": "Verify."}]},
    )
    calls: list[tuple[list[str], bool, bool]] = []

    def fake_spawn(args: list[str], dry: bool, wait: bool) -> bool:
        calls.append((args, dry, wait))
        return True

    monkeypatch.setattr(graph_sync, "spawn_bungraph", fake_spawn)
    assert graph_sync.main(["--wait"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["triplets_spawned"] == 2
    assert report["episodes_spawned"] == 1
    assert report["escalate"] == ["regressed"]
    assert report["priority"] == ["regressed", "flat"]
    assert all(wait is True for _args, _dry, wait in calls)
    queued = load_jsonl_objects(paths["pending"]).records
    assert [row["name"] for row in queued] == [
        f"harness-status-{graph_sync.today()}",
        f"escalated-regressed-{graph_sync.today()}",
    ]
    assert paths["preflight"].exists()
    assert list(paths["diagnostics"].glob("sync_graph_memory_*.json"))


def test_main_dry_run_reports_zero_spawned_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(graph_sync, "spawn_bungraph", lambda *_args, **_kwargs: False)
    assert graph_sync.main(["--dry-run"]) == 0
    report_text = capsys.readouterr().out
    report = json.loads(report_text[report_text.index("{"):])
    assert report["triplets_spawned"] == 0
    assert report["episodes_spawned"] == 0
    assert not paths["pending"].exists()
    assert not paths["preflight"].exists()
