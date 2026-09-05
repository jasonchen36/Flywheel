from __future__ import annotations

import errno
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import lesson_retire
from state_io import atomic_write_json


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    lessons = tmp_path / "lessons"
    state = tmp_path / "state"
    archive = state / "lesson_archive"
    diagnostics = tmp_path / "diagnostics"
    lessons.mkdir()
    state.mkdir()
    paths = {
        "lessons": lessons,
        "state": state,
        "archive": archive,
        "diagnostics": diagnostics,
        "scores": state / "scores.json",
    }
    monkeypatch.setattr(lesson_retire, "MEM", lessons)
    monkeypatch.setattr(lesson_retire, "STATE", state)
    monkeypatch.setattr(lesson_retire, "ARCHIVE", archive)
    monkeypatch.setattr(lesson_retire, "DIAG", diagnostics)
    monkeypatch.setattr(lesson_retire, "SCORES", paths["scores"])
    return paths


def _lesson(
    directory: Path,
    pattern: str,
    *,
    occurrence_count: int = 1,
    first_seen: str = "2026-01-01",
    baseline: bool = False,
) -> Path:
    baseline_line = f"  baseline_date: {first_seen}\n" if baseline else ""
    path = directory / f"lesson_autogen_{pattern}.md"
    path.write_text(
        "---\n"
        f"pattern: {pattern}\n"
        f"occurrence_count: {occurrence_count}\n"
        f"first_seen: {first_seen}\n"
        f"{baseline_line}"
        "---\n\nAlways verify this behavior.\n"
    )
    return path


def test_score_loading_and_numeric_conversion_are_shape_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    assert lesson_retire.load_scores() == {}
    atomic_write_json(paths["scores"], {"scores": []})
    assert lesson_retire.load_scores() == {}
    atomic_write_json(
        paths["scores"],
        {"scores": {"valid": {"verdict": "pending"}, "invalid": []}},
    )
    assert lesson_retire.load_scores() == {"valid": {"verdict": "pending"}}
    assert lesson_retire.safe_int(None) == 0
    assert lesson_retire.safe_int({}) == 0
    assert lesson_retire.safe_int("bad") == 0
    assert lesson_retire.safe_int("4") == 4
    assert lesson_retire.safe_int(2.9) == 2
    assert lesson_retire.safe_int(float("inf")) == 0


def test_metadata_backfill_and_archive_path_are_durable_and_collision_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    path = _lesson(paths["lessons"], "candidate")
    meta = lesson_retire.parse_meta(path)
    assert meta["pattern"] == "candidate"
    assert meta["occ"] == 1
    assert meta["has_baseline"] is False
    assert lesson_retire.backfill_baseline(meta) is True
    assert "baseline_date: 2026-01-01" in path.read_text()
    assert lesson_retire.backfill_baseline(lesson_retire.parse_meta(path)) is False

    paths["archive"].mkdir()
    first = paths["archive"] / "candidate_2026-09-05.md"
    first.write_text("existing")
    second = paths["archive"] / "candidate_2026-09-05.1.md"
    second.write_text("existing")
    assert lesson_retire.archive_path("candidate", "2026-09-05").name == (
        "candidate_2026-09-05.2.md"
    )


def test_archive_lesson_falls_back_only_for_cross_device_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.md"
    destination = tmp_path / "archive" / "destination.md"
    source.write_text("lesson content")
    real_replace = lesson_retire.os.replace

    def cross_device_replace(source_path: object, destination_path: object) -> None:
        if Path(source_path) == source and Path(destination_path) == destination:
            raise OSError(errno.EXDEV, "cross-device")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(lesson_retire.os, "replace", cross_device_replace)
    lesson_retire.archive_lesson(source, destination)
    assert destination.read_text() == "lesson content"
    assert not source.exists()

    source.write_text("retry")

    def denied_replace(source_path: object, destination_path: object) -> None:
        if Path(source_path) == source and Path(destination_path) == destination:
            raise OSError(errno.EACCES, "denied")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(lesson_retire.os, "replace", denied_replace)
    with pytest.raises(OSError, match="denied"):
        lesson_retire.archive_lesson(source, destination)
    assert source.exists()


def test_main_rejects_negative_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    assert lesson_retire.main(["--retire-days", "-1"]) == 2
    assert lesson_retire.main(["--max-occ", "-1"]) == 2
    assert capsys.readouterr().out.count("must be non-negative") == 2


def test_main_backfills_only_without_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    path = _lesson(paths["lessons"], "backfill")
    assert lesson_retire.main(["--backfill-baseline-only"]) == 0
    assert "baseline backfill only: 1 files" in capsys.readouterr().out
    assert path.exists()
    assert "baseline_date" in path.read_text()
    assert not paths["diagnostics"].exists()


def test_main_selects_only_unprotected_pending_zombies_and_reports_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _lesson(paths["lessons"], "stale", occurrence_count=9, baseline=True)
    _lesson(paths["lessons"], "pending", occurrence_count=2, baseline=True)
    _lesson(paths["lessons"], "working", baseline=True)
    _lesson(paths["lessons"], "unverified_completion", baseline=True)
    _lesson(paths["lessons"], "young", baseline=True)
    _lesson(paths["lessons"], "bad_numeric", baseline=True)
    atomic_write_json(
        paths["scores"],
        {
            "scores": {
                "stale": {"verdict": "stale-pending", "days_open": 20},
                "pending": {"verdict": "pending", "after_n": 1, "days_open": 20},
                "working": {"verdict": "working", "days_open": 100},
                "unverified_completion": {"verdict": "stale-pending", "days_open": 100},
                "young": {"verdict": "pending", "after_n": 0, "days_open": 2},
                "bad_numeric": {"verdict": "pending", "after_n": "bad", "days_open": "bad"},
            }
        },
    )
    assert lesson_retire.main([]) == 0
    output = capsys.readouterr().out
    assert "retire_candidates=2" in output
    assert "stale-pending days_open=20" in output
    assert "pending zombie" in output
    assert "dry-run" in output
    report = next(paths["diagnostics"].glob("lesson_retire_*.md")).read_text()
    assert "stale" in report and "pending" in report
    assert all(path.exists() for path in paths["lessons"].glob("*.md"))


def test_main_apply_uses_collision_safe_atomic_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    candidate = _lesson(paths["lessons"], "stale", baseline=True)
    atomic_write_json(
        paths["scores"],
        {"scores": {"stale": {"verdict": "stale-pending", "days_open": 30}}},
    )
    paths["archive"].mkdir()
    today = lesson_retire.datetime.now(lesson_retire.timezone.utc).strftime("%Y-%m-%d")
    (paths["archive"] / f"stale_{today}.md").write_text("prior archive")

    assert lesson_retire.main(["--apply"]) == 0
    output = capsys.readouterr().out
    assert "archived 1 lessons" in output
    assert not candidate.exists()
    assert (paths["archive"] / f"stale_{today}.1.md").exists()
