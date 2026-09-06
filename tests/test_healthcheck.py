from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING_ROOT = ROOT / "learning"
sys.path.insert(0, str(LEARNING_ROOT))

import harness_healthcheck
from state_io import append_jsonl, atomic_write_json


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    harness = tmp_path / "harness"
    learning = harness / "MEMORY" / "LEARNING"
    state = harness / "MEMORY" / "STATE"
    signals = learning / "SIGNALS"
    lessons = harness / "MEMORY" / "lessons"
    hooks = harness / "hooks"
    diagnostics = learning / "DIAGNOSTICS"
    ratings = signals / "ratings.jsonl"
    for path in (learning, state, signals, lessons, hooks, diagnostics):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(harness_healthcheck, "HARNESS_HOME", harness)
    monkeypatch.setattr(harness_healthcheck, "LEARNING", learning)
    monkeypatch.setattr(harness_healthcheck, "STATE", state)
    monkeypatch.setattr(harness_healthcheck, "SIGNALS", signals)
    monkeypatch.setattr(harness_healthcheck, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(harness_healthcheck, "MEM", lessons)
    monkeypatch.setattr(harness_healthcheck, "RATINGS_FILE", ratings)
    return {
        "harness": harness,
        "learning": learning,
        "state": state,
        "signals": signals,
        "lessons": lessons,
        "hooks": hooks,
        "diagnostics": diagnostics,
        "ratings": ratings,
    }


def _create_critical(paths: dict[str, Path]) -> None:
    for name in (
        "sync_graph_memory.py",
        "flush_graphiti_pending.py",
        "session_graphiti_autoseed.py",
        "self_harness.py",
        "ace_reflector.py",
        "ace_playbook.py",
        "agent_rollouts.py",
        "consolidate_memory.py",
    ):
        (paths["learning"] / name).write_text("# installed\n")
    (paths["hooks"] / "harness-session-end.sh").write_text("#!/bin/sh\n")
    (paths["hooks"] / "EnforcementGate.hook.ts").write_text("// installed\n")


def _seed_healthy(paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc).isoformat()
    atomic_write_json(
        paths["state"] / "effectiveness_scores.json",
        {
            "measured_at": now,
            "scores": {"working_pattern": {"verdict": "working", "injectable": True}},
            "escalate": [],
        },
    )
    atomic_write_json(
        paths["state"] / "enforcement_config.json",
        {
            "enabled": True,
            "overrides": {
                "graphiti_bypassed": "block",
                "unverified_completion": "block",
                "unverified_claims": "block",
                "claim_evidence": "block",
                "silent_completion": "block",
            },
        },
    )
    atomic_write_json(paths["state"] / "skill_autofix_ledger.json", {"edits": []})
    atomic_write_json(
        paths["state"] / "held_out_suite_last.json",
        {"summary": {"accept": True}, "gate": {"gate_pass": True}},
    )
    atomic_write_json(
        paths["state"] / "agent_rollouts_last.json",
        {"summary": {"pass_rate": 1.0}, "gate": {"gate_pass": True}},
    )
    (paths["state"] / "graph_preflight.md").write_text("healthy\n")
    (paths["state"] / "anti_hallucination.md").write_text("verify\n")
    (paths["lessons"] / "lesson_autogen_working_pattern.md").write_text(
        "first_seen: 2026-09-01\nbaseline_date: 2026-09-01\n"
    )
    append_jsonl(paths["ratings"], {"timestamp": now})
    monkeypatch.setattr(
        harness_healthcheck,
        "rating_loader",
        lambda _path: [
            SimpleNamespace(
                skill="review",
                agent="claude",
                skill_candidates=["review", "testing"],
            )
        ],
    )
    _create_critical(paths)


def test_healthcheck_reports_healthy_nested_gate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)

    assert harness_healthcheck.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["checks"]["gates"]["held_out_accept"] is True
    assert report["checks"]["ratings"]["clean_with_skill_non_general_rate"] == 1.0
    assert report["checks"]["signal_freshness"]["rating_age_days"] == 0
    assert report["checks"]["files"]["missing"] == []


