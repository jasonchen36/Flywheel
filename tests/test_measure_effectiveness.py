from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import measure_effectiveness
from state_io import atomic_write_json, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    lessons = tmp_path / "lessons"
    diagnostics = tmp_path / "diagnostics"
    state = tmp_path / "state"
    signals = tmp_path / "signals"
    lessons.mkdir(parents=True)
    paths = {
        "lessons": lessons,
        "diagnostics": diagnostics,
        "scores": state / "effectiveness_scores.json",
        "log": signals / "effectiveness_log.jsonl",
        "reviews": signals / "pending_human_review.jsonl",
        "ratings": signals / "ratings.jsonl",
    }
    monkeypatch.setattr(measure_effectiveness, "MEMORY_DIR", lessons)
    monkeypatch.setattr(measure_effectiveness, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(measure_effectiveness, "SCORES_JSON", paths["scores"])
    monkeypatch.setattr(measure_effectiveness, "EFFECT_LOG", paths["log"])
    monkeypatch.setattr(measure_effectiveness, "REVIEW_FILE", paths["reviews"])
    monkeypatch.setattr(measure_effectiveness, "RATINGS_FILE", paths["ratings"])
    return paths


def _entry(timestamp: str, rating: object, patterns: object) -> SimpleNamespace:
    return SimpleNamespace(timestamp=timestamp, rating=rating, patterns=patterns)


def _lesson(directory: Path, pattern: str, metadata: str) -> Path:
    path = directory / f"lesson_autogen_{pattern}.md"
    path.write_text(f"---\n{metadata}---\n\nRule.\n")
    return path


def test_date_and_verdict_boundaries():
    assert measure_effectiveness.entry_date("2026-09-06T12:00:00Z") == "2026-09-06"
    assert measure_effectiveness.entry_date("") == ""
    assert measure_effectiveness.days_between("2026-09-01", "2026-09-06") == 5
    assert measure_effectiveness.days_between("bad", "2026-09-06") == 999
    assert measure_effectiveness.days_between(None, "2026-09-06") == 999  # type: ignore[arg-type]

    verdict = measure_effectiveness.verdict_for
    assert verdict(0.5, 0.5, 1, 2, days_open=2) == "pending"
    assert verdict(0.5, 0.5, 1, 2, days_open=14) == "stale-pending"
    assert verdict(0.0, 0.1, 2, 2) == "no-baseline"
    assert verdict(0.5, 0.0, 2, 2) == "resolved"
    assert verdict(0.8, 0.4, 2, 2) == "working"
    assert verdict(0.8, 0.7, 2, 2) == "improving"
    assert verdict(0.5, 0.6, 2, 2) == "flat"
    assert verdict(0.5, 0.61, 2, 2) == "regressed"
    assert measure_effectiveness.is_real_verdict("working") is True
    assert measure_effectiveness.is_real_verdict("pending") is False


def test_rate_helpers_ignore_malformed_ratings_and_split_judge_dates():
    entries = [
        _entry("t1", 2, ["target"]),
        _entry("t2", 8, ["target"]),
        _entry("t3", "bad", ["target"]),
        _entry("t4", 2, "bad-shape"),
    ]
    assert measure_effectiveness.rating_failure_rate([], "target") == (0.0, 0)
    assert measure_effectiveness.rating_failure_rate(entries, "target") == (0.25, 1)

    failures = {
        "2026-01-01T00:00:00Z": {"target": True},
        "2026-01-02T00:00:00Z": {"target": False},
        "2026-01-03T00:00:00Z": {"target": True, "other": False},
    }
    assert measure_effectiveness.judge_failure_rate(
        failures, "target", "2026-01-03", before=True
    ) == (0.5, 2)
    assert measure_effectiveness.judge_failure_rate(
        failures, "target", "2026-01-03", before=False
    ) == (1.0, 1)
    assert measure_effectiveness.judge_failure_rate(
        failures, "missing", "2026-01-03", before=False
    ) == (0.0, 0)


def test_prior_scores_and_numeric_normalization_are_shape_safe(tmp_path: Path):
    path = tmp_path / "scores.json"
    assert measure_effectiveness.load_prior_scores(path) == {}
    path.write_text("[]")
    assert measure_effectiveness.load_prior_scores(path) == {}
    atomic_write_json(path, {"scores": ["bad"]})
    assert measure_effectiveness.load_prior_scores(path) == {}
    atomic_write_json(path, {"scores": {"good": {"verdict": "working"}, "bad": []}})
    assert measure_effectiveness.load_prior_scores(path) == {"good": {"verdict": "working"}}

    assert measure_effectiveness.safe_nonnegative_int(None) == 0
    assert measure_effectiveness.safe_nonnegative_int("bad") == 0
    assert measure_effectiveness.safe_nonnegative_int(float("inf")) == 0
    assert measure_effectiveness.safe_nonnegative_int(-3) == 0
    assert measure_effectiveness.safe_nonnegative_int("4") == 4


def test_escalation_signal_precedence_and_dual_signal_override(monkeypatch: pytest.MonkeyPatch):
    base = {
        "pattern": "semantic",
        "verdict": "regressed",
        "after_n": 10,
        "eval_covered": False,
        "obj_verdict": "resolved",
        "judge_covered": False,
        "judge_verdict": "working",
    }
    assert measure_effectiveness.escalation_verdict(base, 5) == "regressed"
    assert measure_effectiveness.escalation_verdict({**base, "judge_covered": True}, 5) == "working"
    assert measure_effectiveness.escalation_verdict({**base, "eval_covered": True}, 5) == "resolved"

    monkeypatch.setattr(measure_effectiveness, "ENFORCEABLE_PATTERNS", {"semantic"})
    assert measure_effectiveness.escalation_verdict({**base, "eval_covered": True}, 5) == "regressed"
    assert measure_effectiveness.escalation_verdict(
        {**base, "eval_covered": True, "after_n": "bad"}, 5
    ) == "resolved"
    assert measure_effectiveness.escalation_verdict({}, 5) == "pending"


def test_discover_lessons_uses_frozen_date_precedence(tmp_path: Path):
    _lesson(
        tmp_path,
        "baseline",
        "baseline_date: 2026-01-01\nfirst_seen: 2026-01-02\nlast_updated: 2026-01-03\ncontent_version: abc\n",
    )
    _lesson(tmp_path, "first", "first_seen: 2026-02-01\nlast_updated: 2026-02-02\n")
    _lesson(tmp_path, "last", "last_updated: 2026-03-01\n")
    _lesson(tmp_path, "undated", "pattern: undated\n")
    lessons = measure_effectiveness.discover_lessons(tmp_path)
    assert lessons["baseline"]["baseline_date"] == "2026-01-01"
    assert lessons["baseline"]["content_version"] == "abc"
    assert lessons["first"]["baseline_date"] == "2026-02-01"
    assert lessons["last"]["baseline_date"] == "2026-03-01"
    assert lessons["undated"]["baseline_date"] == ""


def test_notify_and_bungraph_are_nonfatal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    calls: list[tuple[list[str], dict[str, str]]] = []

    def spawned(command: list[str], **kwargs: object):
        calls.append((command, kwargs.get("env", {})))
        return object()

    monkeypatch.setattr(measure_effectiveness.subprocess, "Popen", spawned)
    assert measure_effectiveness.notify("message") is True
    measure_effectiveness.push_to_bungraph("pattern", "working", -0.2, "2026-09-06")
    assert len(calls) == 3
    measure_effectiveness.push_to_bungraph("pattern", "pending", 0.0, "2026-09-06")
    assert len(calls) == 3

    monkeypatch.setattr(
        measure_effectiveness.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    assert measure_effectiveness.notify("message") is False
    measure_effectiveness.push_to_bungraph("pattern", "working", 0.1, "2026-09-06")
    output = capsys.readouterr().out
    assert "notification unavailable" in output
    assert "bungraph-loopback" in output


def test_main_rejects_invalid_minimum_and_handles_no_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    assert measure_effectiveness.main(["--min-after", "0"]) == 2
    assert "must be positive" in capsys.readouterr().out
    monkeypatch.setattr(measure_effectiveness, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(measure_effectiveness, "load_objective_fails", lambda: {})
    monkeypatch.setattr(measure_effectiveness, "covered_patterns", lambda: set())
    monkeypatch.setattr(measure_effectiveness, "load_judge_fails", lambda: {})
    monkeypatch.setattr(measure_effectiveness, "judged_patterns", lambda: set())
    assert measure_effectiveness.main([]) == 0
    assert "No auto-generated lessons" in capsys.readouterr().out


def _seed_effectiveness_run(
    paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    pattern: str,
    *,
    covered: bool,
) -> list[SimpleNamespace]:
    _lesson(paths["lessons"], pattern, "baseline_date: 2026-01-02\ncontent_version: v1\n")
    _lesson(paths["lessons"], "undated", "pattern: undated\n")
    entries = [
        _entry("2026-01-01T01:00:00Z", 2, [pattern]),
        _entry("2026-01-01T02:00:00Z", 8, []),
        _entry("2026-01-03T01:00:00Z", 2, [pattern]),
        _entry("2026-01-04T01:00:00Z", 2, [pattern]),
    ]
    monkeypatch.setattr(measure_effectiveness, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(measure_effectiveness, "classify_entry", lambda entry: entry.patterns)
    monkeypatch.setattr(
        measure_effectiveness,
        "load_objective_fails",
        lambda: {
            entries[0].timestamp: {pattern: True},
            entries[1].timestamp: {pattern: False},
            entries[2].timestamp: {pattern: False},
            entries[3].timestamp: {pattern: False},
        },
    )
    monkeypatch.setattr(measure_effectiveness, "covered_patterns", lambda: {pattern} if covered else set())
    monkeypatch.setattr(measure_effectiveness, "load_judge_fails", lambda: {})
    monkeypatch.setattr(measure_effectiveness, "judged_patterns", lambda: set())
    monkeypatch.setattr(measure_effectiveness, "load_reviews", lambda _path: [])
    return entries


def test_main_persists_dual_signal_escalation_and_batched_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    pattern = "unverified_completion"
    _seed_effectiveness_run(paths, monkeypatch, pattern, covered=True)
    paths["scores"].parent.mkdir(parents=True)
    paths["scores"].write_text('{")bad": true}')
    pushed: list[tuple[str, str]] = []
    notices: list[str] = []
    monkeypatch.setattr(
        measure_effectiveness,
        "push_to_bungraph",
        lambda current, verdict, _delta, _today: pushed.append((current, verdict)),
    )
    monkeypatch.setattr(measure_effectiveness, "notify", lambda message: notices.append(message) or True)
    monkeypatch.setattr(measure_effectiveness, "expire_pending", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(measure_effectiveness, "enqueue_pending", lambda _path, rows: rows)

    assert measure_effectiveness.main(["--min-after", "2"]) == 0
    output = capsys.readouterr().out
    assert "Escalation: 1" in output
    state = json.loads(paths["scores"].read_text())
    assert state["escalate"] == [pattern]
    assert state["scores"][pattern]["verdict"] == "regressed"
    assert state["scores"][pattern]["obj_verdict"] == "resolved"
    assert len(load_jsonl_objects(paths["log"]).records) == 2
    assert (pattern, "regressed") in pushed
    assert any("known regression" in message for message in notices)


def test_main_gates_first_time_soft_regression_and_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    pattern = "scope_misunderstanding"
    _seed_effectiveness_run(paths, monkeypatch, pattern, covered=False)
    monkeypatch.setattr(measure_effectiveness, "ENFORCEABLE_PATTERNS", set())
    queued: list[dict] = []
    notices: list[str] = []
    monkeypatch.setattr(measure_effectiveness, "push_to_bungraph", lambda *_args: None)
    monkeypatch.setattr(measure_effectiveness, "notify", lambda message: notices.append(message) or True)
    monkeypatch.setattr(measure_effectiveness, "expire_pending", lambda *_args, **_kwargs: ["old"])
    monkeypatch.setattr(
        measure_effectiveness,
        "enqueue_pending",
        lambda _path, rows: queued.extend(rows) or rows,
    )

    assert measure_effectiveness.main(["--min-after", "2"]) == 0
    state = json.loads(paths["scores"].read_text())
    assert state["escalate"] == []
    assert queued[0]["pattern"] == pattern
    assert any("NEW regression" in message for message in notices)
    assert "Auto-escalated" in capsys.readouterr().out

    paths = _configure(tmp_path / "dry", monkeypatch)
    _seed_effectiveness_run(paths, monkeypatch, pattern, covered=False)
    monkeypatch.setattr(measure_effectiveness, "load_reviews", lambda _path: [])
    assert measure_effectiveness.main(["--dry-run", "--min-after", "2"]) == 0
    assert "no files written" in capsys.readouterr().out
    assert not paths["scores"].exists()
    assert not paths["log"].exists()


def test_objective_failure_rate_handles_empty_and_malformed_entries():
    pool = [
        SimpleNamespace(timestamp="before"),
        SimpleNamespace(timestamp="after"),
        SimpleNamespace(),
    ]
    failures = {
        "before": {"target": True},
        "after": {"target": False},
    }
    assert measure_effectiveness.objective_failure_rate([], failures, "target") == 0.0
    assert measure_effectiveness.objective_failure_rate(pool, failures, "target") == pytest.approx(1 / 3)


def test_active_review_patterns_normalizes_status_and_age():
    records = [
        {"pattern": 4, "status": "pending", "detected_at": "2026-09-06"},
        {"pattern": "processing", "status": "processing"},
        {"pattern": "failed", "status": "action_failed"},
        {"pattern": "approved", "status": "approved"},
        {"pattern": "fresh", "status": "pending", "detected_at": "2026-09-01"},
        {"pattern": "old", "status": "pending", "detected_at": "2026-08-01"},
        {"pattern": "bad-date", "status": "pending", "detected_at": "bad"},
        {"pattern": "missing-date", "status": "pending"},
    ]
    assert measure_effectiveness.active_review_patterns(
        records,
        "2026-09-06",
        max_age_days=14,
    ) == {"processing", "failed", "fresh"}


def test_escalation_driver_covers_subjective_objective_judge_and_fallbacks():
    base = {
        "verdict": "regressed",
        "delta": 0.4,
        "after_n": 5,
        "eval_covered": False,
        "obj_verdict": "working",
        "obj_delta": -0.2,
        "judge_covered": False,
        "judge_verdict": "flat",
        "judge_delta": 0.1,
        "judge_after_n": 4,
    }
    assert measure_effectiveness.escalation_driver(base, "regressed") == ("subj", 0.4, 5)
    assert measure_effectiveness.escalation_driver(
        {**base, "eval_covered": True}, "working"
    ) == ("obj", -0.2, 5)
    assert measure_effectiveness.escalation_driver(
        {**base, "judge_covered": True}, "flat"
    ) == ("jdg", 0.1, 4)
    assert measure_effectiveness.escalation_driver(
        {**base, "eval_covered": True}, "unexpected"
    ) == ("obj", -0.2, 5)
    assert measure_effectiveness.escalation_driver(base, "unexpected") == ("subj", 0.4, 5)


def test_main_bounds_large_stale_pending_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(measure_effectiveness, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(measure_effectiveness, "classify_entry", lambda entry: entry.patterns)
    monkeypatch.setattr(
        measure_effectiveness,
        "discover_lessons",
        lambda _path: {
            f"pattern_{index:02d}": {
                "baseline_date": "2020-01-01",
                "content_version": "v1",
                "last_updated": "2020-01-01",
            }
            for index in range(41)
        },
    )
    monkeypatch.setattr(measure_effectiveness, "load_objective_fails", lambda: {})
    monkeypatch.setattr(measure_effectiveness, "covered_patterns", lambda: set())
    monkeypatch.setattr(measure_effectiveness, "load_judge_fails", lambda: {})
    monkeypatch.setattr(measure_effectiveness, "judged_patterns", lambda: set())
    monkeypatch.setattr(measure_effectiveness, "load_reviews", lambda _path: [])

    assert measure_effectiveness.main(["--dry-run", "--min-after", "5"]) == 0
    output = capsys.readouterr().out
    assert "## Stale-pending" in output
    assert "… and 1 more" in output


def test_objective_failure_rate_prefers_exact_turn_over_legacy_timestamp() -> None:
    entry = SimpleNamespace(session_id="session-a", timestamp="same")
    failures = {
        "session-a|same": {"target": True},
        "same": {"target": False},
    }
    assert measure_effectiveness.objective_failure_rate([entry], failures, "target") == 1.0
