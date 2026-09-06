from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import self_harness
from state_io import atomic_write_json, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    harness = tmp_path / "harness"
    state = harness / "MEMORY" / "STATE"
    learning = harness / "MEMORY" / "LEARNING"
    signals = learning / "SIGNALS"
    diagnostics = learning / "DIAGNOSTICS"
    lessons = harness / "MEMORY" / "lessons"
    for path in (state, signals, diagnostics, lessons):
        path.mkdir(parents=True)
    paths = {
        "harness": harness,
        "state": state,
        "signals": signals,
        "diagnostics": diagnostics,
        "lessons": lessons,
        "surfaces": learning / "editable_surfaces.json",
        "scores": state / "effectiveness_scores.json",
        "ledger": state / "skill_autofix_ledger.json",
        "negative": signals / "negative_results.jsonl",
        "archive": state / "harness_candidates.jsonl",
        "ratings": signals / "ratings.jsonl",
    }
    monkeypatch.setattr(self_harness, "HARNESS_HOME", harness)
    monkeypatch.setattr(self_harness, "SURFACES", paths["surfaces"])
    monkeypatch.setattr(self_harness, "SCORES", paths["scores"])
    monkeypatch.setattr(self_harness, "LEDGER", paths["ledger"])
    monkeypatch.setattr(self_harness, "NEG", paths["negative"])
    monkeypatch.setattr(self_harness, "DIAG", diagnostics)
    monkeypatch.setattr(self_harness, "ARCHIVE", paths["archive"])
    monkeypatch.setattr(self_harness, "LESSONS_DIR", lessons)
    monkeypatch.setattr(self_harness, "RATINGS_FILE", paths["ratings"])
    return paths


def test_state_loaders_normalize_missing_malformed_and_dynamic_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    assert self_harness.load_surfaces() == {"allow": [], "deny": []}
    paths["surfaces"].write_text("{")
    assert self_harness.load_surfaces() == {"allow": [], "deny": []}
    atomic_write_json(
        paths["surfaces"],
        {"allow": [{"id": "lesson_autogen"}, "bad"], "deny": ["protected"]},
    )
    assert self_harness.load_surfaces() == {
        "allow": [{"id": "lesson_autogen"}],
        "deny": ["protected"],
    }

    atomic_write_json(paths["scores"], {"scores": "bad"})
    assert self_harness.load_scores() == {}
    atomic_write_json(paths["scores"], {"scores": {"good": {"verdict": "working"}, "bad": []}})
    assert self_harness.load_scores() == {"good": {"verdict": "working"}}

    child = paths["state"] / "child.json"
    atomic_write_json(child, {"summary": [], "gate": "bad"})
    assert self_harness.load_child_result(child) == ({}, {})
    atomic_write_json(child, {"summary": {"accept": True}, "gate": {"gate_pass": True}})
    assert self_harness.load_child_result(child) == (
        {"accept": True},
        {"gate_pass": True},
    )


