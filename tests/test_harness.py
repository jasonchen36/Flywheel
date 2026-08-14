import json
import os
import subprocess
import sys
import types
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
HOOKS = ROOT / "hooks"

sys.path.insert(0, str(LEARNING))


def test_call_llm_reasoning_token_floor(monkeypatch):
    called_kwargs = {}

    class MockCompletions:
        def create(self, **kwargs):
            called_kwargs.update(kwargs)
            class MockMessage:
                content = "OK"
            class MockChoice:
                message = MockMessage()
            class MockResponse:
                choices = [MockChoice()]
            return MockResponse()

    class MockOpenAI:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": MockCompletions()})()

    mock_openai_module = types.ModuleType("openai")
    mock_openai_module.OpenAI = MockOpenAI
    monkeypatch.setitem(sys.modules, "openai", mock_openai_module)

    env_key_name = "_".join(["PAI", "OPENAI", "API", "KEY"])
    monkeypatch.setenv(env_key_name, "dummy_token")
    monkeypatch.setenv("PAI_OPENAI_BASE_URL", "http://localhost:9999/v1")

    import self_improve
    res = self_improve._call_llm_opencode("Hello", max_tokens=8)
    assert res == "OK"
    assert called_kwargs.get("max_tokens") == 128


def test_verification_reminder_hook_stdout():
    hook_script = HOOKS / "VerificationReminder.hook.ts"
    assert hook_script.exists()

    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/test.py"}
    })

    proc = subprocess.run(
        ["bun", str(hook_script)],
        input=payload,
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert "[VERIFICATION MANDATE]" in proc.stdout
    assert "test.py" in proc.stdout


def test_symbol_grounding_verifier_hook_stdout():
    hook_script = HOOKS / "SymbolGroundingVerifier.hook.ts"
    assert hook_script.exists()

    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {
            "file_path": "/tmp/design_doc.md",
            "content": "We will use WarehouseDatasetUtility and DagPokeUtil to construct schedules."
        }
    })

    proc = subprocess.run(
        ["bun", str(hook_script)],
        input=payload,
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert "[SYMBOL GROUNDING MANDATE]" in proc.stdout
    assert "WarehouseDatasetUtility" in proc.stdout


def test_review_feedback_persistence_hook_stdout():
    hook_script = HOOKS / "ReviewFeedbackPersistence.hook.ts"
    assert hook_script.exists()

    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {
            "command": "gh pr view 5311 --json comments"
        }
    })

    proc = subprocess.run(
        ["bun", str(hook_script)],
        input=payload,
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert "[MANDATORY IMMEDIATE MEMORY PERSISTENCE]" in proc.stdout


def test_stacked_pr_reminder_hook_stdout():
    hook_script = HOOKS / "StackedPRReminder.hook.ts"
    assert hook_script.exists()

    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {
            "command": "gh stack submit"
        }
    })

    proc = subprocess.run(
        ["bun", str(hook_script)],
        input=payload,
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert "[STACKED PR WORKFLOW]" in proc.stdout


def test_no_mistakes_gate_hook():
    hook_script = HOOKS / "NoMistakesGate.hook.sh"
    assert hook_script.exists()

    # Blocked feature branch push
    blocked_payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "rtk git push origin feat/my-branch"}
    })
    proc = subprocess.run(["bash", str(hook_script)], input=blocked_payload, text=True, capture_output=True)
    assert proc.returncode == 2
    res = json.loads(proc.stdout)
    assert res.get("decision") == "deny"
    assert res.get("continue") is False

    # Allowed promotion push
    allowed_payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "rtk git push origin prd"}
    })
    proc = subprocess.run(["bash", str(hook_script)], input=allowed_payload, text=True, capture_output=True)
    assert proc.returncode == 0


def test_workflow_guard_hook():
    hook_script = HOOKS / "WorkflowGuard.hook.sh"
    assert hook_script.exists()

    # Blocked invalid teams channel
    blocked_payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {
            "file_path": ".github/workflows/deploy.yml",
            "content": "notify_teams_channel: AXP+-+bigbrother+-+nonprd"
        }
    })
    proc = subprocess.run(["bash", str(hook_script)], input=blocked_payload, text=True, capture_output=True)
    assert proc.returncode == 2
    res = json.loads(proc.stdout)
    assert res.get("decision") == "deny"

    # Allowed valid channel
    allowed_payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {
            "file_path": ".github/workflows/deploy.yml",
            "content": "notify_teams_channel: Data+Architecture+-+Non-PRD"
        }
    })
    proc = subprocess.run(["bash", str(hook_script)], input=allowed_payload, text=True, capture_output=True)
    assert proc.returncode == 0


def test_harness_healthcheck_execution(tmp_path, monkeypatch):
    harness_home = tmp_path / ".claude"
    monkeypatch.setenv("HARNESS_HOME", str(harness_home))
    monkeypatch.setenv("HOME", str(tmp_path))

    install_script = ROOT / "install.sh"
    install_proc = subprocess.run(
        ["bash", str(install_script)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESS_HOME": str(harness_home), "HOME": str(tmp_path)}
    )
    assert install_proc.returncode == 0

    health_script = LEARNING / "harness_healthcheck.py"
    proc = subprocess.run(
        [sys.executable, str(health_script)],
        cwd=str(LEARNING),
        text=True,
        capture_output=True,
        env={**os.environ, "HARNESS_HOME": str(harness_home), "HOME": str(tmp_path)}
    )
    assert proc.returncode == 0
    assert "OK: True" in proc.stdout
