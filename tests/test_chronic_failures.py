from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import chronic_failures
from state_io import append_jsonl, atomic_write_json, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    state = tmp_path / "state"
    signals = tmp_path / "signals"
    diagnostics = tmp_path / "diagnostics"
    paths = {
        "state": state,
        "signals": signals,
        "diagnostics": diagnostics,
        "review": signals / "pending.jsonl",
        "audit": signals / "audit.jsonl",
        "snapshot": signals / "snapshots.jsonl",
    }
    monkeypatch.setattr(chronic_failures, "STATE", state)
    monkeypatch.setattr(chronic_failures, "SIGNALS", signals)
    monkeypatch.setattr(chronic_failures, "DIAG", diagnostics)
    monkeypatch.setattr(chronic_failures, "REVIEW_FILE", paths["review"])
    monkeypatch.setattr(chronic_failures, "AUDIT_FILE", paths["audit"])
    monkeypatch.setattr(chronic_failures, "SNAPSHOT_FILE", paths["snapshot"])
    return paths


def test_chronic_normalizers_and_state_rows(tmp_path: Path) -> None:
    for value in (True, None, object(), "bad", float("inf"), -1):
        assert chronic_failures.safe_nonnegative_int(value) == 0
    assert chronic_failures.safe_nonnegative_int("4.8") == 4
    assert chronic_failures.object_rows("bad") == []
    assert chronic_failures.object_rows([{"a": 1}, "bad", []]) == [{"a": 1}]
    path = tmp_path / "state.json"
    assert chronic_failures.load_state_rows(path, "rows") == []
    atomic_write_json(path, {"rows": [{"a": 1}, "bad"]})
    assert chronic_failures.load_state_rows(path, "rows") == [{"a": 1}]


def test_build_rows_rotates_interventions_and_tracks_daily_hits() -> None:
    scores = {
        "ignored": {"verdict": "working"},
        "chronic": {"verdict": "regressed"},
        "fresh": {"verdict": "regressed"},
        "unblocked": {"verdict": "regressed"},
        "no_audit": {"verdict": "regressed"},
    }
    audit = [{"pattern": "chronic"}] * 5 + [{"pattern": "fresh"}] * 5 + [
        {"pattern": "unblocked"},
        {"pattern": 3},
    ]
    rows = chronic_failures.build_rows(
        scores=scores,
        overrides={"chronic": "block", "fresh": "block"},
        bullets=[
            {"pattern": "chronic", "quality": "4"},
            {"pattern": "chronic", "quality": "bad"},
        ],
        edits=[{"pattern": "chronic", "skill": "review", "status": "active"}],
        audit=audit,
        pending=[{"pattern": "chronic", "status": "pending"}, {"pattern": "fresh", "status": "done"}],
        snapshots=[
            {"pattern": "chronic", "date": "2026-01-02", "top_hits": 9},
            {"pattern": "fresh", "date": "2026-01-01", "top_hits": "bad"},
            {"pattern": 4},
        ],
        today="2026-01-02",
        minimum=5,
    )
    by_pattern = {row["pattern"]: row for row in rows}
    chronic = by_pattern["chronic"]
    assert chronic["chronic"] is True
    assert chronic["top_hits"] == 9 and chronic["snapshot_current"] is True
    assert chronic["ace_quality"] == 4
    assert chronic["skill_edits"] == ["/review:active"]
    assert chronic["untried"] == ["session_priming"]
    assert chronic["next_intervention"] == "session_priming"
    assert by_pattern["fresh"]["top_hits"] == 1
    assert by_pattern["fresh"]["next_intervention"] == "ace_bullet"
    assert by_pattern["unblocked"]["chronic"] is False
    assert by_pattern["unblocked"]["top_hits"] == 0
    assert by_pattern["no_audit"]["untried"][0] == "lesson"
    assert "ignored" not in by_pattern

    exhausted = chronic_failures.build_rows(
        scores={"all": {"verdict": "regressed"}},
        overrides={"all": "block"},
        bullets=[{"pattern": "all", "quality": 2}],
        edits=[{"pattern": "all", "skill": "x", "status": "active"}],
        audit=[{"pattern": "all"}] * 5,
        pending=[{"pattern": "all", "status": "pending"}],
        snapshots=[],
        today="2026-01-02",
        minimum=5,
    )[0]
    assert exhausted["untried"] == ["session_priming"]
    # Marking priming as an explicit future class can exhaust the list in callers.
    all_tried = set(chronic_failures.INTERVENTION_CLASSES)
    untried = [name for name in chronic_failures.INTERVENTION_CLASSES if name not in all_tried]
    exhausted["untried"] = untried
    exhausted["next_intervention"] = untried[0] if untried else "human_pairing (repeat)"
    assert exhausted["next_intervention"] == "human_pairing (repeat)"


def test_render_report_handles_empty_and_chronic_rows() -> None:
    empty = chronic_failures.render_report([], "2026-01-01", 5)
    assert "No chronic patterns" in empty
    row = {
        "pattern": "proof",
        "chronic": True,
        "top_hits": 2,
        "audit_entries": 5,
        "skill_edits": [],
        "ace_quality": None,
        "next_intervention": "session_priming",
    }
    report = chronic_failures.render_report([row], "2026-01-01", 5)
    assert "top_hits=2" in report
    assert "skill edits: none" in report
    assert "Session-priming checklist" in report
    assert "verify no proof" in report


def test_main_is_idempotent_and_outputs_json_or_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    assert chronic_failures.main(["--min-audit", "0"]) == 2
    capsys.readouterr()
    atomic_write_json(
        paths["state"] / "effectiveness_scores.json",
        {"scores": {"unverified_completion": {"verdict": "regressed"}, "bad": [], "": {}}},
    )
    atomic_write_json(
        paths["state"] / "enforcement_config.json",
        {"enabled": True, "overrides": {"unverified_completion": "block"}},
    )
    atomic_write_json(
        paths["state"] / "ace_playbook.json",
        {"bullets": [{"pattern": "unverified_completion", "quality": 3}, "bad"]},
    )
    atomic_write_json(
        paths["state"] / "skill_autofix_ledger.json",
        {"edits": [{"pattern": "unverified_completion", "skill": "review", "status": "active"}]},
    )
    for _ in range(5):
        append_jsonl(paths["audit"], {"pattern": "unverified_completion"})
    append_jsonl(paths["review"], {"pattern": "unverified_completion", "status": "pending"})

    assert chronic_failures.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["regressed"][0]["chronic"] is True
    assert len(load_jsonl_objects(paths["snapshot"]).records) == 1
    assert (paths["diagnostics"] / "chronic_failures_latest.md").exists()

    assert chronic_failures.main([]) == 0
    output = capsys.readouterr().out
    assert "Session-priming checklist" in output
    assert len(load_jsonl_objects(paths["snapshot"]).records) == 1


def test_main_tolerates_malformed_state_and_reports_lock_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(paths["state"] / "effectiveness_scores.json", {"scores": []})
    atomic_write_json(paths["state"] / "ace_playbook.json", {"bullets": "bad"})
    atomic_write_json(paths["state"] / "skill_autofix_ledger.json", {"edits": "bad"})
    assert chronic_failures.main([]) == 0
    assert "No chronic patterns" in capsys.readouterr().out

    class Busy:
        def __enter__(self):
            raise TimeoutError("busy")

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(chronic_failures, "exclusive_locks", lambda _paths: Busy())
    assert chronic_failures.main([]) == 1
    assert "state busy" in capsys.readouterr().out
