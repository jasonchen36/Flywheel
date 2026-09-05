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
