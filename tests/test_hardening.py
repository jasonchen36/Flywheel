from __future__ import annotations

import hashlib
import json
import os
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

    config = harness / "MEMORY" / "STATE" / "enforcement_config.json"
    config.write_text('{"enabled":false,"overrides":{"x":"off"}}\n')
    _, second = run_installer(tmp_path, force_tar=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(config.read_text())["enabled"] is False


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
