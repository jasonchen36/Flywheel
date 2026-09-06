from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import skill_autofix
from state_io import lock_path_for


@dataclass
class Entry:
    timestamp: str = "2026-09-06T12:00:00Z"
    rating: float = 2
    skill: str = "deploy"
    skill_candidates: list[str] = field(default_factory=list)
    sentiment_summary: str = "Used the wrong tool"
    response_preview: str = ""


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    commands = tmp_path / "commands"
    pi_skills = tmp_path / "pi-skills"
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    for path in (commands, pi_skills, state, diagnostics):
        path.mkdir(parents=True)
    paths = {
        "commands": commands,
        "pi_skills": pi_skills,
        "state": state,
        "diagnostics": diagnostics,
        "ledger": state / "skill_autofix_ledger.json",
        "repo": state / "skillfix_repo",
        "ratings": tmp_path / "ratings.jsonl",
    }
    monkeypatch.setattr(skill_autofix, "COMMANDS_DIR", commands)
    monkeypatch.setattr(skill_autofix, "PI_SKILLS_DIR", pi_skills)
    monkeypatch.setattr(skill_autofix, "STATE_DIR", state)
    monkeypatch.setattr(skill_autofix, "LEDGER_FILE", paths["ledger"])
    monkeypatch.setattr(skill_autofix, "SNAP_REPO", paths["repo"])
    monkeypatch.setattr(skill_autofix, "DIAG_DIR", diagnostics)
    monkeypatch.setattr(skill_autofix, "RATINGS_FILE", paths["ratings"])
    return paths


def _active_edit(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "skill": "deploy",
        "pattern": "tool_misuse",
        "surface": "claude",
        "status": "active",
        "applied": "2026-09-01",
        "baseline_fail_rate": 0.5,
        "commit_before": "a" * 40,
    }
    value.update(overrides)
    return value


def _post_entries(*, rating: float = 2, count: int = skill_autofix.MIN_AFTER) -> list[Entry]:
    return [
        Entry(timestamp=f"2026-09-{i + 2:02d}T12:00:00Z", rating=rating)
        for i in range(count)
    ]


def test_normalize_ledger_quarantines_malformed_records() -> None:
    valid = _active_edit()
    malformed = {"skill": "deploy", "pattern": "tool_misuse"}
    normalized = skill_autofix.normalize_ledger(
        {
            "edits": [valid, malformed, "bad"],
            "invalid_edits": [{"old": True}, 3],
            "log": [{"date": "2026-09-06"}, "bad"],
        }
    )

    assert normalized["edits"] == [valid]
    assert normalized["invalid_edits"] == [malformed, {"old": True}]
    assert normalized["log"] == [{"date": "2026-09-06"}]
    assert skill_autofix.normalize_ledger([]) == {
        "edits": [],
        "invalid_edits": [],
        "log": [],
    }


def test_load_and_save_ledger_are_tolerant_and_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    paths["ledger"].write_text("[]\n")
    assert skill_autofix.load_ledger()["edits"] == []

    ledger = {"edits": [_active_edit()], "log": [], "invalid_edits": []}
    skill_autofix.save_ledger(ledger)
    saved = json.loads(paths["ledger"].read_text())
    assert saved["edits"][0]["skill"] == "deploy"
    assert not lock_path_for(paths["ledger"]).exists()


def test_skill_resolution_and_snapshot_names_reject_unsafe_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Deploy\n")
    assert skill_autofix.skill_file_with_surface("deploy") == (live, "claude")
    assert skill_autofix.skill_file("../escape") is None
    with pytest.raises(ValueError, match="invalid skill name"):
        skill_autofix._snap_name("../escape", "claude")


def test_snapshot_round_trip_and_missing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Deploy\n")
    skill_autofix.ensure_repo()
    commit = skill_autofix.snapshot("deploy", live, "before")
    assert skill_autofix.COMMIT_RE.fullmatch(commit)
    assert skill_autofix.content_at("deploy", commit) == "# Deploy\n"
    assert skill_autofix.content_at("deploy", "not-a-commit") is None
    assert skill_autofix.content_at("missing", commit) is None


def test_git_failures_raise_with_bounded_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, stdout="", stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="exit 2: boom"):
        skill_autofix._git("status")