def test_healthcheck_surfaces_degraded_and_malformed_runtime_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    scores = {
        f"pending_{index}": {"verdict": "pending"} for index in range(21)
    }
    scores["unverified_completion"] = {"verdict": "regressed"}
    atomic_write_json(
        paths["state"] / "effectiveness_scores.json",
        {"scores": scores, "escalate": []},
    )
    atomic_write_json(
        paths["state"] / "enforcement_config.json",
        {"enabled": "yes", "overrides": {"unknown": "explode"}},
    )
    atomic_write_json(
        paths["state"] / "skill_autofix_ledger.json",
        {
            "edits": [
                {
                    "status": "active",
                    "applied": "2020-01-01",
                    "skill": "review",
                    "pattern": "blind_retry",
                    "post_n": 0,
                },
                "bad-row",
            ]
        },
    )
    (paths["lessons"] / "lesson_autogen_broken.md").write_text("no anchors\n")
    append_jsonl(paths["ratings"], {"timestamp": "2020-01-01T00:00:00+00:00"})
    paths["ratings"].open("a").write("not-json\n[]\n")
    monkeypatch.setattr(
        harness_healthcheck,
        "rating_loader",
        lambda _path: [SimpleNamespace(skill="", agent="", skill_candidates=[])],
    )
    for index in range(11):
        append_jsonl(paths["state"] / "graphiti_pending_episodes.jsonl", {"id": index})
    (paths["state"] / "graphiti_pending_episodes.jsonl").open("a").write("bad\n")
    review = paths["signals"] / "pending_human_review.jsonl"
    append_jsonl(review, {"pattern": "failed", "status": "action_failed"})
    append_jsonl(review, {"pattern": "running", "status": "processing"})
    append_jsonl(review, {"pattern": "mystery", "status": "unknown"})
    review.open("a").write("bad\n")

    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["checks"]["effectiveness"]["pending_n"] == 21
    assert report["checks"]["graph"]["pending_episodes"] == 11
    assert report["checks"]["graph"]["pending_invalid_lines"] == [12]
    assert report["checks"]["signal_freshness"]["invalid_lines"] == [2, 3]
    assert report["checks"]["review_queue"]["unknown_statuses"] == ["unknown"]
    assert report["checks"]["review_queue"]["failed_patterns"] == ["failed"]
    assert report["checks"]["skill_autofix"]["burnin_stalled"]
    assert any("RATINGS FLATLINE" in error for error in report["errors"])
    assert any("graph state has malformed" in error for error in report["errors"])
    assert any("invalid enforcement_config" in error for error in report["errors"])
    assert any("unverified_completion is regressed" in error for error in report["errors"])
    assert any("missing files" in error for error in report["errors"])
    assert any("effectiveness stuck" in warning for warning in report["warnings"])
    assert any("ratings contain malformed" in warning for warning in report["warnings"])


def test_healthcheck_human_output_and_unparseable_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    paths["ratings"].write_text('{"timestamp":"not-a-date"}\n')

    assert harness_healthcheck.main([]) == 1
    output = capsys.readouterr().out
    assert "# Harness healthcheck" in output
    assert "Signal freshness" in output
    assert "no parseable rating timestamps" in output


def test_healthcheck_rejects_collection_shape_drift_and_detector_holes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    atomic_write_json(
        paths["state"] / "effectiveness_scores.json",
        {"scores": "not-an-object", "escalate": "not-a-list"},
    )
    atomic_write_json(
        paths["state"] / "skill_autofix_ledger.json",
        {"edits": "not-a-list"},
    )
    import evals

    monkeypatch.setattr(evals, "has_strong_artifact", lambda _text: True)
    monkeypatch.setattr(
        evals,
        "score_text",
        lambda _text: {"completion_without_artifact": {"passed": True}},
    )

    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "effectiveness scores must be a JSON object" in report["errors"]
    assert "effectiveness escalate must be a JSON list" in report["errors"]
    assert "skill_autofix edits must be a JSON list of objects" in report["errors"]
    assert any("bare '5000 rows'" in error for error in report["errors"])
    assert any("bare-metric completion" in error for error in report["errors"])


