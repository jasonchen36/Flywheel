from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import lesson_dedup
from review_store import load_reviews


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    lessons = tmp_path / "lessons"
    diagnostics = tmp_path / "diagnostics"
    review = tmp_path / "signals" / "reviews.jsonl"
    backups = tmp_path / "state" / "backups"
    lessons.mkdir()
    monkeypatch.setattr(lesson_dedup, "MEMORY_DIR", lessons)
    monkeypatch.setattr(lesson_dedup, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(lesson_dedup, "REVIEW_FILE", review)
    monkeypatch.setattr(lesson_dedup, "BACKUP_DIR", backups)
    return {
        "lessons": lessons,
        "diagnostics": diagnostics,
        "review": review,
        "backups": backups,
    }


def _lesson(
    directory: Path,
    pattern: str,
    rule: str,
    *,
    count: int = 1,
    frontmatter_pattern: str | None = None,
) -> Path:
    path = directory / f"lesson_autogen_{pattern}.md"
    path.write_text(
        "---\n"
        f"pattern: {frontmatter_pattern or pattern}\n"
        f"occurrence_count: {count}\n"
        "avg_rating: 2.5\n"
        "---\n\n"
        f"{rule}\n\nWhy: evidence\n"
    )
    return path


def test_parsing_tokens_and_similarity_rules_use_safe_filename_identity(tmp_path: Path):
    path = _lesson(
        tmp_path,
        "safe_name",
        "Verify deployment evidence before declaring success.",
        count=3,
        frontmatter_pattern="../../unsafe",
    )
    parsed = lesson_dedup.parse_lesson(path)
    assert parsed["pattern"] == "safe_name"
    assert parsed["occurrence_count"] == 3
    assert parsed["avg_rating"] == 2.5
    assert parsed["rule"] == "Verify deployment evidence before declaring success."
    assert lesson_dedup.is_template_rule("Avoid mistakes — verify before acting.") is True
    assert lesson_dedup.is_template_rule(parsed["rule"]) is False
    assert lesson_dedup.tokens("Verify unique deployment evidence") == {"unique", "deployment", "evidence"}
    assert lesson_dedup.jaccard(set(), {"a"}) == 0.0
    assert lesson_dedup.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_find_merge_candidates_handles_template_and_semantic_rules():
    lessons = [
        {"pattern": "variable_name_error", "occurrence_count": 5, "rule": "Avoid variable name error — verify before acting."},
        {"pattern": "variable_naming_error", "occurrence_count": 2, "rule": "Avoid variable naming error — verify before acting."},
        {"pattern": "proof_a", "occurrence_count": 1, "rule": "Capture deployment command output and evidence."},
        {"pattern": "proof_b", "occurrence_count": 4, "rule": "Capture deployment command output and evidence."},
        {"pattern": "other", "occurrence_count": 9, "rule": "Discuss unrelated architecture constraints."},
    ]
    candidates = lesson_dedup.find_merge_candidates(lessons, 0.3)
    pairs = {(row["survivor"], row["loser"]) for row in candidates}
    assert ("variable_name_error", "variable_naming_error") in pairs
    assert ("proof_b", "proof_a") in pairs
    assert candidates == sorted(candidates, key=lambda row: -row["score"])
    assert lesson_dedup.find_merge_candidates(lessons, 1.1) == []


@pytest.mark.parametrize("threshold", ["-0.1", "1.1"])
def test_main_rejects_invalid_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    threshold: str,
):
    _configure(tmp_path, monkeypatch)
    assert lesson_dedup.main(["--threshold", threshold]) == 2
    assert "between 0 and 1" in capsys.readouterr().out


def test_main_reports_and_transactionally_queues_candidates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    rule = "Capture concrete deployment output and preserve verification evidence."
    _lesson(paths["lessons"], "first", rule, count=5)
    _lesson(paths["lessons"], "second", rule, count=2)

    assert lesson_dedup.main([]) == 0
    assert "Re-run with --apply" in capsys.readouterr().out
    assert load_reviews(paths["review"]) == []
    assert list(paths["diagnostics"].glob("lesson_dedup_*.md"))

    assert lesson_dedup.main(["--apply"]) == 0
    assert "Queued 1 merge candidate" in capsys.readouterr().out
    reviews = load_reviews(paths["review"])
    assert [row["pattern"] for row in reviews] == ["first<-second"]
    assert lesson_dedup.main(["--apply"]) == 0
    assert "All candidates already queued" in capsys.readouterr().out
    assert len(load_reviews(paths["review"])) == 1


def test_main_no_candidates_writes_report_without_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _lesson(paths["lessons"], "only", "Unique behavior with no pair.")
    assert lesson_dedup.main(["--apply"]) == 0
    assert "No merge candidates" in capsys.readouterr().out
    assert not paths["review"].exists()


def test_merge_rejects_unsafe_same_and_missing_patterns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _lesson(paths["lessons"], "one", "Rule one.")
    assert lesson_dedup.valid_pattern("safe_name-1") is True
    assert lesson_dedup.valid_pattern("../unsafe") is False
    assert lesson_dedup.merge_lessons("../unsafe", "one", "2026-09-05") is False
    assert lesson_dedup.merge_lessons("one", "one", "2026-09-05") is False
    assert lesson_dedup.merge_lessons("one", "missing", "2026-09-05") is False
    output = capsys.readouterr().out
    assert "invalid lesson pattern" in output
    assert "must differ" in output
    assert "missing file" in output


def test_merge_is_atomic_collision_safe_and_recovers_prior_partial_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    survivor = _lesson(paths["lessons"], "survivor", "Verify output evidence.", count=5)
    loser = _lesson(paths["lessons"], "loser", "Capture command proof.", count=2)
    paths["backups"].mkdir(parents=True)
    (paths["backups"] / "loser_2026-09-05.md").write_text("existing")

    assert lesson_dedup.merge_lessons("survivor", "loser", "2026-09-05") is True
    text = survivor.read_text()
    assert "occurrence_count: 7" in text
    assert "Merged from lesson_autogen_loser.md" in text
    assert not loser.exists()
    assert (paths["backups"] / "loser_2026-09-05.1.md").exists()
    assert "Loser file deleted" in capsys.readouterr().out

    # Simulate an interrupted prior run where the survivor write completed but loser removal did not.
    loser = _lesson(paths["lessons"], "loser", "Capture command proof.", count=2)
    assert lesson_dedup.merge_lessons("survivor", "loser", "2026-09-05") is True
    assert not loser.exists()
    assert survivor.read_text() == text
    assert "Completed prior merge" in capsys.readouterr().out


def test_merge_legacy_survivor_without_occurrence_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    survivor = paths["lessons"] / "lesson_autogen_survivor.md"
    survivor.write_text("---\npattern: survivor\n---\n\nPreserve legacy evidence.\n")
    loser = _lesson(paths["lessons"], "loser", "Capture command proof.", count=2)

    assert lesson_dedup.merge_lessons("survivor", "loser", "2026-09-05") is True
    assert "occurrence_count" not in survivor.read_text()
    assert "Merged from lesson_autogen_loser.md" in survivor.read_text()
    assert not loser.exists()