def test_apply_guardrail_success_and_validation_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Deploy\n")
    skill_autofix.ensure_repo()

    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="## Auto-learned guardrails\n- Verify deployment output.",
        today="2026-09-06",
        baseline_rate=0.75,
        baseline_n=4,
    )
    assert record is not None and record["status"] == "active"
    assert "APPLIED /deploy" in outcome
    assert skill_autofix.START in live.read_text()
    assert skill_autofix.content_at("deploy", str(record["commit_before"])) == "# Deploy\n"

    live.write_text("# Deploy\n")
    monkeypatch.setattr(skill_autofix, "run_validation", lambda *_args, **_kwargs: (True, "ok"))
    record, _ = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="## Guardrails\n-- @validation: pytest -q\n- Verify output.",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is not None and record["status"] == "active"
    assert record["validation"] == "pass: ok"


def test_apply_guardrail_validation_failure_restores_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Original\n")
    skill_autofix.ensure_repo()
    monkeypatch.setattr(skill_autofix, "run_validation", lambda *_args, **_kwargs: (False, "tests failed"))

    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="## Guardrails\n-- @validation: pytest -q\n- Verify output.",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is not None and record["status"] == "validation-failed"
    assert record["validation"] == "fail: tests failed"
    assert record["commit_after"] == record["rollback_commit"]
    assert live.read_text() == "# Original\n"
    assert "REVERTED /deploy" in outcome


def test_apply_guardrail_snapshot_failures_restore_or_record_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Original\n")
    calls = 0

    def failed_after_snapshot(*_args: object, **_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "a" * 40
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(skill_autofix, "snapshot", failed_after_snapshot)
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="- Verify output.",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is not None and record["status"] == "apply-audit-failed"
    assert live.read_text() == "# Original\n"
    assert "live file restored" in outcome

    monkeypatch.setattr(skill_autofix, "snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("before failed")))
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="- Verify output.",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is None
    assert "pre-edit snapshot failed" in outcome


def test_apply_guardrail_records_restore_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Original\n")
    snapshot_calls = 0

    def snapshot_then_fail(*_args: object, **_kwargs: object) -> str:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 1:
            return "a" * 40
        raise RuntimeError("snapshot failed")

    real_atomic = skill_autofix.atomic_write_text
    write_calls = 0

    def fail_restore(path: Path, text: str) -> None:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            raise OSError("restore denied")
        real_atomic(path, text)

    monkeypatch.setattr(skill_autofix, "snapshot", snapshot_then_fail)
    monkeypatch.setattr(skill_autofix, "atomic_write_text", fail_restore)
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="- Verify output.",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is not None and record["status"] == "rollback-failed"
    assert record["rollback_error"] == "restore denied"
    assert "ROLLBACK FAILED" in outcome


def test_evaluate_active_never_claims_unavailable_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    entries = _post_entries()

    ledger = {"edits": [_active_edit(commit_before="bad")], "log": []}
    changes: list[str] = []
    skill_autofix.evaluate_active(ledger, entries, "2026-09-06", changes, False)
    assert ledger["edits"][0]["status"] == "rollback-failed"
    assert "live skill file is unavailable" in ledger["edits"][0]["rollback_error"]

    live = tmp_path / "commands" / "deploy.md"
    live.write_text("# Modified\n")
    ledger = {"edits": [_active_edit(commit_before="bad")], "log": []}
    changes = []
    skill_autofix.evaluate_active(ledger, entries, "2026-09-06", changes, False)
    assert ledger["edits"][0]["status"] == "rollback-failed"
    assert live.read_text() == "# Modified\n"
    assert "snapshot is unavailable" in ledger["edits"][0]["rollback_error"]


def test_evaluate_active_dry_run_success_and_audit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Modified\n")
    entries = _post_entries()
    monkeypatch.setattr(skill_autofix, "content_at", lambda *_args, **_kwargs: "# Original\n")

    ledger = {"edits": [_active_edit()], "log": []}
    changes: list[str] = []
    skill_autofix.evaluate_active(ledger, entries, "2026-09-06", changes, True)
    assert ledger["edits"][0]["status"] == "active"
    assert live.read_text() == "# Modified\n"
    assert changes[0].startswith("WOULD REVERT")

    monkeypatch.setattr(skill_autofix, "snapshot", lambda *_args, **_kwargs: "b" * 40)
    ledger = {"edits": [_active_edit()], "log": []}
    changes = []
    skill_autofix.evaluate_active(ledger, entries, "2026-09-06", changes, False)
    assert ledger["edits"][0]["status"] == "reverted"
    assert ledger["edits"][0]["rollback_commit"] == "b" * 40
    assert live.read_text() == "# Original\n"

    live.write_text("# Modified again\n")
    monkeypatch.setattr(skill_autofix, "snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit down")))
    ledger = {"edits": [_active_edit()], "log": []}
    changes = []
    skill_autofix.evaluate_active(ledger, entries, "2026-09-06", changes, False)
    assert ledger["edits"][0]["status"] == "reverted-audit-failed"
    assert live.read_text() == "# Original\n"