def test_healthcheck_handles_startup_and_degraded_sensor_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    paths["ratings"].write_text("")
    monkeypatch.setattr(harness_healthcheck, "rating_loader", lambda _path: [])
    atomic_write_json(
        paths["state"] / "skill_autofix_ledger.json",
        {"edits": [{"status": "active", "applied": "bad-date"}]},
    )
    import evals

    def unavailable(_text: str) -> dict:
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(evals, "score_text", unavailable)
    assert harness_healthcheck.main(["--json"]) == 0
    startup = json.loads(capsys.readouterr().out)
    assert startup["checks"]["signal_freshness"]["status"] == "no_ratings_yet"
    assert any("detector probe skipped" in warning for warning in startup["warnings"])

    three_days_ago = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta

    paths["ratings"].write_text(
        json.dumps({"timestamp": (three_days_ago - timedelta(days=3)).isoformat()}) + "\n"
    )
    monkeypatch.setattr(
        harness_healthcheck,
        "rating_loader",
        lambda _path: [SimpleNamespace(skill="review", agent="claude", skill_candidates=["review"])],
    )
    monkeypatch.setattr(evals, "score_text", lambda _text: {"completion_without_artifact": {"passed": False}})
    monkeypatch.setattr(evals, "has_strong_artifact", lambda _text: False)
    assert harness_healthcheck.main(["--json"]) == 0
    degraded = json.loads(capsys.readouterr().out)
    assert degraded["checks"]["signal_freshness"]["rating_age_days"] == 3
    assert any("capture may be degraded" in warning for warning in degraded["warnings"])


def test_healthcheck_handles_malformed_score_rows_and_nested_gate_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    atomic_write_json(
        paths["state"] / "effectiveness_scores.json",
        {"scores": {"bad_pattern": "not-an-object"}, "escalate": []},
    )
    atomic_write_json(
        paths["state"] / "held_out_suite_last.json",
        {"summary": "bad-summary", "gate": "bad-gate"},
    )
    atomic_write_json(
        paths["state"] / "agent_rollouts_last.json",
        {"summary": "bad-summary", "gate": "bad-gate"},
    )

    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "effectiveness score rows must be JSON objects: ['bad_pattern']" in report["errors"]
    assert report["checks"]["gates"] == {
        "held_out_suite_gate_pass": None,
        "held_out_accept": None,
        "agent_rollouts_gate_pass": None,
        "agent_rollouts_pass_rate": None,
        "agent_rollouts_skipped_all": False,
        "agent_rollouts_baseline_error": None,
    }


def test_healthcheck_reports_outcome_judge_backlog_quarantine_and_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    pending = paths["signals"] / "pending_judge.jsonl"
    for index in range(51):
        append_jsonl(
            pending,
            {
                "timestamp": f"2026-09-06T12:00:{index:02d}Z",
                "session_id": f"session-{index}",
                "response": "substantive response",
            },
        )
    append_jsonl(
        paths["signals"] / "invalid_judge.jsonl",
        {"turn_id": "bad", "reason": "missing response", "record": {}},
    )
    (paths["signals"] / "judge_results.jsonl").write_text("not-json\n")

    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    judge = report["checks"]["outcome_judge"]
    assert judge["pending"] == 51
    assert judge["results"] == 0
    assert judge["quarantined"] == 1
    assert judge["result_invalid_lines"] == [1]
    assert any("outcome judge state has malformed" in error for error in report["errors"])
    assert any("quarantined outcome-judge" in warning for warning in report["warnings"])
    assert any("51 outcome-judge turns still pending" in warning for warning in report["warnings"])


def test_healthcheck_human_output_includes_clean_outcome_judge_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    assert harness_healthcheck.main([]) == 0
    output = capsys.readouterr().out
    assert "Outcome judge:" in output
    assert "'pending': 0" in output
    assert "'quarantined': 0" in output


def test_healthcheck_surfaces_autofix_rollback_audit_and_quarantine_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    atomic_write_json(
        paths["state"] / "skill_autofix_ledger.json",
        {
            "edits": [
                {"skill": "deploy", "pattern": "tool_misuse", "status": "rollback-failed"},
                {"skill": "review", "pattern": "missing_proof", "status": "invalid"},
                {"skill": "test", "pattern": "blind_retry", "status": "reverted-audit-failed"},
                {"skill": "build", "pattern": "missing_dependency", "status": "apply-audit-failed"},
            ],
            "invalid_edits": [{"skill": "bad"}],
        },
    )

    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    check = report["checks"]["skill_autofix"]
    assert check["quarantined_edits"] == 1
    assert len(check["failed_edits"]) == 4
    assert any("unresolved critical edits" in error for error in report["errors"])
    assert any("audit-failed edits" in warning for warning in report["warnings"])
    assert any("quarantined 1 malformed" in warning for warning in report["warnings"])


