import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
HOOKS = ROOT / "hooks"

sys.path.insert(0, str(LEARNING))


def test_call_llm_reasoning_token_floor(monkeypatch):
    import self_improve

    monkeypatch.setenv("PAI_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PAI_OPENAI_BASE_URL", "http://localhost:9999/v1")

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

    monkeypatch.setattr("openai.OpenAI", MockOpenAI)

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


def test_harness_healthcheck_execution():
    health_script = LEARNING / "harness_healthcheck.py"
    proc = subprocess.run(
        [sys.executable, str(health_script)],
        cwd=str(LEARNING),
        text=True,
        capture_output=True
    )
    assert proc.returncode == 0
    assert "OK: True" in proc.stdout