def test_evaluate_active_invalid_and_confirmed_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    ledger = {"edits": [_active_edit(baseline_fail_rate="bad")], "log": []}
    changes: list[str] = []
    skill_autofix.evaluate_active(ledger, _post_entries(), "2026-09-06", changes, False)
    assert ledger["edits"][0]["status"] == "invalid"

    ledger = {"edits": [_active_edit(baseline_fail_rate=1.0)], "log": []}
    changes = []
    skill_autofix.evaluate_active(
        ledger, _post_entries(rating=10), "2026-09-06", changes, False
    )
    assert ledger["edits"][0]["status"] == "confirmed"
    assert changes[0].startswith("confirmed /deploy")


def test_suite_gate_fails_closed_and_accepts_valid_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    fake_module = tmp_path / "module" / "skill_autofix.py"
    fake_module.parent.mkdir()
    monkeypatch.setattr(skill_autofix, "__file__", str(fake_module))
    allowed, message = skill_autofix.suite_gate_allows_apply()
    assert allowed is False and "missing" in message

    suite = fake_module.parent / "held_out_suite.py"
    suite.write_text("# gate\n")
    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="pass", stderr=""),
    )
    allowed, message = skill_autofix.suite_gate_allows_apply()
    assert allowed is True and "no prior run" in message

    last = paths["state"] / "agent_rollouts_last.json"
    last.write_text("not json")
    allowed, message = skill_autofix.suite_gate_allows_apply()
    assert allowed is False and "invalid" in message

    last.write_text(json.dumps({"summary": {"skipped_all": True}, "gate": {}}))
    assert skill_autofix.suite_gate_allows_apply()[0] is True

    last.write_text(json.dumps({"summary": {"pass_rate": 0.5}, "gate": {}}))
    assert skill_autofix.suite_gate_allows_apply()[0] is False

    last.write_text(
        json.dumps(
            {
                "summary": {"pass_rate": 0.9, "n": 5},
                "gate": {"has_baseline": True, "gate_pass": False},
            }
        )
    )
    assert skill_autofix.suite_gate_allows_apply()[0] is False

    last.write_text(
        json.dumps(
            {
                "summary": {"pass_rate": 0.9, "n": 5},
                "gate": {"has_baseline": True, "gate_pass": True},
            }
        )
    )
    allowed, message = skill_autofix.suite_gate_allows_apply()
    assert allowed is True and "90.0%" in message


def test_suite_gate_handles_execution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    fake_module = tmp_path / "module" / "skill_autofix.py"
    fake_module.parent.mkdir()
    (fake_module.parent / "held_out_suite.py").write_text("# gate\n")
    monkeypatch.setattr(skill_autofix, "__file__", str(fake_module))
    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("gate", 120)),
    )
    allowed, message = skill_autofix.suite_gate_allows_apply()
    assert allowed is False and "unavailable" in message


