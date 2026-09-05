from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import agent_rollouts


SCENARIO = {
    "id": "example",
    "split": "in",
    "system_role": "You are careful.",
    "constraints": ["Do not post."],
    "user": "Review this.",
    "must_match_any": ["draft"],
}


def test_run_scenario_skips_disabled_empty_and_failed_providers(
    monkeypatch: pytest.MonkeyPatch,
):
    disabled = agent_rollouts.run_scenario(SCENARIO, "lessons", use_llm=False)
    assert disabled["skipped"] is True
    assert disabled["errors"] == ["--no-llm"]

    monkeypatch.setattr(agent_rollouts, "call_llm", lambda *_args, **_kwargs: "  ")
    empty = agent_rollouts.run_scenario(SCENARIO, "lessons", use_llm=True)
    assert empty["skipped"] is True
    assert empty["errors"] == ["LLM unavailable or empty response"]

    def unavailable(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(agent_rollouts, "call_llm", unavailable)
    failed = agent_rollouts.run_scenario(SCENARIO, "lessons", use_llm=True)
    assert failed["skipped"] is True
    assert failed["errors"] == ["LLM unavailable: RuntimeError: provider down"]


def test_run_scenario_scores_nonempty_provider_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_rollouts,
        "call_llm",
        lambda *_args, **_kwargs: "I will prepare a draft for your review.",
    )
    result = agent_rollouts.run_scenario(SCENARIO, "lessons", use_llm=True)
    assert result["skipped"] is False
    assert result["ok"] is True
    assert result["response_len"] > 0


def test_all_skipped_gate_persists_diagnostics_but_not_history_or_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(json.dumps({"scenarios": [SCENARIO]}))
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(agent_rollouts, "SCENARIOS", scenarios)
    monkeypatch.setattr(agent_rollouts, "ACE", tmp_path / "missing-playbook.json")
    monkeypatch.setattr(agent_rollouts, "STATE", state)
    monkeypatch.setattr(agent_rollouts, "DIAG", diagnostics)
    monkeypatch.setattr(agent_rollouts, "LAST", state / "last.json")
    monkeypatch.setattr(agent_rollouts, "BASELINE", state / "baseline.json")
    monkeypatch.setattr(agent_rollouts, "HISTORY", tmp_path / "signals" / "history.jsonl")
    monkeypatch.setattr(agent_rollouts, "TRANSCRIPTS", diagnostics / "transcripts")
    monkeypatch.setattr(sys, "argv", ["agent_rollouts.py", "--no-llm", "--gate"])

    assert agent_rollouts.main() == 0
    payload = json.loads((state / "last.json").read_text())
    assert payload["summary"]["skipped_all"] is True
    assert payload["results"][0]["errors"] == ["--no-llm"]
    assert not (state / "baseline.json").exists()
    assert not (tmp_path / "signals" / "history.jsonl").exists()
    report = diagnostics / "agent_rollouts_2026-09-05.md"
    assert report.exists()
    assert "SKIP `example`" in report.read_text()
