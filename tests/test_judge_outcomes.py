from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import judge_outcomes
from state_io import append_jsonl, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    signals = tmp_path / "signals"
    diagnostics = tmp_path / "diagnostics"
    paths = {
        "pending": signals / "pending_judge.jsonl",
        "results": signals / "judge_results.jsonl",
        "reclass": signals / "other_reclass.jsonl",
        "diagnostics": diagnostics,
        "invalid": signals / "invalid_judge.jsonl",
        "ratings": signals / "ratings.jsonl",
    }
    monkeypatch.setattr(judge_outcomes, "PENDING_FILE", paths["pending"])
    monkeypatch.setattr(judge_outcomes, "RESULTS_FILE", paths["results"])
    monkeypatch.setattr(judge_outcomes, "OTHER_RECLASS_FILE", paths["reclass"])
    monkeypatch.setattr(judge_outcomes, "DIAG_DIR", diagnostics)
    monkeypatch.setattr(judge_outcomes, "INVALID_FILE", paths["invalid"])
    monkeypatch.setattr(judge_outcomes, "RATINGS_FILE", paths["ratings"])
    return paths


def _turn(response: str = "A substantive response", suffix: str = "one") -> dict:
    return {
        "timestamp": "2026-09-06T10:00:00Z",
        "session_id": "session",
        "response": response,
        "context": "User requested verification.",
        "skill": "deploy",
        "repo": "Flywheel",
        "suffix": suffix,
    }


def test_gap_patterns_excludes_binary_covered_patterns(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(judge_outcomes, "PATTERN_KEYWORDS", {"covered": [], "semantic": []})
    monkeypatch.setattr(judge_outcomes, "covered_patterns", lambda: {"covered"})
    assert judge_outcomes.gap_patterns() == ["semantic"]


def test_environment_bounds_are_positive_validated_and_capped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOUND", "bad")
    assert judge_outcomes.env_positive_int("BOUND", 3, 9) == 3
    monkeypatch.setenv("BOUND", "0")
    assert judge_outcomes.env_positive_int("BOUND", 3, 9) == 3
    monkeypatch.setenv("BOUND", "12")
    assert judge_outcomes.env_positive_int("BOUND", 3, 9) == 9
    monkeypatch.setenv("BOUND", "4")
    assert judge_outcomes.env_positive_int("BOUND", 3, 9) == 4


def test_extract_json_accepts_fences_and_rejects_invalid_shapes():
    assert judge_outcomes._extract_json('```json\n{"failures": []}\n```') == {"failures": []}
    assert judge_outcomes._extract_json("no object") is None
    assert judge_outcomes._extract_json("{bad}") is None
    assert judge_outcomes._extract_json("[]") is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        '{"wrong": []}',
        '{"failures": "bad"}',
        '{"failures": ["bad"]}',
        '{"failures": [{"pattern": "unknown", "evidence": "quote"}]}',
        '{"failures": [{"pattern": "scope_misunderstanding", "evidence": ""}]}',
    ],
)
def test_judge_once_preserves_turn_for_empty_or_malformed_provider_output(
    monkeypatch: pytest.MonkeyPatch, raw: str
):
    monkeypatch.setattr(judge_outcomes, "call_llm", lambda *_args, **_kwargs: raw)
    assert judge_outcomes._judge_once("context", "response", ["scope_misunderstanding"]) is None


