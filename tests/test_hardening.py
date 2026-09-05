from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

from ace_playbook import bullet_id
from flush_graphiti_pending import GraphitiMCPHttp, normalize_mcp_url
from harness_healthcheck import load_json_object
from skill_autofix import (
    END,
    START,
    _validation_argv,
    run_validation,
    validate_skill_content,
)


def run_installer(tmp_path: Path, *, force_tar: bool = False) -> tuple[Path, subprocess.CompletedProcess[str]]:
    home = tmp_path / "home"
    harness = tmp_path / "harness"
    home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "HARNESS_HOME": str(harness),
        "HARNESS_PI_EXTENSIONS": str(tmp_path / "missing-parent" / "extensions"),
        "HARNESS_SKIP_BUN_INSTALL": "1",
    }
    if force_tar:
        env["HARNESS_FORCE_TAR_COPY"] = "1"
    proc = subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return harness, proc


def test_validate_skill_content_accepts_valid_bounded_section():
    content = f"# Skill\n\n{START}\n- Verify output.\n{END}\n"
    assert validate_skill_content(content) is True


@pytest.mark.parametrize(
    "content",
    [
        "# no markers\n",
        f"{START}\nmissing end\n",
        f"{END}\n{START}\n",
        f"{START}\n```python\nprint('open fence')\n{END}\n",
    ],
)
def test_validate_skill_content_rejects_invalid_structure(content: str):
    assert validate_skill_content(content) is False


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q; touch /tmp/flywheel-pwned",
        "pytest -q | tee output.txt",
        "python3 -c 'print(1)'",
        "/bin/pytest -q",
        "env TOKEN=secret pytest -q",
        "pytest $(touch /tmp/flywheel-pwned)",
        "npm install",
        "npm run postinstall",
        "cargo run",
        "go env",
    ],
)
def test_validation_contract_rejects_unsafe_commands(command: str):
    argv, error = _validation_argv(command)
    assert argv is None
    assert error


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -q", ["pytest", "-q"]),
        ("python3 -m pytest tests", ["python3", "-m", "pytest", "tests"]),
        ("ruff check .", ["ruff", "check", "."]),
        ("npm test", ["npm", "test"]),
        ("pnpm run lint", ["pnpm", "run", "lint"]),
        ("cargo check", ["cargo", "check"]),
        ("go test ./...", ["go", "test", "./..."]),
    ],
)
def test_validation_contract_accepts_bounded_commands(command: str, expected: list[str]):
    argv, error = _validation_argv(command)
    assert argv == expected
    assert error == ""


def test_run_validation_never_executes_shell_metacharacters(tmp_path: Path):
    marker = tmp_path / "must-not-exist"
    ok, note = run_validation(f"pytest -q; touch {marker}", tmp_path)
    assert ok is False
    assert "shell control" in note
    assert not marker.exists()


def test_run_validation_executes_allowed_program_without_shell(tmp_path: Path):
    ok, note = run_validation("pytest --version", tmp_path)
    assert ok is True
    assert "pytest" in note.lower()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000/mcp"),
        ("https://graph.example.com/mcp/", "https://graph.example.com/mcp"),
        ("https://graph.example.com/mcp?tenant=a", "https://graph.example.com/mcp?tenant=a"),
    ],
)
def test_normalize_mcp_url_accepts_http_endpoints(url: str, expected: str):
    assert normalize_mcp_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://graph.example.com/mcp",
        "https:///mcp",
        "https://user:secret@graph.example.com/mcp",
        "https://graph.example.com/admin",
        "https://graph.example.com/mcp#fragment",
    ],
)
def test_normalize_mcp_url_rejects_unsafe_or_ambiguous_endpoints(url: str):
    with pytest.raises(ValueError):
        normalize_mcp_url(url)


def test_graphiti_client_rejects_nonpositive_timeout():
    with pytest.raises(ValueError, match="positive"):
        GraphitiMCPHttp("http://localhost:8000/mcp", timeout=0)


def test_content_ids_use_sha256():
    expected = hashlib.sha256(b"pattern|rule").hexdigest()[:10]
    assert bullet_id("pattern", "rule") == f"b_pattern_{expected}"


def test_load_json_object_reports_invalid_json_and_shape(tmp_path: Path):
    state = tmp_path / "state.json"
    state.write_text("not-json")
    data, error = load_json_object(state)
    assert data == {}
    assert error and "invalid JSON" in error

    state.write_text("[]")
    data, error = load_json_object(state)
    assert data == {}
    assert error and "expected a JSON object" in error