def test_main_blocked_apply_persists_reverts_and_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(skill_autofix, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(skill_autofix, "ensure_repo", lambda: None)
    monkeypatch.setattr(skill_autofix, "suite_gate_allows_apply", lambda: (False, "red gate"))
    monkeypatch.setattr(skill_autofix, "propose_new", lambda *_args, **_kwargs: [])

    assert skill_autofix.main(["--apply", "--no-llm"]) == 2
    saved = json.loads(paths["ledger"].read_text())
    assert saved["log"][0]["changes"][0] == "BLOCKED new skill applies — red gate"
    assert not lock_path_for(paths["ledger"]).exists()


def test_main_dry_run_and_status_do_not_mutate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(skill_autofix, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(skill_autofix, "propose_new", lambda *_args, **_kwargs: [])

    assert skill_autofix.main(["--dry-run", "--no-llm"]) == 0
    assert not paths["ledger"].exists()
    assert "[dry-run]" in capsys.readouterr().out

    paths["ledger"].write_text(json.dumps({"edits": [], "log": []}))
    assert skill_autofix.main(["--status"]) == 0
    assert '"edits": []' in capsys.readouterr().out


def test_main_reports_mutation_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(skill_autofix, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(
        skill_autofix,
        "ensure_repo",
        lambda: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )
    assert skill_autofix.main(["--apply", "--force", "--no-llm"]) == 1
    assert "mutation cycle failed" in capsys.readouterr().err


def test_propose_new_applies_once_and_respects_cooldown_and_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Deploy\n")
    skill_autofix.ensure_repo()
    entries = _post_entries(count=3)
    monkeypatch.setattr(skill_autofix, "dominant_pattern", lambda _sessions: "tool_misuse")
    monkeypatch.setattr(skill_autofix, "generate_guardrail", lambda *_args, **_kwargs: "- Verify output.")
    ledger: dict[str, object] = {"edits": [], "invalid_edits": [], "log": []}
    changes: list[str] = []

    candidates = skill_autofix.propose_new(
        ledger, entries, "2026-09-06", changes, use_llm=True, dry_run=False
    )
    assert candidates and ledger["edits"][0]["status"] == "active"  # type: ignore[index]
    assert changes[-1].startswith("APPLIED /deploy")

    changes = []
    skill_autofix.propose_new(
        ledger, entries, "2026-09-06", changes, use_llm=True, dry_run=False
    )
    assert changes == []

    ledger = {
        "edits": [
            {
                "skill": "deploy",
                "pattern": "tool_misuse",
                "status": "reverted",
            }
        ],
        "log": [],
    }
    live.write_text("# Deploy\n")
    changes = []
    skill_autofix.propose_new(
        ledger, entries, "2026-09-06", changes, use_llm=True, dry_run=False
    )
    assert changes == ["skip /deploy — pattern 'tool_misuse' already tried+reverted (cooldown)"]

    general = paths["commands"] / "general-session.md"
    general.write_text(
        skill_autofix.upsert_section(
            "# General\n",
            "\n".join(f"- guardrail {index}" for index in range(skill_autofix.GENERAL_SESSION_MAX_BULLETS)),
        )
    )
    general_entries = [Entry(skill="general-session") for _ in range(3)]
    changes = []
    skill_autofix.propose_new(
        {"edits": [], "log": []},
        general_entries,
        "2026-09-06",
        changes,
        use_llm=True,
        dry_run=False,
    )
    assert changes and "dump-bin cap reached" in changes[0]


def test_report_counts_only_known_lifecycle_states() -> None:
    report = skill_autofix.build_report(
        {
            "edits": [
                _active_edit(),
                _active_edit(skill="other", status="confirmed"),
                _active_edit(skill="third", status="reverted"),
                _active_edit(skill="fourth", status="rollback-failed"),
            ]
        },
        ["one change"],
        ["/deploy"],
        "2026-09-06",
    )
    assert "Active edits: 1 | Confirmed: 1 | Reverted: 1" in report
    assert "one change" in report
    assert "/deploy" in report


def test_repo_upsert_and_validation_parser_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    skill_autofix.ensure_repo()
    skill_autofix.ensure_repo()
    original = f"before\n{skill_autofix.START}\nold\n{skill_autofix.END}\nafter\n"
    updated = skill_autofix.upsert_section(original, "new")
    assert updated.count(skill_autofix.START) == 1
    assert "old" not in updated and "new" in updated and "after" in updated

    cases = {
        "   ": "empty",
        "'unterminated": "invalid validation command",
        "pytest " + " ".join("x" for _ in range(skill_autofix.MAX_VALIDATION_ARGS)): "exceeds",
        "/usr/bin/pytest -q": "not allowed",
        "python script.py": "Python validation",
        "cargo build": "cargo validation",
        "go build": "go validation",
    }
    for command, expected in cases.items():
        argv, error = skill_autofix._validation_argv(command)
        assert argv is None and expected in error


def test_run_validation_missing_failure_success_and_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skill_autofix.shutil, "which", lambda _name: None)
    assert skill_autofix.run_validation("pytest -q", tmp_path) == (
        False,
        "validation executable not found: pytest",
    )

    monkeypatch.setattr(skill_autofix.shutil, "which", lambda _name: "/bin/pytest")
    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout="x" * 600, stderr="bad"),
    )
    ok, note = skill_autofix.run_validation("pytest -q", tmp_path)
    assert ok is False and len(note) == 500 and note.endswith("bad")

    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="ok", stderr=""),
    )
    assert skill_autofix.run_validation("pytest -q", tmp_path) == (True, "ok")
    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    assert skill_autofix.run_validation("pytest -q", tmp_path) == (False, "spawn failed")


