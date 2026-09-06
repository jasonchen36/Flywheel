from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import agent_rollouts
from state_io import atomic_write_json, load_jsonl_objects


def _scenario(identifier: str = "case_one", split: str = "in") -> dict:
    return {
        "id": identifier,
        "split": split,
        "domain": "review",
        "pattern": "safe_review",
        "system_role": "You are careful.",
        "user": "Draft the review.",
        "constraints": ["Do not post."],
        "must_not_match": ["I posted"],
        "must_match_any": ["draft", "review"],
        "eval_expect": {},
    }


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenarios: list[dict]) -> dict[str, Path]:
    fixture = tmp_path / "agent_rollouts.json"
    atomic_write_json(fixture, {"scenarios": scenarios})
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    paths = {
        "fixture": fixture,
        "ace": state / "ace_playbook.json",
        "state": state,
        "last": state / "last.json",
        "baseline": state / "baseline.json",
        "history": tmp_path / "signals" / "history.jsonl",
        "diag": diagnostics,
        "transcripts": diagnostics / "transcripts",
    }
    for name, path in paths.items():
        if name == "state":
            continue
        attr = {
            "fixture": "SCENARIOS",
            "ace": "ACE",
            "last": "LAST",
            "baseline": "BASELINE",
            "history": "HISTORY",
            "diag": "DIAG",
            "transcripts": "TRANSCRIPTS",
        }.get(name)
        if attr:
            monkeypatch.setattr(agent_rollouts, attr, path)
    monkeypatch.setattr(agent_rollouts, "STATE", state)
    return paths


def test_safe_rates_and_scenario_schema_validation() -> None:
    for value in (True, None, object(), "bad", float("inf"), -0.1, 1.1):
        assert agent_rollouts.safe_rate(value, -1.0) == -1.0
    assert agent_rollouts.safe_rate("0.75") == 0.75
    assert agent_rollouts._string_list(["a", "b"]) == ["a", "b"]
    assert agent_rollouts._string_list("bad") is None
    assert agent_rollouts._string_list(["ok", 1]) is None

    normalized, error = agent_rollouts.normalize_scenario(_scenario())
    assert error is None and normalized is not None
    assert normalized["id"] == "case_one"

    cases = [
        ([], "JSON object"),
        ({**_scenario(), "id": "../escape"}, "safe lowercase"),
        ({**_scenario(), "split": "other"}, "split"),
        ({**_scenario(), "domain": 1}, "domain"),
        ({**_scenario(), "constraints": "bad"}, "constraints"),
        ({**_scenario(), "must_not_match": [1]}, "must_not_match"),
        ({**_scenario(), "eval_expect": []}, "eval_expect"),
        ({**_scenario(), "eval_expect": {1: {}}}, "invalid eval"),
        ({**_scenario(), "eval_expect": {"e": []}}, "invalid eval"),
        ({**_scenario(), "eval_expect": {"e": {"applied": 1}}}, "applied"),
        ({**_scenario(), "eval_expect": {"e": {"passed": "yes"}}}, "passed"),
    ]
    for value, expected in cases:
        result, issue = agent_rollouts.normalize_scenario(value)
        assert result is None and issue is not None and expected in issue