def test_installer_tar_fallback_and_idempotency(tmp_path: Path):
    harness, first = run_installer(tmp_path, force_tar=True)
    assert first.returncode == 0, first.stderr
    learning = harness / "MEMORY" / "LEARNING"
    assert (learning / "self_improve.py").is_file()
    assert not (learning / "__pycache__").exists()
    assert os.access(harness / "hooks" / "harness-session-end.sh", os.X_OK)
    assert (tmp_path / "missing-parent" / "extensions" / "pai-enforcement-gate.ts").is_file()

    config = harness / "MEMORY" / "STATE" / "enforcement_config.json"
    config.write_text('{"enabled":false,"overrides":{"x":"off"}}\n')
    _, second = run_installer(tmp_path, force_tar=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(config.read_text())["enabled"] is False
    _, third = run_installer(tmp_path, force_tar=True)
    assert third.returncode == 0, third.stderr
    backups = list((harness / "hooks").glob("RatingCapture.hook.ts.bak.*"))
    assert len(backups) == 2
    assert len({path.name for path in backups}) == 2
    assert not list((harness / "MEMORY" / "STATE").glob("*.tmp.*"))


def test_installer_rejects_python_below_supported_floor(tmp_path: Path):
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    home.mkdir()
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = \"--version\" ]; then echo 'Python 3.9.99'; fi\n"
        "exit 1\n"
    )
    fake_python.chmod(0o755)
    proc = subprocess.run(
        ["bash", str(ROOT / "install.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "HOME": str(home),
            "HARNESS_HOME": str(tmp_path / "harness"),
            "HARNESS_SKIP_BUN_INSTALL": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "Python 3.10 or newer is required (found Python 3.9.99)" in proc.stderr


def test_custom_harness_home_propagates_to_runtime_modules(tmp_path: Path):
    harness, install = run_installer(tmp_path)
    assert install.returncode == 0, install.stderr
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(harness / 'MEMORY' / 'LEARNING')!r})\n"
        "import ace_playbook, evals, self_improve, skill_autofix\n"
        "print(json.dumps({\n"
        "  'lessons': str(ace_playbook.LESSONS_DIR),\n"
        "  'ratings': str(self_improve.RATINGS_FILE),\n"
        "  'evals': str(evals.SIGNALS_DIR),\n"
        "  'commands': str(skill_autofix.COMMANDS_DIR),\n"
        "}))\n"
    )
    proc = subprocess.run(
        [sys.executable, str(probe)],
        env={**os.environ, "HARNESS_HOME": str(harness), "HOME": str(tmp_path / "home")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    paths = json.loads(proc.stdout)
    assert paths == {
        "lessons": str(harness / "MEMORY" / "lessons"),
        "ratings": str(harness / "MEMORY" / "LEARNING" / "SIGNALS" / "ratings.jsonl"),
        "evals": str(harness / "MEMORY" / "LEARNING" / "SIGNALS"),
        "commands": str(harness / "commands"),
    }


def test_healthcheck_fails_cleanly_for_corrupt_state(tmp_path: Path):
    harness, install = run_installer(tmp_path)
    assert install.returncode == 0, install.stderr
    (harness / "MEMORY" / "STATE" / "effectiveness_scores.json").write_text("[]")
    proc = subprocess.run(
        [sys.executable, str(harness / "MEMORY" / "LEARNING" / "harness_healthcheck.py"), "--json"],
        env={**os.environ, "HARNESS_HOME": str(harness), "HOME": str(tmp_path / "home")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert any("expected a JSON object" in error for error in report["errors"])


def test_changelog_observes_lesson_changes(tmp_path: Path):
    harness, install = run_installer(tmp_path)
    assert install.returncode == 0, install.stderr
    env = {**os.environ, "HARNESS_HOME": str(harness), "HOME": str(tmp_path / "home")}
    script = harness / "MEMORY" / "LEARNING" / "harness_changelog.py"

    first = subprocess.run([sys.executable, str(script)], env=env, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    lesson = harness / "MEMORY" / "lessons" / "lesson_autogen_test.md"
    lesson.write_text("---\npattern: test\n---\nVerify the result.\n")
    second = subprocess.run([sys.executable, str(script)], env=env, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    changelog = (harness / "MEMORY" / "STATE" / "harness_changelog.md").read_text()
    assert "lessons/lesson_autogen_test.md" in changelog


def test_healthcheck_rejects_malformed_enforcement_config(tmp_path: Path):
    harness, install = run_installer(tmp_path)
    assert install.returncode == 0, install.stderr
    config = harness / "MEMORY" / "STATE" / "enforcement_config.json"
    config.write_text(
        json.dumps(
            {
                "enabled": "yes",
                "overrides": {
                    "blind_retry": "explode",
                    "typo_pattern": "block",
                },
            }
        )
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(harness / "MEMORY" / "LEARNING" / "harness_healthcheck.py"),
            "--json",
        ],
        env={**os.environ, "HARNESS_HOME": str(harness), "HOME": str(tmp_path / "home")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    enforcement = report["checks"]["enforcement"]
    assert enforcement["valid"] is False
    assert enforcement["enabled"] is True
    assert enforcement["errors"] == [
        "enabled must be a boolean",
        "invalid mode for blind_retry: 'explode'; expected off, warn, or block",
        "unknown enforcement override: 'typo_pattern'",
    ]
    assert any("invalid enforcement_config.json" in error for error in report["errors"])


def test_healthcheck_reports_review_queue_state_machine_problems(tmp_path: Path):
    harness, install = run_installer(tmp_path)
    assert install.returncode == 0, install.stderr
    review_file = harness / "MEMORY" / "LEARNING" / "SIGNALS" / "pending_human_review.jsonl"
    review_file.write_text(
        "\n".join(
            [
                json.dumps({"pattern": "failed", "status": "action_failed"}),
                json.dumps({"pattern": "running", "status": "processing"}),
                json.dumps({"pattern": "mystery", "status": "unknown"}),
                "not-json",
            ]
        )
        + "\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(harness / "MEMORY" / "LEARNING" / "harness_healthcheck.py"),
            "--json",
        ],
        env={**os.environ, "HARNESS_HOME": str(harness), "HOME": str(tmp_path / "home")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    review = report["checks"]["review_queue"]
    assert review["counts"] == {"action_failed": 1, "processing": 1, "unknown": 1}
    assert review["invalid_lines"] == [4]
    assert review["failed_patterns"] == ["failed"]
    assert review["processing_patterns"] == ["running"]
    assert review["unknown_statuses"] == ["unknown"]
    assert any("--retry-failed" in warning for warning in report["warnings"])
    assert any("still processing" in warning for warning in report["warnings"])


def _run_enforcement_hook(harness: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    hooks = harness / "hooks"
    if not hooks.exists():
        shutil.copytree(ROOT / "hooks", hooks)
    parser = harness / "PAI" / "Tools" / "TranscriptParser.ts"
    parser.parent.mkdir(parents=True, exist_ok=True)
    parser.write_text(
        "export interface ParsedTranscript { messages: unknown[]; lastMessage: string; }\n"
        "export function parseTranscript(_path: string): ParsedTranscript {\n"
        "  return { messages: [], lastMessage: '' };\n"
        "}\n"
    )
    return subprocess.run(
        ["bun", str(hooks / "EnforcementGate.hook.ts")],
        env={**os.environ, "HARNESS_HOME": str(harness), "HOME": str(harness.parent)},
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def test_enforcement_hook_ignores_invalid_modes_fail_safely(tmp_path: Path):
    harness = tmp_path / "harness"
    config = harness / "MEMORY" / "STATE" / "enforcement_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": "yes",
                "overrides": {
                    "unverified_completion": "explode",
                    "claim_evidence": "off",
                    "typo_pattern": "off",
                },
            }
        )
    )
    proc = _run_enforcement_hook(
        harness,
        {
            "hook_event_name": "Stop",
            "session_id": "test",
            "last_assistant_message": "Done.",
        },
    )
    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assert output["decision"] == "block"
    assert "unverified_completion" in output["reason"]


def test_enforcement_hook_honors_valid_disabled_config(tmp_path: Path):
    harness = tmp_path / "harness"
    config = harness / "MEMORY" / "STATE" / "enforcement_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"enabled": False, "overrides": {}}))
    proc = _run_enforcement_hook(
        harness,
        {"hook_event_name": "Stop", "last_assistant_message": "Done."},
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_enforcement_hook_does_not_overwrite_malformed_existing_config(tmp_path: Path):
    harness = tmp_path / "harness"
    config = harness / "MEMORY" / "STATE" / "enforcement_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{")
    proc = _run_enforcement_hook(
        harness,
        {"hook_event_name": "Stop", "last_assistant_message": ""},
    )
    assert proc.returncode == 0
    assert config.read_text() == "{"
