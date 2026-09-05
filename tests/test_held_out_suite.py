from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import held_out_suite
from state_io import load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    fixtures = tmp_path / "fixtures"
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    history = tmp_path / "signals" / "history.jsonl"
    fixtures.mkdir()
    monkeypatch.setattr(held_out_suite, "FIXTURES", fixtures)
    monkeypatch.setattr(held_out_suite, "STATE", state)
    monkeypatch.setattr(held_out_suite, "DIAG", diagnostics)
    monkeypatch.setattr(held_out_suite, "BASELINE_FILE", state / "baseline.json")
    monkeypatch.setattr(held_out_suite, "LAST_FILE", state / "last.json")
    monkeypatch.setattr(held_out_suite, "HISTORY", history)
    return {
        "fixtures": fixtures,
        "state": state,
        "diagnostics": diagnostics,
        "history": history,
    }


def _write_splits(paths: dict[str, Path]) -> None:
    (paths["fixtures"] / "d_in.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "in-good",
                        "domain": "review",
                        "pattern": "proof",
                        "response": "good",
                        "expect": {"proof": {"applied": True, "passed": True}},
                    }
                ]
            }
        )
    )
    (paths["fixtures"] / "d_out.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "out-good",
                        "domain": "sql",
                        "pattern": "scope",
                        "response": "good",
                        "expect": {"proof": {"applied": True, "passed": True}},
                    }
                ]
            }
        )
    )


def _score(response: str) -> dict:
    passed = response == "good"
    return {"proof": {"applied": True, "passed": passed}}


def test_load_split_validates_missing_json_shape_and_case_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="cannot read d_in fixtures"):
        held_out_suite.load_split("d_in")
    split = paths["fixtures"] / "d_in.json"
    split.write_text("{")
    with pytest.raises(ValueError, match="cannot read d_in fixtures"):
        held_out_suite.load_split("d_in")
    for value in ([], {}, {"cases": "bad"}):
        split.write_text(json.dumps(value))
        with pytest.raises(ValueError, match="object with a cases list"):
            held_out_suite.load_split("d_in")
    split.write_text(json.dumps({"cases": [1]}))
    with pytest.raises(ValueError, match="cases must be JSON objects"):
        held_out_suite.load_split("d_in")
    split.write_text(json.dumps({"cases": [{"id": "ok"}]}))
    assert held_out_suite.load_split("d_in") == [{"id": "ok"}]


def test_case_ok_reports_missing_applied_passed_and_not_applied_contracts():
    case = {
        "expect": {
            "missing": {"applied": True, "passed": True},
            "applied": {"applied": True, "passed": None},
            "passed": {"applied": None, "passed": True},
            "inactive": {"applied": False, "passed": None},
        }
    }
    scored = {
        "applied": {"applied": False, "passed": None},
        "passed": {"applied": True, "passed": False},
        "inactive": {"applied": False, "passed": True},
    }
    ok, errors = held_out_suite.case_ok(case, scored)
    assert ok is False
    assert len(errors) == 4
    assert any("missing from score_text" in error for error in errors)
    assert any("applied expected" in error for error in errors)
    assert any("passed expected" in error for error in errors)
    assert any("expected not-applied" in error for error in errors)
    assert held_out_suite.case_ok({"expect": {}}, {}) == (True, [])


def test_run_split_summarize_and_compare_baseline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(held_out_suite, "score_text", _score)
    result = held_out_suite.run_split(
        [
            {"id": "good", "domain": "a", "pattern": "p", "response": "good", "expect": {"proof": {"passed": True}}},
            {"id": "bad", "domain": "a", "pattern": "p", "response": "bad", "expect": {"proof": {"passed": True}}},
        ]
    )
    assert result["n"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["pass_rate"] == 0.5
    assert result["by_domain"]["a"] == {"pass": 1, "fail": 1}
    assert result["failures"][0]["id"] == "bad"
    assert held_out_suite.run_split([])["pass_rate"] == 0.0

    summary = held_out_suite.summarize(result, result)
    assert summary["accept"] is False
    baseline = {"ts": "before", "d_in": {"pass_rate": 0.75}, "d_out": {"pass_rate": 0.5}}
    gate = held_out_suite.compare_baseline(summary, baseline)
    assert gate["d_in_delta"] == -0.25
    assert gate["d_out_delta"] == 0.0
    assert gate["d_in_regressed"] is True
    assert gate["gate_pass"] is False


def test_write_report_includes_failures_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    split = {
        "n": 1,
        "passed": 0,
        "failed": 1,
        "pass_rate": 0.0,
        "by_domain": {},
        "by_pattern": {},
        "failures": [{"id": "broken", "errors": ["mismatch"]}],
    }
    summary = {"ts": "now", "eval_count": 1, "d_in": split, "d_out": split, "accept": False}
    gate = {
        "baseline_ts": "before",
        "d_in_delta": -1.0,
        "d_out_delta": -1.0,
        "d_out_regressed": True,
        "d_in_regressed": True,
        "gate_pass": False,
    }
    report = held_out_suite.write_report(summary, gate)
    text = report.read_text()
    assert text.count("`broken`: mismatch") == 2
    assert "gate_pass: False" in text


def test_main_writes_state_history_report_and_initial_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _write_splits(paths)
    monkeypatch.setattr(held_out_suite, "score_text", _score)

    assert held_out_suite.main(["--json"]) == 0
    output = capsys.readouterr().out
    assert '"accept": true' in output
    assert held_out_suite.LAST_FILE.exists()
    assert held_out_suite.BASELINE_FILE.exists()
    assert load_jsonl_objects(held_out_suite.HISTORY).records[0]["accept"] is True
    assert list(paths["diagnostics"].glob("held_out_suite_*.md"))


def test_main_dry_run_and_invalid_fixture_do_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _write_splits(paths)
    monkeypatch.setattr(held_out_suite, "score_text", _score)
    assert held_out_suite.main(["--dry-run"]) == 0
    assert not paths["state"].exists()
    assert not paths["history"].exists()

    (paths["fixtures"] / "d_in.json").write_text("{")
    assert held_out_suite.main([]) == 2
    assert "invalid fixtures" in capsys.readouterr().err


def test_main_gate_fails_closed_for_corrupt_baseline_and_failed_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _write_splits(paths)
    monkeypatch.setattr(held_out_suite, "score_text", _score)
    paths["state"].mkdir()
    held_out_suite.BASELINE_FILE.write_text("{")
    assert held_out_suite.main(["--gate", "--dry-run"]) == 1

    held_out_suite.BASELINE_FILE.unlink()
    monkeypatch.setattr(
        held_out_suite,
        "score_text",
        lambda _response: {"proof": {"applied": True, "passed": False}},
    )
    assert held_out_suite.main(["--gate", "--dry-run"]) == 1
    assert "FAIL D_in" in capsys.readouterr().out


def test_main_updates_baseline_explicitly_even_for_failed_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    _write_splits(paths)
    monkeypatch.setattr(
        held_out_suite,
        "score_text",
        lambda _response: {"proof": {"applied": True, "passed": False}},
    )
    assert held_out_suite.main(["--update-baseline"]) == 1
    assert json.loads(held_out_suite.BASELINE_FILE.read_text())["accept"] is False