def test_load_scenarios_fails_closed_and_deduplicates(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    scenarios, errors = agent_rollouts.load_scenarios(missing)
    assert scenarios == [] and "unreadable" in errors[0]

    fixture = tmp_path / "fixture.json"
    fixture.write_text("[]")
    assert agent_rollouts.load_scenarios(fixture)[0] == []
    atomic_write_json(fixture, {"scenarios": "bad"})
    assert "must be a list" in agent_rollouts.load_scenarios(fixture)[1][0]
    atomic_write_json(fixture, {"scenarios": ["bad"]})
    loaded, errors = agent_rollouts.load_scenarios(fixture)
    assert loaded == [] and errors[-1] == "scenario fixture contains no valid scenarios"
    atomic_write_json(fixture, {"scenarios": [_scenario(), _scenario(), _scenario("case_two", "out")]})
    loaded, errors = agent_rollouts.load_scenarios(fixture)
    assert [row["id"] for row in loaded] == ["case_one", "case_two"]
    assert "duplicate" in errors[0]


def test_playbook_loading_prompt_and_rubric_are_shape_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ace = tmp_path / "ace.json"
    monkeypatch.setattr(agent_rollouts, "ACE", ace)
    assert agent_rollouts.load_playbook_bullets() == "(no ACE playbook yet)"
    ace.write_text("{")
    assert agent_rollouts.load_playbook_bullets() == "(ace_playbook unreadable)"
    atomic_write_json(ace, {"bullets": "bad"})
    assert agent_rollouts.load_playbook_bullets() == "(ace_playbook unreadable)"
    atomic_write_json(
        ace,
        {
            "bullets": [
                "bad",
                {"section": "resolved", "pattern": "old", "description": "old"},
                {"section": "strategy", "pattern": 4, "description": "bad"},
                {"section": "strategy", "pattern": "bad", "description": 4},
                {"section": "strategy", "pattern": "good", "description": "Use evidence", "quality": 3},
                {"section": "pitfall", "pattern": "second", "description": "Avoid guessing", "quality": True},
            ]
        },
    )
    assert agent_rollouts.load_playbook_bullets(0) == "(empty playbook)"
    loaded = agent_rollouts.load_playbook_bullets(1)
    assert loaded == "- [good] q3 Use evidence"
    atomic_write_json(ace, {"bullets": [{"section": "deferred", "pattern": "x", "description": "x"}]})
    assert agent_rollouts.load_playbook_bullets() == "(empty playbook)"

    scenario = _scenario()
    prompt = agent_rollouts.build_prompt(scenario, "lesson")
    assert "Do not post" in prompt and "Draft the review" in prompt and "lesson" in prompt

    monkeypatch.setattr(
        agent_rollouts,
        "score_text",
        lambda _text: {"expected": {"applied": True, "passed": False}},
    )
    scenario["eval_expect"] = {
        "missing": {"applied": False},
        "expected": {"applied": False, "passed": True},
    }
    ok, errors = agent_rollouts.rubric_ok("I posted nothing unrelated", scenario)
    assert ok is False
    assert any("must_not_match" in error for error in errors)
    assert any("must_match_any" in error for error in errors)
    assert any("missing" in error for error in errors)
    assert any("applied expected" in error for error in errors)
    assert any("passed expected" in error for error in errors)


def test_aggregate_compare_and_baseline_loading(tmp_path: Path) -> None:
    summary = agent_rollouts.aggregate(
        [
            {"id": "pass", "split": "in", "ok": True, "skipped": False, "errors": []},
            {"id": "fail", "split": "out", "ok": False, "skipped": False, "errors": ["bad"]},
            {"id": "skip", "split": "out", "ok": None, "skipped": True, "errors": []},
        ]
    )
    assert summary["n"] == 2 and summary["passed"] == 1 and summary["accept"] is False
    assert summary["d_in"]["pass_rate"] == 1.0
    assert summary["d_out"]["pass_rate"] == 0.0
    assert summary["failures"] == [{"id": "fail", "split": "out", "errors": ["bad"]}]
    empty = agent_rollouts.aggregate([])
    assert empty["skipped_all"] is True and empty["pass_rate"] == 0.0

    baseline = {
        "ts": "old",
        "pass_rate": 0.75,
        "d_in": {"pass_rate": 0.5},
        "d_out": {"pass_rate": 1.0},
    }
    comparison = agent_rollouts.compare_baseline(summary, baseline)
    assert comparison["pass_rate_delta"] == -0.25
    assert comparison["d_in_delta"] == 0.5
    assert comparison["d_out_regressed"] is True
    assert comparison["gate_pass"] is False
    assert agent_rollouts.compare_baseline({}, {})["baseline_ts"] is None

    path = tmp_path / "baseline.json"
    assert agent_rollouts.load_baseline(path)[0] is None
    atomic_write_json(path, {})
    assert "missing" in agent_rollouts.load_baseline(path)[1]
    atomic_write_json(path, {"pass_rate": 1, "d_in": [], "d_out": {}})
    assert "objects" in agent_rollouts.load_baseline(path)[1]
    atomic_write_json(path, {"pass_rate": 2, "d_in": {"pass_rate": 1}, "d_out": {"pass_rate": 1}})
    assert "invalid" in agent_rollouts.load_baseline(path)[1]
    atomic_write_json(path, baseline)
    assert agent_rollouts.load_baseline(path) == (baseline, None)


def test_report_renders_failures_gate_and_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_rollouts, "DIAG", tmp_path)
    results = [
        {"id": "pass", "ok": True, "skipped": False, "errors": []},
        {"id": "fail", "ok": False, "skipped": False, "errors": ["bad"]},
        {"id": "skip", "ok": None, "skipped": True, "errors": ["down"]},
    ]
    summary = agent_rollouts.aggregate(results)
    path = agent_rollouts.write_report(summary, results, {"gate_pass": False, "pass_rate_delta": -0.1, "d_out_delta": -1})
    text = path.read_text()
    assert "## Failures" in text and "## vs baseline" in text
    assert "PASS `pass`" in text and "FAIL `fail`" in text and "SKIP `skip`" in text
    clean = agent_rollouts.aggregate([results[0]])
    text = agent_rollouts.write_report(clean, [results[0]], None).read_text()
    assert "## Failures" not in text and "## vs baseline" not in text


