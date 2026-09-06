from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import consolidate_memory


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    directory = tmp_path / "lessons"
    directory.mkdir()
    memory = directory / "MEMORY.md"
    monkeypatch.setattr(consolidate_memory, "MEMORY_DIR", directory)
    monkeypatch.setattr(consolidate_memory, "MEMORY_MD", memory)
    return directory, memory


def test_tokens_discovery_and_duplicate_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert consolidate_memory.tokens("The stale context validation rule") == {
        "stale",
        "context",
        "validation",
        "rule",
    }
    generated = tmp_path / "lesson_autogen_stale_context.md"
    generated.write_text("lesson")
    (tmp_path / "stale_context_feedback.md").write_text("hand")
    (tmp_path / "unrelated.md").write_text("hand")
    (tmp_path / "MEMORY.md").write_text("index")
    (tmp_path / "lesson_autogen_skip.md").symlink_to(generated)
    (tmp_path / "linked_feedback.md").symlink_to(tmp_path / "unrelated.md")
    assert consolidate_memory.discover_patterns(tmp_path) == ["stale_context"]
    assert consolidate_memory.find_duplicates(["stale_context", "other"], tmp_path) == [
        ("stale_context", "stale_context_feedback.md")
    ]

    original_glob = Path.glob

    def broken(path: Path, pattern: str):
        if path == tmp_path:
            raise OSError("denied")
        return original_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", broken)
    assert consolidate_memory.discover_patterns(tmp_path) == []
    assert consolidate_memory.find_duplicates(["x"], tmp_path) == []


def test_consolidate_noop_and_idempotent_collapse(tmp_path: Path) -> None:
    plain = "# Memory\n- hand written\n"
    unchanged, info = consolidate_memory.consolidate(plain, tmp_path)
    assert unchanged == plain and info["after_lines"] == info["before_lines"]

    (tmp_path / "lesson_autogen_alpha.md").write_text("a")
    without_index, info = consolidate_memory.consolidate(plain, tmp_path)
    assert without_index == plain and info["patterns"] == ["alpha"]

    (tmp_path / "lesson_autogen_alpha_beta.md").write_text("b")
    (tmp_path / "alpha_beta_feedback.md").write_text("hand")
    original = (
        "# Memory\n"
        "- [Auto-lesson: alpha](lesson_autogen_alpha.md) — old\n"
        "- hand written\n"
        "- [Auto-lesson: alpha_beta](lesson_autogen_alpha_beta.md) — old\n"
    )
    collapsed, info = consolidate_memory.consolidate(original, tmp_path)
    assert collapsed.count("[Auto-lessons (2)]") == 1
    assert "lesson_autogen_alpha.md lesson_autogen_alpha_beta.md" in collapsed
    assert "- hand written" in collapsed
    assert info["autogen_count"] == 2
    assert info["duplicates"] == [("alpha_beta", "alpha_beta_feedback.md")]

    again, again_info = consolidate_memory.consolidate(collapsed, tmp_path)
    assert again == collapsed
    assert again_info["already_collapsed"] is True
    assert again_info["autogen_count"] == 0


def test_backup_paths_are_collision_safe(tmp_path: Path) -> None:
    target = tmp_path / "MEMORY.md"
    first = consolidate_memory.backup_path(target)
    second = consolidate_memory.backup_path(target)
    assert first != second
    assert first.name.startswith("MEMORY.md.bak.")


def test_print_report_covers_noop_duplicates_diff_and_budget(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    consolidate_memory.print_report(
        "same",
        "same",
        {
            "before_lines": 1,
            "after_lines": 1,
            "autogen_count": 0,
            "already_collapsed": True,
            "duplicates": [],
        },
    )
    output = capsys.readouterr().out
    assert "Already consolidated" in output and "No changes" in output and "Budget: OK" in output

    monkeypatch.setattr(consolidate_memory, "BUDGET", 1)
    consolidate_memory.print_report(
        "old\nline",
        "new\nline",
        {
            "before_lines": 2,
            "after_lines": 2,
            "autogen_count": 1,
            "already_collapsed": False,
            "duplicates": [("alpha", "alpha_feedback.md")],
        },
    )
    output = capsys.readouterr().out
    assert "OVER by 1" in output
    assert "Possible duplicates" in output
    assert "--- diff ---" in output


def test_main_dry_run_missing_unsafe_and_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, memory = _configure(tmp_path, monkeypatch)
    assert consolidate_memory.main([]) == 1
    target = tmp_path / "target.md"
    target.write_text("x")
    memory.symlink_to(target)
    assert consolidate_memory.main([]) == 1
    memory.unlink()
    memory.write_text("# Memory\n")
    original = Path.read_text

    def denied(path: Path, *args: object, **kwargs: object) -> str:
        if path == memory:
            raise OSError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert consolidate_memory.main([]) == 1
    assert "Unable to read" in capsys.readouterr().out
    monkeypatch.setattr(Path, "read_text", original)
    assert consolidate_memory.main([]) == 0

    (directory / "lesson_autogen_alpha.md").write_text("lesson")
    memory.write_text("# Memory\n- [Auto-lesson: alpha](lesson_autogen_alpha.md)\n")
    before = memory.read_text()
    assert consolidate_memory.main([]) == 0
    assert memory.read_text() == before
    assert "[dry-run]" in capsys.readouterr().out


def test_main_apply_noop_change_lock_failure_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, memory = _configure(tmp_path, monkeypatch)
    memory.write_text("# Memory\n")
    assert consolidate_memory.main(["--apply"]) == 0
    assert not list(directory.glob("MEMORY.md.bak.*"))

    (directory / "lesson_autogen_alpha.md").write_text("lesson")
    original = "# Memory\n- [Auto-lesson: alpha](lesson_autogen_alpha.md)\n"
    memory.write_text(original)
    assert consolidate_memory.main(["--apply"]) == 0
    backups = list(directory.glob("MEMORY.md.bak.*"))
    assert len(backups) == 1 and backups[0].read_text() == original
    assert "[Auto-lessons (1)]" in memory.read_text()
    assert "Backed up" in capsys.readouterr().out

    memory.write_text(original)

    class FailedLock:
        def __enter__(self):
            raise TimeoutError("busy")

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(consolidate_memory, "exclusive_lock", lambda _path: FailedLock())
    assert consolidate_memory.main(["--apply"]) == 1
    assert memory.read_text() == original
    assert "Unable to consolidate" in capsys.readouterr().out


def test_apply_restores_original_when_live_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, memory = _configure(tmp_path, monkeypatch)
    (directory / "lesson_autogen_alpha.md").write_text("lesson")
    original = "# Memory\n- [Auto-lesson: alpha](lesson_autogen_alpha.md)\n"
    memory.write_text(original)
    real_write = consolidate_memory.atomic_write_text
    failed = False

    def write(path: Path, content: str) -> None:
        nonlocal failed
        if path == memory and not failed:
            failed = True
            raise OSError("disk full")
        real_write(path, content)

    monkeypatch.setattr(consolidate_memory, "atomic_write_text", write)
    assert consolidate_memory.main(["--apply"]) == 1
    assert memory.read_text() == original