def test_skill_path_resolution_covers_pi_symlink_directory_and_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    package = paths["pi_skills"] / "review" / "SKILL.md"
    package.parent.mkdir()
    package.write_text("# Review\n")
    assert skill_autofix.skill_file_with_surface("review") == (package, "pi")
    package.unlink()
    flat = paths["pi_skills"] / "review.md"
    flat.write_text("# Review\n")
    assert skill_autofix.skill_file_with_surface("review") == (flat, "pi")

    command = paths["commands"] / "review.md"
    command.mkdir()
    assert skill_autofix.skill_file_with_surface("review") == (flat, "pi")
    command.rmdir()
    command.symlink_to(flat)
    assert skill_autofix.skill_file_with_surface("review") == (flat, "pi")

    original_is_file = Path.is_file

    def unavailable(path: Path) -> bool:
        if path == flat:
            raise OSError("filesystem unavailable")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", unavailable)
    assert skill_autofix.skill_file_with_surface("review") is None


def test_statistics_and_prompt_context_tolerate_malformed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        Entry(timestamp="2026-09-01", skill="other", skill_candidates=["DEPLOY", 3]),
        Entry(timestamp="2026-09-03", skill="deploy", rating=10, sentiment_summary="worked"),
        Entry(timestamp="2026-09-04", skill="deploy", rating=float("nan")),
        Entry(timestamp="2026-09-05", skill="deploy", rating=True),
    ]
    sessions = skill_autofix.skill_sessions(entries, "deploy", since="2026-09-02")
    assert len(sessions) == 3
    assert skill_autofix.skill_sessions(entries, "missing") == []
    assert skill_autofix.count_guardrail_bullets("plain") == 0
    assert skill_autofix.fail_rate([Entry(rating=float("nan")), Entry(rating=True)]) == (0.0, 0)

    monkeypatch.setattr(
        skill_autofix,
        "classify_entry",
        lambda entry: ["tool_misuse", "other", 3] if entry.rating <= skill_autofix.LOW else [],
    )
    assert skill_autofix.dominant_pattern([Entry(rating=2), Entry(rating=10)]) == "tool_misuse"
    assert skill_autofix.dominant_pattern([Entry(rating=10)]) == "general_quality"
    assert "worked" in skill_autofix._passing_behaviors(entries, "deploy")
    assert skill_autofix._passing_behaviors([Entry(rating=10, sentiment_summary="", response_preview="")], "deploy").startswith("(no summaries")
    assert skill_autofix._prior_failed_edits({"edits": "bad"}, "deploy") == "(none)"
    prior = skill_autofix._prior_failed_edits(
        {"edits": ["bad", {"skill": "deploy", "status": "reverted", "pattern": "x"}]},
        "deploy",
    )
    assert "pattern=x" in prior

    monkeypatch.setattr(skill_autofix, "call_llm", lambda *_args, **_kwargs: "")
    assert skill_autofix.generate_guardrail("deploy", "x", [], "2026-09-06") is None
    monkeypatch.setattr(skill_autofix, "call_llm", lambda *_args, **_kwargs: "Use a checklist")
    assert "Use a checklist" in str(
        skill_autofix.generate_guardrail("deploy", "x", [], "2026-09-06")
    )


