from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import judge_outcomes
import measure_effectiveness
from review_store import load_reviews
from state_io import append_jsonl


def test_judge_results_drive_effectiveness_and_human_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    signals = tmp_path / "signals"
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    pending = signals / "pending_judge.jsonl"
    results = signals / "judge_results.jsonl"
    invalid = signals / "invalid_judge.jsonl"
    reviews = signals / "pending_human_review.jsonl"
    scores = state / "effectiveness_scores.json"
    history = signals / "effectiveness_log.jsonl"

    monkeypatch.setattr(judge_outcomes, "PENDING_FILE", pending)
    monkeypatch.setattr(judge_outcomes, "RESULTS_FILE", results)
    monkeypatch.setattr(judge_outcomes, "INVALID_FILE", invalid)
    monkeypatch.setattr(judge_outcomes, "DIAG_DIR", diagnostics)
    monkeypatch.setattr(judge_outcomes, "gap_patterns", lambda: ["scope_misunderstanding"])

    turns = [
        {
            "timestamp": "2026-01-01T01:00:00Z",
            "session_id": "before-fail",
            "response": "failed before",
            "context": "context",
        },
        {
            "timestamp": "2026-01-01T02:00:00Z",
            "session_id": "before-pass",
            "response": "passed before",
            "context": "context",
        },
        {
            "timestamp": "2026-01-03T01:00:00Z",
            "session_id": "after-fail",
            "response": "failed after",
            "context": "context",
        },
    ]
    for turn in turns:
        append_jsonl(pending, turn)

    def judge(turn: dict, _patterns: list[str]) -> dict:
        failed = "fail" in turn["response"]
        return {
            "scope_misunderstanding": {
                "failed": failed,
                "evidence": "quoted proof" if failed else "",
            }
        }

    monkeypatch.setattr(judge_outcomes, "judge_turn", judge)
    assert judge_outcomes.main([]) == 0
    assert not pending.read_text().strip()
    assert len(judge_outcomes.load_judge_fails()) == 3
    assert judge_outcomes.judged_patterns() == {"scope_misunderstanding"}

    (lessons / "lesson_autogen_scope_misunderstanding.md").write_text(
        "---\nbaseline_date: 2026-01-02\ncontent_version: v1\n---\n\nClarify scope.\n"
    )
    monkeypatch.setattr(measure_effectiveness, "MEMORY_DIR", lessons)
    monkeypatch.setattr(measure_effectiveness, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(measure_effectiveness, "SCORES_JSON", scores)
    monkeypatch.setattr(measure_effectiveness, "EFFECT_LOG", history)
    monkeypatch.setattr(measure_effectiveness, "REVIEW_FILE", reviews)
    monkeypatch.setattr(measure_effectiveness, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(measure_effectiveness, "load_objective_fails", lambda: {})
    monkeypatch.setattr(measure_effectiveness, "covered_patterns", lambda: set())
    monkeypatch.setattr(
        measure_effectiveness,
        "load_judge_fails",
        lambda: judge_outcomes.load_judge_fails(results),
    )
    monkeypatch.setattr(
        measure_effectiveness,
        "judged_patterns",
        lambda: judge_outcomes.judged_patterns(results),
    )
    monkeypatch.setattr(measure_effectiveness, "push_to_bungraph", lambda *_args: None)
    monkeypatch.setattr(measure_effectiveness, "notify", lambda _message: True)

    assert measure_effectiveness.main(["--min-after", "1"]) == 0
    payload = json.loads(scores.read_text())
    score = payload["scores"]["scope_misunderstanding"]
    assert score["judge_covered"] is True
    assert score["judge_verdict"] == "regressed"
    assert score["judge_after_n"] == 1
    assert payload["escalate"] == []
    review_rows = load_reviews(reviews)
    assert len(review_rows) == 1
    assert review_rows[0]["pattern"] == "scope_misunderstanding"
    assert review_rows[0]["judge_verdict"] == "regressed"
