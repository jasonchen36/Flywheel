from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import skill_burnin


def test_valid_edits_and_safe_rates_normalize_malformed_state():
    assert skill_burnin.valid_edits({}) == []
    assert skill_burnin.valid_edits({"edits": "bad"}) == []
    valid = {"skill": "deploy", "pattern": "missing_proof"}
    assert skill_burnin.valid_edits({"edits": [None, {}, {"skill": "", "pattern": "x"}, valid]}) == [valid]
    assert skill_burnin.safe_rate(None) == 0.0
    assert skill_burnin.safe_rate({}) == 0.0
    assert skill_burnin.safe_rate("bad") == 0.0
    assert skill_burnin.safe_rate("0.75") == 0.75


def test_resolve_stall_handles_invalid_dates_and_statuses(
    monkeypatch: pytest.MonkeyPatch,
):
    ledger = {
        "edits": [
            {"skill": "invalid-date", "pattern": "one", "status": "active", "applied": "bad"},
            {"skill": "inactive", "pattern": "two", "status": "confirmed"},
        ]
    }
    monkeypatch.setattr(skill_burnin, "skill_sessions", lambda *_args, **_kwargs: [])
    assert skill_burnin.resolve_stall(ledger, [], "bad", True) == []
    assert skill_burnin.resolve_stall(ledger, [], "2026-09-05", True) == []


def test_resolve_stall_and_reactivate_are_dry_run_or_persisted(
    monkeypatch: pytest.MonkeyPatch,
):
    ledger = {
        "edits": [
            {"skill": "stale", "pattern": "missing_proof", "status": "active", "applied": "2026-01-01"},
            {"skill": "fresh", "pattern": "new_signal", "status": "active", "applied": "2026-09-01"},
            {"skill": "returning", "pattern": "old_signal", "status": "stalled", "applied": "2026-01-01", "stalled_reason": "quiet"},
        ]
    }
    sessions = {
        ("stale", True): [],
        ("stale", False): [SimpleNamespace(timestamp="2025-12-01")],
        ("fresh", True): [],
        ("returning", True): [SimpleNamespace(timestamp="2026-09-04")],
    }

    def fake_sessions(_entries: list, skill: str, since: object = None):
        return sessions.get((skill, since is not None), [])

    saved: list[dict] = []
    monkeypatch.setattr(skill_burnin, "skill_sessions", fake_sessions)
    monkeypatch.setattr(skill_burnin, "save_ledger", lambda value: saved.append(value))
    changes = skill_burnin.resolve_stall(ledger, [], "2026-09-05", False)
    assert len(changes) == 2
    assert ledger["edits"][0]["status"] == "active"
    assert saved == []

    changes = skill_burnin.resolve_stall(ledger, [], "2026-09-05", True)
    assert any("STALL /stale" in change for change in changes)
    assert any("REACTIVATE /returning" in change for change in changes)
    assert ledger["edits"][0]["status"] == "stalled"
    assert ledger["edits"][2]["status"] == "active"
    assert "stalled_reason" not in ledger["edits"][2]
    assert saved == [ledger]


def _patch_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger: dict,
    session_counts: dict[str, tuple[int, int]],
):
    monkeypatch.setattr(skill_burnin, "load_ledger", lambda: ledger)
    monkeypatch.setattr(skill_burnin, "load_all_ratings", lambda _path: [object()])
    monkeypatch.setattr(skill_burnin, "MIN_AFTER", 3)
    monkeypatch.setattr(skill_burnin, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(skill_burnin, "DIAG_DIR", tmp_path / "diagnostics")

    def fake_sessions(_entries: list, skill: str, since: object = None):
        post_n, all_n = session_counts.get(skill, (0, 0))
        count = post_n if since is not None else all_n
        return [SimpleNamespace(skill=skill, index=i) for i in range(count)]

    def fake_fail_rate(entries: list):
        if not entries:
            return 0.0, 0
        rates = {"working": 0.2, "holding": 0.7, "short": 0.1}
        return rates[entries[0].skill], 0

    monkeypatch.setattr(skill_burnin, "skill_sessions", fake_sessions)
    monkeypatch.setattr(skill_burnin, "fail_rate", fake_fail_rate)
    monkeypatch.setattr(
        skill_burnin,
        "verdict_for",
        lambda _base, rate, _n, _minimum: "working" if rate < 0.5 else "regressed",
    )


def test_main_status_json_and_human_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ledger = {
        "edits": [
            {"skill": "working", "pattern": "proof", "status": "active", "applied": "2026-01-01", "baseline_fail_rate": "0.8"},
            {"skill": "bad", "pattern": "ignored", "status": "confirmed"},
        ]
    }
    _patch_main(tmp_path, monkeypatch, ledger, {"working": (1, 4)})
    assert skill_burnin.main(["--status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["active"][0]["post_n"] == 1
    assert report["active"][0]["baseline_fail_rate"] == 0.8

    assert skill_burnin.main(["--status"]) == 0
    assert "/working pattern=proof" in capsys.readouterr().out


def test_main_resolve_stall_reports_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ledger = {"edits": []}
    _patch_main(tmp_path, monkeypatch, ledger, {})
    monkeypatch.setattr(skill_burnin, "resolve_stall", lambda *_args: ["STALL /one"])
    assert skill_burnin.main(["--resolve-stall"]) == 0
    output = capsys.readouterr().out
    assert "STALL /one" in output
    assert "re-run with --apply" in output


def test_main_provisional_dry_run_does_not_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ledger = {
        "edits": [
            {"skill": "working", "pattern": "proof", "status": "active", "applied": "2026-01-01", "baseline_fail_rate": 0.8}
        ]
    }
    _patch_main(tmp_path, monkeypatch, ledger, {"working": (1, 4)})
    monkeypatch.setattr(skill_burnin, "save_ledger", lambda _value: pytest.fail("dry run must not save"))
    assert skill_burnin.main(["--provisional-measure"]) == 0
    output = capsys.readouterr().out
    assert "re-run with --apply" in output
    assert "would evaluate /working" in output


def test_main_provisional_apply_confirms_holds_skips_and_writes_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ledger = {
        "edits": [
            {"skill": "working", "pattern": "proof", "status": "active", "applied": "2026-01-01", "baseline_fail_rate": 0.8},
            {"skill": "holding", "pattern": "retry", "status": "active", "applied": "2026-01-01", "baseline_fail_rate": "bad"},
            {"skill": "short", "pattern": "small", "status": "active", "applied": "2026-01-01", "baseline_fail_rate": 0.9},
        ]
    }
    _patch_main(tmp_path, monkeypatch, ledger, {"working": (1, 5), "holding": (0, 4), "short": (0, 2)})
    saved: list[dict] = []
    monkeypatch.setattr(skill_burnin, "save_ledger", lambda value: saved.append(value))
    assert skill_burnin.main(["--provisional-measure", "--apply"]) == 0
    output = capsys.readouterr().out
    assert "PROVISIONAL-CONFIRM /working" in output
    assert "HOLD /holding" in output
    assert "skip /short" in output
    assert ledger["edits"][0]["status"] == "confirmed"
    assert ledger["edits"][1]["status"] == "active"
    assert saved == [ledger]
    report = next((tmp_path / "diagnostics").glob("skill_burnin_*.md")).read_text()
    assert "PROVISIONAL-CONFIRM" in report and "HOLD" in report
