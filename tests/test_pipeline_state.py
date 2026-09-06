from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import judge_outcomes
import lesson_evolve
from state_io import append_jsonl, load_jsonl_objects


def test_judge_drain_preserves_rows_appended_after_initial_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pending = tmp_path / "pending_judge.jsonl"
    monkeypatch.setattr(judge_outcomes, "PENDING_FILE", pending)
    monkeypatch.setattr(judge_outcomes, "QUEUE_CAP", 1000)
    first = {"timestamp": "2026-09-05T10:00:00Z", "session_id": "first"}
    second = {"timestamp": "2026-09-05T10:01:00Z", "session_id": "second"}
    late = {"timestamp": "2026-09-05T10:02:00Z", "session_id": "late"}
    judge_outcomes.write_queue([first, second])
    stale_snapshot = judge_outcomes.read_queue()
    append_jsonl(pending, late)

    remaining = judge_outcomes.drain_queue(
        {judge_outcomes.turn_key(stale_snapshot[0])}
    )
    assert remaining == [second, late]
    assert judge_outcomes.read_queue() == [second, late]


def test_judge_queue_cap_keeps_latest_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pending = tmp_path / "pending_judge.jsonl"
    monkeypatch.setattr(judge_outcomes, "PENDING_FILE", pending)
    monkeypatch.setattr(judge_outcomes, "QUEUE_CAP", 2)
    rows = [{"timestamp": str(index), "session_id": str(index)} for index in range(4)]
    judge_outcomes.write_queue(rows)
    assert judge_outcomes.read_queue() == rows[-2:]


def _configure_lesson_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    variants = tmp_path / "signals" / "lesson_variants.jsonl"
    lessons = tmp_path / "lessons"
    backups = tmp_path / "backups"
    lessons.mkdir()
    monkeypatch.setattr(lesson_evolve, "VARIANTS_FILE", variants)
    monkeypatch.setattr(lesson_evolve, "MEMORY_DIR", lessons)
    monkeypatch.setattr(lesson_evolve, "BACKUP_DIR", backups)
    return variants, lessons


def test_apply_variant_updates_lesson_and_only_matching_ledger_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    variants, lessons = _configure_lesson_paths(tmp_path, monkeypatch)
    lesson_evolve.append_variants(
        [
            {
                "pattern": "target",
                "variant_id": 1,
                "status": "proposed",
                "text": "Use the improved verification rule.",
                "proposed_at": "2026-09-04",
            },
            {
                "pattern": "other",
                "variant_id": 1,
                "status": "proposed",
                "text": "Keep this proposal.",
                "proposed_at": "2026-09-04",
            },
        ]
    )
    lesson = lessons / "lesson_autogen_target.md"
    lesson.write_text(
        "---\npattern: target\nlast_updated: 2026-09-01\n---\n\n"
        "Old rule.\n\n**Why:** evidence\n"
    )

    assert lesson_evolve.apply_variant("target", 1, "2026-09-05") is True
    updated = lesson.read_text()
    assert "Use the improved verification rule." in updated
    assert "first_seen: 2026-09-01" in updated
    assert "last_updated: 2026-09-05" in updated
    records = load_jsonl_objects(variants).records
    assert records[0]["status"] == "applied"
    assert records[1]["status"] == "proposed"
    backups = list((tmp_path / "backups").glob("target_2026-09-05_*.md"))
    assert len(backups) == 1


def test_apply_variant_fails_without_proposal_lesson_or_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, lessons = _configure_lesson_paths(tmp_path, monkeypatch)
    assert lesson_evolve.apply_variant("missing", 1, "2026-09-05") is False

    lesson_evolve.append_variants(
        [{
            "pattern": "target",
            "variant_id": 1,
            "status": "proposed",
            "text": "New rule.",
            "proposed_at": "2026-09-04",
        }]
    )
    assert lesson_evolve.apply_variant("target", 1, "2026-09-05") is False
    (lessons / "lesson_autogen_target.md").write_text("missing frontmatter")
    assert lesson_evolve.apply_variant("target", 1, "2026-09-05") is False