@pytest.mark.parametrize("value", [None, True, [], "bad", -0.1, 1.1, float("inf")])
def test_normalized_rate_rejects_invalid_values(value: object) -> None:
    assert skill_autofix._normalized_rate(value) is None


def test_remaining_evaluate_and_apply_failure_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Original\n")

    ledger = {"edits": [_active_edit()], "log": []}
    changes: list[str] = []
    skill_autofix.evaluate_active(
        ledger,
        _post_entries(count=skill_autofix.MIN_AFTER - 1),
        "2026-09-06",
        changes,
        False,
    )
    assert ledger["edits"][0]["status"] == "active" and changes == []

    monkeypatch.setattr(skill_autofix, "content_at", lambda *_args, **_kwargs: "# Original\n")
    real_atomic_write = skill_autofix.atomic_write_text
    monkeypatch.setattr(
        skill_autofix,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("restore denied")),
    )
    ledger = {"edits": [_active_edit()], "log": []}
    changes = []
    skill_autofix.evaluate_active(ledger, _post_entries(), "2026-09-06", changes, False)
    assert ledger["edits"][0]["status"] == "rollback-failed"
    assert "restore failed" in str(ledger["edits"][0]["rollback_error"])

    monkeypatch.setattr(skill_autofix, "atomic_write_text", real_atomic_write)
    monkeypatch.setattr(skill_autofix, "snapshot", lambda *_args, **_kwargs: "a" * 40)
    monkeypatch.setattr(skill_autofix, "validate_skill_content", lambda _text: False)
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="- invalid",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is None and "format validation" in outcome


def test_validation_restore_audit_and_outer_mutation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Original\n")
    monkeypatch.setattr(skill_autofix, "run_validation", lambda *_args, **_kwargs: (False, "bad"))
    snapshot_calls = 0

    def audit_failure(*_args: object, **_kwargs: object) -> str:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls < 3:
            return "a" * 40
        raise RuntimeError("rollback audit failed")

    monkeypatch.setattr(skill_autofix, "snapshot", audit_failure)
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="-- @validation: pytest -q\n- verify",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is not None and record["status"] == "validation-failed-audit-failed"
    assert "rollback snapshot failed" in outcome

    monkeypatch.setattr(
        skill_autofix,
        "exclusive_lock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("busy")),
    )
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="- verify",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is None and "mutation unavailable" in outcome


def test_proposal_skip_flag_and_deferred_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    entries = _post_entries(count=3)
    changes: list[str] = []
    assert skill_autofix.propose_new(
        {"edits": [], "log": []}, entries, "2026-09-06", changes, True, False
    ) == []

    live = paths["commands"] / "deploy.md"
    live.write_text("# Deploy\n")
    monkeypatch.setattr(skill_autofix, "dominant_pattern", lambda _sessions: "tool_misuse")
    candidates = skill_autofix.propose_new(
        {"edits": [], "log": []}, entries, "2026-09-06", [], False, False
    )
    assert candidates and "deploy" in candidates[0]

    monkeypatch.setattr(skill_autofix, "generate_guardrail", lambda *_args, **_kwargs: None)
    changes = []
    skill_autofix.propose_new(
        {"edits": [], "log": []}, entries, "2026-09-06", changes, True, False
    )
    assert "LLM unavailable" in changes[0]

    weak_entries = [Entry(rating=2)]
    assert skill_autofix.propose_new(
        {"edits": [], "log": []}, weak_entries, "2026-09-06", [], True, False
    ) == []


def test_suite_gate_remaining_fail_closed_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    fake_module = tmp_path / "module" / "skill_autofix.py"
    fake_module.parent.mkdir()
    (fake_module.parent / "held_out_suite.py").write_text("# gate\n")
    monkeypatch.setattr(skill_autofix, "__file__", str(fake_module))
    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, stdout="bad", stderr="gate"),
    )
    allowed, message = skill_autofix.suite_gate_allows_apply()
    assert allowed is False and "rc=2" in message

    monkeypatch.setattr(
        skill_autofix.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="ok", stderr=""),
    )
    last = paths["state"] / "agent_rollouts_last.json"
    last.write_text(json.dumps({"summary": [], "gate": {}}))
    assert skill_autofix.suite_gate_allows_apply()[0] is False
    last.write_text(json.dumps({"summary": {"pass_rate": "bad"}, "gate": {}}))
    assert skill_autofix.suite_gate_allows_apply()[0] is False