def test_stage_mine_and_propose_filter_to_allowed_addressable_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    entries = [
        SimpleNamespace(
            rating=2,
            timestamp="t1",
            skill="review",
            sentiment_summary="missing evidence",
        ),
        SimpleNamespace(
            rating=3,
            timestamp="t2",
            skill="review",
            sentiment_summary="missing evidence again",
        ),
        SimpleNamespace(
            rating=8,
            timestamp="t3",
            skill="testing",
            sentiment_summary="good",
        ),
    ]
    monkeypatch.setattr(self_harness, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(
        self_harness,
        "classify_entry",
        lambda entry: ["unverified_completion"] if entry.rating <= 4 else ["other"],
    )
    mine = self_harness.stage_mine()
    assert mine["low_n"] == 2
    assert mine["addressable"] == ["unverified_completion"]
    assert mine["top_skills"] == [("review", 2)]

    atomic_write_json(
        paths["surfaces"],
        {"allow": [{"id": "lesson_autogen"}, {"id": "skill_guardrails"}], "deny": ["x"]},
    )
    atomic_write_json(
        paths["scores"],
        {"scores": {"unverified_completion": {"verdict": "regressed"}}},
    )
    proposed = self_harness.stage_propose(mine)
    assert proposed["proposal_n"] == 2
    assert {row["surface"] for row in proposed["proposals"]} == {
        "lesson_autogen",
        "skill_guardrails",
    }
    assert proposed["deny_count"] == 1


def test_child_runners_report_process_and_normalized_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(
        paths["state"] / "held_out_suite_last.json",
        {"summary": {"accept": True}, "gate": {"gate_pass": True}},
    )
    atomic_write_json(
        paths["state"] / "agent_rollouts_last.json",
        {"summary": {"accept": False, "pass_rate": 0.5}, "gate": {}},
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "x" * 2100, "y" * 700)

    monkeypatch.setattr(self_harness.subprocess, "run", fake_run)
    suite = self_harness.run_held_out_suite(gate=True)
    rollouts = self_harness.run_agent_rollouts(gate=True, no_llm=True)
    assert commands[0][-1] == "--gate"
    assert commands[1][-2:] == ["--gate", "--no-llm"]
    assert suite["suite_accept"] is True
    assert len(suite["stdout_tail"]) == 1500
    assert len(suite["stderr_tail"]) == 500
    assert rollouts["pass_rate"] == 0.5
    assert rollouts["gate_pass"] is False


def test_stage_validate_archives_reverts_once_and_detects_diversity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(
        paths["scores"],
        {
            "scores": {
                "regressed": {"verdict": "regressed"},
                "resolved": {"verdict": "resolved"},
            }
        },
    )
    atomic_write_json(
        paths["ledger"],
        {
            "edits": [
                {
                    "status": "reverted",
                    "skill": "review",
                    "pattern": "proof",
                    "commit_after": "abc",
                    "applied": "2026-09-05",
                    "verdict": "regressed",
                },
                {"status": "active", "skill": "other"},
                "bad",
            ]
        },
    )
    rule = "Always verify every final claim with concrete command output before completion."
    for name in ("one", "two"):
        (paths["lessons"] / f"lesson_autogen_{name}.md").write_text(
            f"---\npattern: {name}\n---\n\n{rule}\n"
        )
    monkeypatch.setattr(
        self_harness,
        "run_held_out_suite",
        lambda gate=False: {
            "summary": {
                "d_in": {"pass_rate": 1.0},
                "d_out": {"pass_rate": 1.0},
            },
            "suite_accept": True,
            "gate_pass": True,
            "exit_code": 0,
        },
    )
    monkeypatch.setattr(
        self_harness,
        "run_agent_rollouts",
        lambda gate=False, no_llm=False: {
            "summary": {"d_in": {}, "d_out": {}, "skipped_all": False},
            "pass_rate": 1.0,
            "suite_accept": True,
            "gate_pass": True,
            "exit_code": 0,
        },
    )

    first = self_harness.stage_validate()
    second = self_harness.stage_validate(run_suite=False, run_rollouts=False)
    assert first["regressed"] == ["regressed"]
    assert first["resolved_n"] == 1
    assert first["skill_reverts_logged"] == 1
    assert first["near_duplicate_lessons"][0]["jaccard"] == 1.0
    assert second["skill_reverts_logged"] == 0
    assert len(load_jsonl_objects(paths["negative"]).records) == 1


def _accepted_validation(*, rollout_rate: object = 1.0, skipped: bool = False) -> dict:
    return {
        "held_in_verdicts": {},
        "regressed": [],
        "near_duplicate_lessons": [],
        "accept_policy": "policy",
        "held_out_suite": {
            "d_in_rate": 1.0,
            "d_out_rate": 1.0,
            "suite_accept": True,
            "gate_pass": True,
            "exit_code": 0,
        },
        "agent_rollouts": {
            "pass_rate": rollout_rate,
            "suite_accept": True,
            "gate_pass": True,
            "exit_code": 0,
            "d_in": {},
            "d_out": {},
            "skipped_all": skipped,
        },
    }


def test_main_reuses_validation_results_for_gate_without_duplicate_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(self_harness, "stage_mine", lambda: {"low_n": 0, "total_n": 0, "addressable": [], "top_patterns": []})
    monkeypatch.setattr(self_harness, "stage_propose", lambda _mine: {"proposal_n": 0, "allow_ids": [], "proposals": []})
    monkeypatch.setattr(self_harness, "stage_validate", lambda **_kwargs: _accepted_validation())
    monkeypatch.setattr(
        self_harness,
        "run_held_out_suite",
        lambda **_kwargs: pytest.fail("gate must reuse validation held-out result"),
    )
    monkeypatch.setattr(
        self_harness,
        "run_agent_rollouts",
        lambda **_kwargs: pytest.fail("gate must reuse validation rollout result"),
    )

    assert self_harness.main(["--gate"]) == 0
    assert list(paths["diagnostics"].glob("self_harness_*.json"))
    assert len(load_jsonl_objects(paths["archive"]).records) == 1


def test_main_gate_rejects_recorded_failures_and_handles_skipped_rollouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(self_harness, "stage_validate", lambda **_kwargs: _accepted_validation(skipped=True))
    assert self_harness.main(["--stage", "validate", "--gate"]) == 0

    monkeypatch.setattr(self_harness, "stage_validate", lambda **_kwargs: _accepted_validation(rollout_rate="bad"))
    assert self_harness.main(["--stage", "validate", "--gate"]) == 1

    failed = _accepted_validation()
    failed["held_out_suite"]["suite_accept"] = False
    monkeypatch.setattr(self_harness, "stage_validate", lambda **_kwargs: failed)
    assert self_harness.main(["--stage", "validate", "--gate"]) == 1


def test_run_child_handles_timeouts_and_spawn_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        self_harness.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["child"], 2, output=b"x" * 2100, stderr=b"y" * 700)
        ),
    )
    timed = self_harness.run_child(["child"], timeout=2)
    assert timed["exit_code"] == 124
    assert len(timed["stdout_tail"]) == 2000
    assert len(timed["stderr_tail"]) == 500
    assert "timed out" in str(timed["error"])

    monkeypatch.setattr(
        self_harness.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    unavailable = self_harness.run_child(["child"])
    assert unavailable["exit_code"] == 127
    assert unavailable["stdout_tail"] == ""
    assert "OSError" in str(unavailable["error"])


def test_child_wrappers_cover_optional_argument_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(
        paths["state"] / "held_out_suite_last.json",
        {"summary": {}, "gate": {}},
    )
    atomic_write_json(
        paths["state"] / "agent_rollouts_last.json",
        {"summary": {}, "gate": {}},
    )
    commands: list[list[str]] = []

    def fake(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(self_harness.subprocess, "run", fake)
    assert self_harness.run_held_out_suite()["gate_pass"] is True
    assert self_harness.run_agent_rollouts()["gate_pass"] is True
    assert commands[0][-1].endswith("held_out_suite.py")
    assert commands[1][-1].endswith("agent_rollouts.py")


def test_stage_validate_tolerates_unreadable_and_empty_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(
        paths["ledger"],
        {
            "edits": [
                {"status": "reverted", "skill": "same", "pattern": "p", "commit_after": "c", "applied": "a"},
                {"status": "reverted", "skill": "same", "pattern": "p", "commit_after": "c", "applied": "a"},
            ]
        },
    )
    readable = paths["lessons"] / "lesson_autogen_readable.md"
    empty = paths["lessons"] / "lesson_autogen_empty.md"
    other = paths["lessons"] / "lesson_autogen_other.md"
    unreadable = paths["lessons"] / "lesson_autogen_unreadable.md"
    readable.write_text("---\n---\nAlways verify concrete output before reporting completion.\n")
    empty.write_text("---\n---\n# only heading\n**Root cause:** metadata\n")
    other.write_text("---\n---\nNever repeat a failed command without new evidence.\n")
    unreadable.write_text("x")
    original = Path.read_text

    def read(path: Path, *args: object, **kwargs: object) -> str:
        if path == unreadable:
            raise OSError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = self_harness.stage_validate(run_suite=False, run_rollouts=False)
    assert result["skill_reverts_logged"] == 1
    assert result["unreadable_lessons"] == [unreadable.name]
    assert result["near_duplicate_lessons"] == []
    assert len(load_jsonl_objects(paths["negative"]).records) == 1


def test_validation_gate_failure_matrix() -> None:
    assert self_harness.validation_gate_failure({}) == "held_out_suite result is missing"
    failed_held = _accepted_validation()
    failed_held["held_out_suite"]["exit_code"] = 1
    assert "held_out_suite" in self_harness.validation_gate_failure(failed_held)
    missing_rollouts = _accepted_validation()
    missing_rollouts["agent_rollouts"] = []
    assert self_harness.validation_gate_failure(missing_rollouts) == "agent_rollouts result is missing"
    assert self_harness.validation_gate_failure(_accepted_validation(skipped=True)) is None
    for rate in (True, None, "bad", -0.1, 1.1):
        invalid = _accepted_validation(rollout_rate=rate)
        assert self_harness.validation_gate_failure(invalid) == "agent_rollouts pass rate is invalid"
    for field, value in (
        ("exit_code", 1),
        ("suite_accept", False),
        ("pass_rate", 0.5),
        ("gate_pass", False),
    ):
        failed = _accepted_validation()
        failed["agent_rollouts"][field] = value
        assert "agent_rollouts regression" in self_harness.validation_gate_failure(failed)
    assert self_harness.validation_gate_failure(_accepted_validation()) is None


def test_run_ace_playbook_reports_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        self_harness,
        "run_child",
        lambda _command: {"exit_code": 1, "error": "timeout", "stderr_tail": "bad"},
    )
    assert self_harness.run_ace_playbook() is False
    assert "ACE rebuild failed" in capsys.readouterr().out
    monkeypatch.setattr(
        self_harness,
        "run_child",
        lambda _command: {"exit_code": 0, "error": None, "stderr_tail": ""},
    )
    assert self_harness.run_ace_playbook() is True


def test_main_requires_validation_and_gates_before_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(tmp_path, monkeypatch)
    assert self_harness.main(["--stage", "mine", "--gate"]) == 2
    assert self_harness.main(["--stage", "propose", "--apply"]) == 2

    mine = {"low_n": 0, "total_n": 0, "addressable": [], "top_patterns": [], "top_skills": []}
    monkeypatch.setattr(self_harness, "stage_mine", lambda: mine)
    monkeypatch.setattr(
        self_harness,
        "stage_propose",
        lambda value: {"proposal_n": 0, "allow_ids": [], "proposals": [], "source": value},
    )
    assert self_harness.main(["--stage", "propose"]) == 0

    failed = _accepted_validation()
    failed["held_out_suite"]["suite_accept"] = False
    monkeypatch.setattr(self_harness, "stage_validate", lambda **_kwargs: failed)
    monkeypatch.setattr(
        self_harness,
        "run_ace_playbook",
        lambda: pytest.fail("ACE must not run after a failed gate"),
    )
    assert self_harness.main(["--stage", "validate", "--apply"]) == 1

    monkeypatch.setattr(self_harness, "stage_validate", lambda **_kwargs: _accepted_validation())
    monkeypatch.setattr(self_harness, "run_ace_playbook", lambda: False)
    assert self_harness.main(["--stage", "validate", "--apply"]) == 1
    monkeypatch.setattr(self_harness, "run_ace_playbook", lambda: True)
    assert self_harness.main(["--stage", "validate", "--apply", "--gate"]) == 0
    assert "GATE PASS" in capsys.readouterr().out


def test_skip_rollouts_without_recorded_result_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(
        self_harness,
        "run_held_out_suite",
        lambda gate=False: {
            "summary": {"d_in": {}, "d_out": {}},
            "suite_accept": True,
            "gate_pass": True,
            "exit_code": 0,
        },
    )
    assert self_harness.main(["--stage", "validate", "--skip-rollouts", "--gate"]) == 1


def test_self_harness_final_branch_arcs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    entries = [
        SimpleNamespace(rating=1, timestamp="t1", skill="", sentiment_summary="x"),
        SimpleNamespace(rating=2, timestamp="t2", skill="review", sentiment_summary="y"),
    ]
    monkeypatch.setattr(self_harness, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(self_harness, "classify_entry", lambda _entry: ["first", "second"])
    mine = self_harness.stage_mine()
    assert mine["top_patterns"] == [("first", 2), ("second", 2)]
    assert mine["sample_rich"][0]["agent_mechanism"] == "unattributed"

    atomic_write_json(paths["surfaces"], {"allow": [{"id": "lesson_autogen"}], "deny": []})
    atomic_write_json(
        paths["scores"],
        {"scores": {"first": {"verdict": "resolved"}, "second": {"verdict": "pending"}}},
    )
    proposed = self_harness.stage_propose({"addressable": ["first", "second"], "top_skills": []})
    assert [row["pattern"] for row in proposed["proposals"]] == ["second"]

    missing_lessons = tmp_path / "missing-lessons"
    monkeypatch.setattr(self_harness, "LESSONS_DIR", missing_lessons)
    validation = self_harness.stage_validate(run_suite=False, run_rollouts=False)
    assert validation["near_duplicate_lessons"] == []

    lessons = tmp_path / "comparison-lessons"
    lessons.mkdir()
    (lessons / "lesson_autogen_a_readable.md").write_text(
        "---\n---\nAlways verify concrete command output before completion.\n"
    )
    (lessons / "lesson_autogen_b_empty.md").write_text("---\n---\n# heading only\n")
    monkeypatch.setattr(self_harness, "LESSONS_DIR", lessons)
    validation = self_harness.stage_validate(run_suite=False, run_rollouts=False)
    assert validation["near_duplicate_lessons"] == []

    monkeypatch.setattr(
        self_harness,
        "stage_mine",
        lambda: {"low_n": 1, "total_n": 1, "addressable": ["first"], "top_patterns": []},
    )
    monkeypatch.setattr(
        self_harness,
        "stage_propose",
        lambda _mine: {
            "proposal_n": 1,
            "allow_ids": ["lesson_autogen"],
            "proposals": [
                {
                    "surface": "lesson_autogen",
                    "action": "evolve",
                    "verdict": "pending",
                }
            ],
        },
    )
    assert self_harness.main(["--stage", "propose"]) == 0