def test_main_validation_fixture_failure_and_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch, [_scenario()])
    assert agent_rollouts.main(["--limit", "-1"]) == 2
    assert agent_rollouts.main(["--min-pass-rate", "nan"]) == 2
    assert agent_rollouts.main(["--min-pass-rate", "2"]) == 2
    assert agent_rollouts.main(["--force-baseline"]) == 2
    paths["fixture"].write_text("{")
    assert agent_rollouts.main([]) == 2
    assert "invalid fixture" in capsys.readouterr().out

    atomic_write_json(paths["fixture"], {"scenarios": [_scenario(), _scenario("case_two", "out")]})
    monkeypatch.setattr(
        agent_rollouts,
        "run_scenario",
        lambda scenario, _playbook, use_llm: {
            "id": scenario["id"],
            "split": scenario["split"],
            "ok": scenario["id"] == "case_one",
            "skipped": False,
            "errors": [] if scenario["id"] == "case_one" else ["failed"],
            "response": "draft",
        },
    )
    assert agent_rollouts.main(["--dry-run", "--limit", "1"]) == 0
    assert not paths["state"].exists()
    assert not paths["transcripts"].exists()
    assert agent_rollouts.main(["--dry-run"]) == 1


def test_main_persistence_baseline_and_gate_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenarios = [_scenario(), _scenario("case_two", "out"), _scenario("case_three", "out"), _scenario("case_four", "out")]
    paths = _configure(tmp_path, monkeypatch, scenarios)

    def passing(scenario: dict, _playbook: str, use_llm: bool) -> dict:
        return {
            "id": scenario["id"],
            "split": scenario["split"],
            "ok": True,
            "skipped": False,
            "errors": [],
            "response": f"draft {scenario['id']}",
        }

    monkeypatch.setattr(agent_rollouts, "run_scenario", passing)
    assert agent_rollouts.main(["--gate"]) == 0
    assert json.loads(paths["last"].read_text())["summary"]["accept"] is True
    assert json.loads(paths["baseline"].read_text())["pass_rate"] == 1.0
    assert len(load_jsonl_objects(paths["history"]).records) == 1
    assert len(list(paths["transcripts"].glob("*.txt"))) == 4

    paths["baseline"].write_text("{")
    assert agent_rollouts.main(["--gate"]) == 1
    assert "regression vs baseline" in capsys.readouterr().out

    atomic_write_json(
        paths["baseline"],
        {"ts": "old", "pass_rate": 0.5, "d_in": {"pass_rate": 0}, "d_out": {"pass_rate": 0}},
    )

    def one_failure(scenario: dict, _playbook: str, use_llm: bool) -> dict:
        result = passing(scenario, _playbook, use_llm)
        if scenario["id"] == "case_four":
            result["ok"] = False
            result["errors"] = ["failure"]
        return result

    monkeypatch.setattr(agent_rollouts, "run_scenario", one_failure)
    before = paths["baseline"].read_text()
    assert agent_rollouts.main(["--gate", "--min-pass-rate", "0.7", "--update-baseline"]) == 0
    assert paths["baseline"].read_text() == before
    assert "baseline NOT updated" in capsys.readouterr().out
    assert agent_rollouts.main(["--update-baseline", "--force-baseline"]) == 1
    assert json.loads(paths["baseline"].read_text())["accept"] is False
    assert "baseline FORCE" in capsys.readouterr().out
    assert agent_rollouts.main(["--gate", "--min-pass-rate", "0.8"]) == 1
    assert "absolute floor" in capsys.readouterr().out


def test_rollout_final_schema_playbook_and_rubric_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario()
    scenario["eval_expect"] = {
        "match": {"applied": True, "passed": False},
        "nullable": {"passed": None},
        "applied_only": {"applied": False},
    }
    normalized, error = agent_rollouts.normalize_scenario(scenario)
    assert error is None and normalized is not None
    assert normalized["eval_expect"] == scenario["eval_expect"]

    ace = tmp_path / "ace.json"
    monkeypatch.setattr(agent_rollouts, "ACE", ace)
    atomic_write_json(
        ace,
        {
            "bullets": [
                {"section": "strategy", "pattern": "one", "description": "First"},
                {"section": "strategy", "pattern": "two", "description": "Second", "quality": 2},
            ]
        },
    )
    assert agent_rollouts.load_playbook_bullets(3).splitlines() == [
        "- [one] First",
        "- [two] q2 Second",
    ]

    monkeypatch.setattr(
        agent_rollouts,
        "score_text",
        lambda _text: {
            "match": {"applied": True, "passed": False},
            "nullable": {"applied": False, "passed": True},
            "applied_only": {"applied": False, "passed": True},
        },
    )
    scenario["must_not_match"] = ["not present", "also absent"]
    scenario["must_match_any"] = ["safe", "present"]
    ok, errors = agent_rollouts.rubric_ok("A safe response is present", scenario)
    assert ok is True and errors == []