def test_judge_once_accepts_clean_and_evidence_cited_verdicts(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    def clean(prompt: str, **_kwargs: object) -> str:
        calls.append(prompt)
        return '{"failures": []}'

    monkeypatch.setattr(judge_outcomes, "call_llm", clean)
    assert judge_outcomes._judge_once("context", "response", ["scope_misunderstanding"]) == {}
    assert len(calls) == 1

    evidence = "x" * 200
    monkeypatch.setattr(
        judge_outcomes,
        "call_llm",
        lambda *_args, **_kwargs: json.dumps(
            {"failures": [{"pattern": "scope_misunderstanding", "evidence": evidence}]}
        ),
    )
    result = judge_outcomes._judge_once("context", "response", ["scope_misunderstanding"])
    assert result == {"scope_misunderstanding": evidence[:160]}


def test_judge_once_handles_provider_exception(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def unavailable(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(judge_outcomes, "call_llm", unavailable)
    assert judge_outcomes._judge_once("context", "response", ["scope_misunderstanding"]) is None
    assert "provider unavailable" in capsys.readouterr().out


def test_judge_turn_requires_every_quorum_pass_and_uses_majority(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(judge_outcomes, "QUORUM", 3)
    outcomes = iter([
        {"scope_misunderstanding": "first"},
        {},
        {"scope_misunderstanding": "third"},
    ])
    monkeypatch.setattr(judge_outcomes, "_judge_once", lambda *_args: next(outcomes))
    matrix = judge_outcomes.judge_turn(_turn(), ["scope_misunderstanding", "tool_misuse"])
    assert matrix == {
        "scope_misunderstanding": {"failed": True, "evidence": "first"},
        "tool_misuse": {"failed": False, "evidence": ""},
    }

    outcomes = iter([{}, None, {}])
    monkeypatch.setattr(judge_outcomes, "_judge_once", lambda *_args: next(outcomes))
    assert judge_outcomes.judge_turn(_turn(), ["scope_misunderstanding"]) is None
    assert judge_outcomes.judge_turn({"response": 4}, ["scope_misunderstanding"]) is None
    assert judge_outcomes.judge_turn(_turn(), []) == {}


def test_turn_identity_is_explicit_or_content_sensitive():
    first = _turn("first response")
    second = _turn("second response")
    assert judge_outcomes.turn_key(first) != judge_outcomes.turn_key(second)
    assert judge_outcomes.turn_key({"turn_id": "explicit"}) == "explicit"
    assert judge_outcomes.turn_key(first) == judge_outcomes.turn_key(dict(first))
    legacy = {"timestamp": "t", "session_id": "s", "pattern": 4}
    assert judge_outcomes.result_key(legacy)[1] == ""


def test_atomic_commit_deduplicates_results_and_preserves_unjudged_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    first = _turn("first response")
    second = _turn("second response")
    append_jsonl(paths["pending"], first)
    append_jsonl(paths["pending"], second)
    first_key = judge_outcomes.turn_key(first)
    existing = {"turn_id": first_key, "pattern": "scope_misunderstanding", "passed": True}
    append_jsonl(paths["results"], existing)
    legacy = {
        "timestamp": "legacy-time",
        "session_id": "legacy-session",
        "pattern": "legacy-pattern",
        "passed": False,
    }
    append_jsonl(paths["results"], legacy)
    tool_row = {"turn_id": first_key, "pattern": "tool_misuse", "passed": False}
    legacy_retry = {
        **legacy,
        "turn_id": "new-format-id",
    }
    rows = [dict(existing), tool_row, dict(tool_row), legacy_retry]

    written, quarantined, remaining = judge_outcomes.commit_judgements(rows, {first_key})
    assert written == 1
    assert quarantined == 0
    assert remaining == [second]
    results = load_jsonl_objects(paths["results"]).records
    assert len(results) == 3
    assert {row["pattern"] for row in results} == {
        "scope_misunderstanding",
        "tool_misuse",
        "legacy-pattern",
    }

    invalid = {"turn_id": "bad", "reason": "missing response", "record": {}}
    written, quarantined, _ = judge_outcomes.commit_judgements(
        [], set(), [invalid, dict(invalid)]
    )
    assert (written, quarantined) == (0, 1)
    written, quarantined, _ = judge_outcomes.commit_judgements([], set(), [invalid])
    assert (written, quarantined) == (0, 0)


def test_invalid_turn_reasons_are_explicit():
    assert judge_outcomes.invalid_turn_reason({}) == "missing response"
    assert judge_outcomes.invalid_turn_reason({"response": "ok"}) == "missing timestamp"
    assert judge_outcomes.invalid_turn_reason({"response": "ok", "timestamp": "t"}) == "missing session_id"
    assert judge_outcomes.invalid_turn_reason(_turn()) is None


def test_result_readers_ignore_malformed_or_incomplete_rows(tmp_path: Path):
    results = tmp_path / "results.jsonl"
    results.write_text(
        "not-json\n"
        '{"timestamp":"t","pattern":"p","passed":true}\n'
        '{"timestamp":"t","pattern":"p","passed":false}\n'
        '{"session_id":"s","pattern":"q","passed":true}\n'
        '{"timestamp":"","pattern":"bad","passed":false}\n'
        '{"timestamp":"t","pattern":"","passed":false}\n'
        '{"timestamp":"t","pattern":4,"passed":false}\n'
    )
    assert judge_outcomes.load_judge_fails(results) == {"t": {"p": True}, "s": {"q": False}}
    assert judge_outcomes.judged_patterns(results) == {"p", "q", "bad"}


def test_main_status_and_argument_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    append_jsonl(paths["pending"], _turn())
    paths["results"].parent.mkdir(parents=True, exist_ok=True)
    paths["results"].write_text("bad\n{}\n")
    paths["reclass"].write_text("bad\n{}\n")
    monkeypatch.setattr(judge_outcomes, "gap_patterns", lambda: ["scope_misunderstanding"])

    assert judge_outcomes.main(["--limit", "-1"]) == 2
    assert "must be non-negative" in capsys.readouterr().out
    assert judge_outcomes.main(["--status"]) == 0
    output = capsys.readouterr().out
    assert "pending queue: 1" in output
    assert "judge_results rows: 1" in output
    assert "other_reclass: 1" in output


def test_main_reclassification_cli_modes_write_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    assert judge_outcomes.main(["--reclass-other", "--no-llm"]) == 0
    assert "nothing to do" in capsys.readouterr().out

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        judge_outcomes,
        "reclass_other",
        lambda limit, dry_run: calls.append((limit, dry_run)) or 3,
    )
    assert judge_outcomes.main(["--reclass-other", "--dry-run", "--limit", "7"]) == 0
    assert calls == [(7, True)]
    report = next(paths["diagnostics"].glob("reclass_other_*.md"))
    assert "labeled this run: 3" in report.read_text()


def test_main_no_llm_empty_queue_and_dry_run_preserve_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    append_jsonl(paths["pending"], _turn())
    assert judge_outcomes.main(["--no-llm"]) == 0
    assert "skipping (1 turns queued" in capsys.readouterr().out
    assert len(load_jsonl_objects(paths["pending"]).records) == 1

    monkeypatch.setattr(judge_outcomes, "gap_patterns", lambda: ["scope_misunderstanding"])
    monkeypatch.setattr(
        judge_outcomes,
        "judge_turn",
        lambda *_args: {"scope_misunderstanding": {"failed": False, "evidence": ""}},
    )
    assert judge_outcomes.main(["--dry-run"]) == 0
    assert "queue untouched" in capsys.readouterr().out
    assert not paths["results"].exists()
    assert len(load_jsonl_objects(paths["pending"]).records) == 1

    paths["pending"].unlink()
    assert judge_outcomes.main([]) == 0
    assert "queue empty" in capsys.readouterr().out


def test_main_quarantines_malformed_rows_without_blocking_valid_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    append_jsonl(paths["pending"], {"timestamp": "t", "session_id": "bad"})
    append_jsonl(paths["pending"], _turn())
    monkeypatch.setattr(judge_outcomes, "gap_patterns", lambda: ["scope_misunderstanding"])
    monkeypatch.setattr(
        judge_outcomes,
        "judge_turn",
        lambda *_args: {"scope_misunderstanding": {"failed": False, "evidence": ""}},
    )
    assert judge_outcomes.main([]) == 0
    assert load_jsonl_objects(paths["pending"]).records == []
    assert len(load_jsonl_objects(paths["results"]).records) == 1
    invalid = load_jsonl_objects(paths["invalid"]).records
    assert len(invalid) == 1
    assert invalid[0]["reason"] == "missing response"
    assert "quarantined: 1" in capsys.readouterr().out


def test_main_provider_failure_preserves_queue_and_valid_result_drains_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    turn = _turn()
    append_jsonl(paths["pending"], turn)
    monkeypatch.setattr(judge_outcomes, "gap_patterns", lambda: ["scope_misunderstanding"])
    monkeypatch.setattr(judge_outcomes, "judge_turn", lambda *_args: None)
    assert judge_outcomes.main([]) == 0
    assert len(load_jsonl_objects(paths["pending"]).records) == 1
    assert load_jsonl_objects(paths["results"]).records == []
    assert "LLM: unavailable" in capsys.readouterr().out

    monkeypatch.setattr(
        judge_outcomes,
        "judge_turn",
        lambda *_args: {"scope_misunderstanding": {"failed": True, "evidence": "quoted proof"}},
    )
    assert judge_outcomes.main([]) == 0
    assert load_jsonl_objects(paths["pending"]).records == []
    results = load_jsonl_objects(paths["results"]).records
    assert len(results) == 1
    assert results[0]["turn_id"] == judge_outcomes.turn_key(turn)
    assert results[0]["passed"] is False


def test_reclass_other_filters_high_and_known_ratings_and_counts_clean_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    high = SimpleNamespace(rating=8)
    known = SimpleNamespace(rating=2)
    other = SimpleNamespace(
        timestamp="2026-09-06T11:00:00Z",
        session_id="other",
        rating=2,
        response_preview="response",
        sentiment_summary="summary",
        comment="",
        skill="review",
        agent="claude",
        repo="Flywheel",
    )
    monkeypatch.setattr(judge_outcomes, "load_all_ratings", lambda _path: [high, known, other])
    monkeypatch.setattr(
        judge_outcomes,
        "classify_entry",
        lambda entry: ["other"] if entry is other else ["known"],
    )
    monkeypatch.setattr(
        judge_outcomes,
        "judge_turn",
        lambda *_args: {"scope_misunderstanding": {"failed": False, "evidence": ""}},
    )
    assert judge_outcomes.reclass_other() == 0
    assert "labeled=0 still_other=1" in capsys.readouterr().out


def test_reclass_other_handles_empty_unavailable_dry_and_persisted_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    entry = SimpleNamespace(
        timestamp="2026-09-06T11:00:00Z",
        session_id="low",
        rating=2,
        response_preview="A response",
        sentiment_summary="Missed the requested scope",
        comment="",
        skill="review",
        agent="claude",
        repo="Flywheel",
    )
    monkeypatch.setattr(judge_outcomes, "load_all_ratings", lambda _path: [])
    assert judge_outcomes.reclass_other() == 0
    assert "candidates=0" in capsys.readouterr().out

    monkeypatch.setattr(judge_outcomes, "load_all_ratings", lambda _path: [entry])
    monkeypatch.setattr(judge_outcomes, "classify_entry", lambda _entry: ["other"])
    monkeypatch.setattr(judge_outcomes, "judge_turn", lambda *_args: None)
    assert judge_outcomes.reclass_other() == 0
    assert "LLM unavailable" in capsys.readouterr().out

    matrix = {
        pattern: {"failed": pattern == "scope_misunderstanding", "evidence": "proof"}
        for pattern in judge_outcomes.RECLASS_PATTERNS[:24]
    }
    matrix["scope_misunderstanding"] = {"failed": True, "evidence": "proof"}
    monkeypatch.setattr(judge_outcomes, "judge_turn", lambda *_args: matrix)
    assert judge_outcomes.reclass_other(dry_run=True) == 1
    assert not paths["reclass"].exists()
    assert not paths["results"].exists()

    assert judge_outcomes.reclass_other() == 1
    assert load_jsonl_objects(paths["reclass"]).records[0]["patterns"] == ["scope_misunderstanding"]
    result = load_jsonl_objects(paths["results"]).records[0]
    assert result["pattern"] == "scope_misunderstanding"
    assert result["passed"] is False
