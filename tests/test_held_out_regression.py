from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import held_out_regression
from review_store import load_reviews


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    diagnostics = tmp_path / "diagnostics"
    review = tmp_path / "signals" / "pending_human_review.jsonl"
    monkeypatch.setattr(held_out_regression, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(held_out_regression, "REVIEW_FILE", review)
    monkeypatch.setattr(held_out_regression, "RATINGS_FILE", tmp_path / "ratings.jsonl")
    monkeypatch.setattr(held_out_regression, "MEMORY_DIR", tmp_path / "lessons")
    return {"diagnostics": diagnostics, "review": review}


def _entry(timestamp: str, rating: object, patterns: object) -> SimpleNamespace:
    return SimpleNamespace(timestamp=timestamp, rating=rating, patterns=patterns)


def test_date_and_rate_helpers_reject_malformed_state():
    assert held_out_regression.valid_date("2026-09-06") == "2026-09-06"
    assert held_out_regression.valid_date("") is None
    assert held_out_regression.valid_date(None) is None
    assert held_out_regression.valid_date("2026-99-99") is None
    assert held_out_regression.failure_rate([], "target") == 0.0
    pool = [
        _entry("t", 2, ["target"]),
        _entry("t", 8, ["target"]),
        _entry("t", "bad", ["target"]),
        _entry("t", 2, "bad-shape"),
    ]
    assert held_out_regression.failure_rate(pool, "target") == 0.25


def test_find_regressions_detects_relative_and_new_pattern_side_effects():
    entries = [
        _entry("2026-01-01T01:00:00Z", 2, ["existing"]),
        _entry("2026-01-01T02:00:00Z", 8, []),
        _entry("2026-01-03T01:00:00Z", 2, ["existing", "emerged"]),
        _entry("2026-01-04T01:00:00Z", 2, ["existing", "emerged"]),
        _entry("2026-01-05T01:00:00Z", 2, ["existing", "other", 4]),
    ]
    lessons = {
        "lesson": {"baseline_date": "2026-01-02"},
        "existing": {"baseline_date": "2026-01-02"},
        "bad": {"baseline_date": "not-a-date"},
        "missing": {},
    }
    flagged = held_out_regression.find_regressions(entries, lessons, 2)
    by_pattern = {
        (row["offending_lesson"], row["side_effect_pattern"]): row
        for row in flagged
    }
    assert ("lesson", "existing") in by_pattern
    assert ("lesson", "emerged") in by_pattern
    assert ("existing", "existing") not in by_pattern
    assert all(row["before_n"] == 2 and row["after_n"] == 3 for row in flagged)
    assert held_out_regression.find_regressions(entries, lessons, 4) == []


def test_find_regressions_ignores_small_relative_changes():
    entries = [
        *[_entry("2026-01-01T00:00:00Z", 2 if i == 0 else 8, ["side"] if i == 0 else []) for i in range(20)],
        *[_entry("2026-01-03T00:00:00Z", 2 if i == 0 else 8, ["side"] if i == 0 else []) for i in range(20)],
    ]
    assert held_out_regression.find_regressions(
        entries, {"lesson": "2026-01-02"}, 5
    ) == []


def test_main_rejects_invalid_minimum_and_handles_missing_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    assert held_out_regression.main(["--min-side-n", "0"]) == 2
    assert "must be positive" in capsys.readouterr().out
    monkeypatch.setattr(held_out_regression, "load_all_ratings", lambda _path: [])
    monkeypatch.setattr(held_out_regression, "discover_lessons", lambda _path: {})
    assert held_out_regression.main([]) == 0
    assert "No lessons found" in capsys.readouterr().out


def _patch_flagged_run(
    monkeypatch: pytest.MonkeyPatch,
    flagged: list[dict],
) -> None:
    entries = [_entry("2026-01-01T00:00:00Z", 2, ["side"])]
    monkeypatch.setattr(held_out_regression, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(held_out_regression, "classify_entry", lambda entry: entry.patterns)
    monkeypatch.setattr(
        held_out_regression,
        "discover_lessons",
        lambda _path: {"lesson": {"baseline_date": "2026-01-02"}},
    )
    monkeypatch.setattr(
        held_out_regression,
        "find_regressions",
        lambda _entries, _lessons, _minimum: flagged,
    )


def test_main_writes_atomic_no_regression_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _patch_flagged_run(monkeypatch, [])
    assert held_out_regression.main(["--apply"]) == 0
    assert "No held-out regressions" in capsys.readouterr().out
    report = next(paths["diagnostics"].glob("held_out_regression_*.md"))
    assert "Flagged pairs: 0" in report.read_text()
    assert not paths["review"].exists()


def test_main_reports_without_queueing_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    flagged = [{
        "offending_lesson": "lesson",
        "side_effect_pattern": "side",
        "lesson_date": "2026-01-02",
        "before_rate": 0.1,
        "after_rate": 0.3,
        "delta": 0.2,
        "before_n": 10,
        "after_n": 10,
    }]
    _patch_flagged_run(monkeypatch, flagged)
    assert held_out_regression.main([]) == 0
    output = capsys.readouterr().out
    assert "Re-run with --apply" in output
    assert "| lesson | side |" in output
    assert not paths["review"].exists()


def test_main_queues_worst_side_effect_once_transactionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    flagged = [
        {
            "offending_lesson": "lesson",
            "side_effect_pattern": "smaller",
            "lesson_date": "2026-01-02",
            "before_rate": 0.1,
            "after_rate": 0.2,
            "delta": 0.1,
            "before_n": 10,
            "after_n": 10,
        },
        {
            "offending_lesson": "lesson",
            "side_effect_pattern": "worst",
            "lesson_date": "2026-01-02",
            "before_rate": 0.1,
            "after_rate": 0.5,
            "delta": 0.4,
            "before_n": 10,
            "after_n": 10,
        },
        {
            "offending_lesson": "lesson",
            "side_effect_pattern": "later-smaller",
            "lesson_date": "2026-01-02",
            "before_rate": 0.1,
            "after_rate": 0.25,
            "delta": 0.15,
            "before_n": 10,
            "after_n": 10,
        },
    ]
    _patch_flagged_run(monkeypatch, flagged)
    assert held_out_regression.main(["--apply"]) == 0
    assert "Queued 1 lesson" in capsys.readouterr().out
    reviews = load_reviews(paths["review"])
    assert len(reviews) == 1
    assert reviews[0]["pattern"] == "lesson"
    assert "worst" in reviews[0]["note"]

    assert held_out_regression.main(["--apply"]) == 0
    assert "already queued or reviewed" in capsys.readouterr().out
    assert len(load_reviews(paths["review"])) == 1