def test_empty_apply_cycle_and_empty_report_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(skill_autofix, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(skill_autofix, "ensure_repo", lambda: None)
    monkeypatch.setattr(skill_autofix, "propose_new", lambda *_args, **_kwargs: [])
    assert skill_autofix.main(["--apply", "--force", "--no-llm"]) == 0
    saved = json.loads(paths["ledger"].read_text())
    assert saved["log"] == []
    assert "forced" in capsys.readouterr().out
    report = skill_autofix.build_report({"edits": []}, [], [], "2026-09-06")
    assert "No qualifying skills" in report


def test_evaluate_active_skips_invalid_rows_and_keeps_no_baseline_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    invalid = {"skill": "deploy", "status": "active"}
    inactive = _active_edit(status="confirmed")
    no_baseline = _active_edit(baseline_fail_rate=0.0)
    ledger = {"edits": [invalid, inactive, no_baseline], "log": []}
    changes: list[str] = []
    skill_autofix.evaluate_active(
        ledger,
        _post_entries(rating=10),
        "2026-09-06",
        changes,
        False,
    )
    assert invalid["status"] == "active"
    assert inactive["status"] == "confirmed"
    assert no_baseline["status"] == "active"
    assert no_baseline["verdict"] == "no-baseline"
    assert changes == []


def test_apply_guardrail_records_validation_restore_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live = paths["commands"] / "deploy.md"
    live.write_text("# Original\n")
    monkeypatch.setattr(skill_autofix, "snapshot", lambda *_args, **_kwargs: "a" * 40)
    monkeypatch.setattr(skill_autofix, "run_validation", lambda *_args, **_kwargs: (False, "bad"))
    real_atomic = skill_autofix.atomic_write_text
    live_writes = 0

    def fail_second_live_write(path: Path, text: str) -> None:
        nonlocal live_writes
        if path == live:
            live_writes += 1
            if live_writes == 2:
                raise OSError("restore denied")
        real_atomic(path, text)

    monkeypatch.setattr(skill_autofix, "atomic_write_text", fail_second_live_write)
    record, outcome = skill_autofix._apply_guardrail(
        skill="deploy",
        pattern="tool_misuse",
        surface="claude",
        live=live,
        block="-- @validation: pytest -q\n- verify",
        today="2026-09-06",
        baseline_rate=0.5,
        baseline_n=5,
    )
    assert record is not None and record["status"] == "rollback-failed"
    assert "validation failed" in outcome


def test_general_session_read_failure_does_not_fake_cap_and_none_apply_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    general = paths["commands"] / "general-session.md"
    general.write_text("# General\n")
    entries = [Entry(skill="general-session") for _ in range(skill_autofix.MIN_LOW)]
    monkeypatch.setattr(skill_autofix, "dominant_pattern", lambda _sessions: "tool_misuse")
    original_read = Path.read_text

    def unreadable(path: Path, *args: object, **kwargs: object) -> str:
        if path == general:
            raise OSError("denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    candidates = skill_autofix.propose_new(
        {"edits": [], "log": []}, entries, "2026-09-06", [], False, False
    )
    assert candidates and "general-session" in candidates[0]
    monkeypatch.setattr(Path, "read_text", original_read)

    deploy = paths["commands"] / "deploy.md"
    deploy.write_text("# Deploy\n")
    deploy_entries = _post_entries(count=skill_autofix.MIN_LOW)
    monkeypatch.setattr(skill_autofix, "generate_guardrail", lambda *_args, **_kwargs: "- guard")
    monkeypatch.setattr(
        skill_autofix,
        "_apply_guardrail",
        lambda **_kwargs: (None, "deferred /deploy — unavailable"),
    )
    ledger: dict[str, object] = {"edits": [], "log": []}
    changes: list[str] = []
    skill_autofix.propose_new(
        ledger, deploy_entries, "2026-09-06", changes, True, False
    )
    assert ledger["edits"] == []
    assert changes == ["deferred /deploy — unavailable"]