def test_healthcheck_reports_ace_and_self_harness_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    atomic_write_json(
        paths["state"] / "ace_playbook.json",
        {
            "generated_at": "2026-09-06T00:00:00Z",
            "stats": {"weak_output": 2},
            "bullets": [
                {"section": "strategy", "pattern": "good", "description": "rule"},
                {"section": "resolved", "pattern": "old", "description": "rule"},
            ],
        },
    )
    atomic_write_json(
        paths["diagnostics"] / "self_harness_latest.json",
        {
            "ts": "2026-09-06T00:00:00Z",
            "stages": {"mine": {}, "validate": {"unreadable_lessons": ["lesson_bad.md"]}},
        },
    )
    assert harness_healthcheck.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["ace_playbook"]["active_bullets"] == 1
    assert report["checks"]["ace_playbook"]["weak_output"] == 2
    assert report["checks"]["self_harness"]["stages"] == ["mine", "validate"]
    assert any("weak output" in warning for warning in report["warnings"])
    assert any("could not read" in warning for warning in report["warnings"])


def test_healthcheck_fails_on_malformed_ace_self_harness_and_gate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    atomic_write_json(paths["state"] / "ace_playbook.json", {"bullets": ["bad", {"section": "strategy"}]})
    (paths["diagnostics"] / "self_harness_latest.json").write_text("{")
    atomic_write_json(
        paths["state"] / "held_out_suite_last.json",
        {"summary": {"accept": False}, "gate": {"gate_pass": False}},
    )
    atomic_write_json(
        paths["state"] / "agent_rollouts_last.json",
        {
            "summary": {"accept": False, "pass_rate": 0.5, "skipped_all": False},
            "gate": {"gate_pass": False, "error": "corrupt baseline"},
        },
    )
    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("held-out suite" in error for error in report["errors"])
    assert any("agent rollouts" in error for error in report["errors"])
    assert any("baseline invalid" in error for error in report["errors"])
    assert any("malformed bullets" in error for error in report["errors"])
    assert any("self_harness_latest.json" in error for error in report["errors"])

    atomic_write_json(paths["state"] / "ace_playbook.json", {"bullets": "bad"})
    atomic_write_json(
        paths["state"] / "agent_rollouts_last.json",
        {
            "summary": {"accept": False, "pass_rate": 0.0, "skipped_all": True},
            "gate": {"gate_pass": False},
        },
    )
    atomic_write_json(
        paths["state"] / "held_out_suite_last.json",
        {"summary": {"accept": True}, "gate": {"gate_pass": True}},
    )
    atomic_write_json(paths["diagnostics"] / "self_harness_latest.json", {"stages": []})
    assert harness_healthcheck.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "ACE playbook bullets must be a JSON list" in report["errors"]
    assert not any(error == "agent rollouts last run did not pass" for error in report["errors"])


def test_healthcheck_surfaces_self_harness_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    _seed_healthy(paths, monkeypatch)
    latest = paths["diagnostics"] / "self_harness_latest.json"
    atomic_write_json(
        latest,
        {"ts": "2026-09-06T00:00:00Z", "stages": {}, "outcome": {"status": "applied", "gate_error": None}},
    )
    assert harness_healthcheck.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["self_harness"]["outcome_status"] == "applied"

    for status, message in (
        ("rejected", "held-out regression"),
        ("apply_failed", None),
        ("unknown", None),
    ):
        atomic_write_json(
            latest,
            {"stages": {}, "outcome": {"status": status, "gate_error": message}},
        )
        assert harness_healthcheck.main(["--json"]) == 1
        report = json.loads(capsys.readouterr().out)
        if status == "unknown":
            assert any("outcome status is invalid" in error for error in report["errors"])
        else:
            assert any(f"last cycle {status}" in error for error in report["errors"])
