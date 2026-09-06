from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import intent_how_audit


def procedural(lines: int = 130, *, intent: str = "", constraint: str = "") -> str:
    body = [
        "Overview of the tool and its operating environment.",
        "1. Run exactly this command now.",
        "Follow this step-by-step procedure.",
        "You must first inspect then change then verify.",
        intent,
        constraint,
    ]
    body.extend(f"filler line {index}" for index in range(lines - len(body)))
    return "\n".join(body)


def test_candidate_and_iterator_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "skills"
    nested = root / "nested"
    excluded = root / "node_modules"
    nested.mkdir(parents=True)
    excluded.mkdir()
    skill = nested / "SKILL.md"
    named = root / "review_skill.md"
    ignored = root / "README.md"
    skill.write_text("x" * 100)
    named.write_text("y" * 100)
    ignored.write_text("z" * 100)
    (excluded / "SKILL.md").write_text("bad")
    (root / "linked_skill.md").symlink_to(named)
    assert intent_how_audit.is_candidate(skill)
    assert intent_how_audit.is_candidate(named)
    assert not intent_how_audit.is_candidate(ignored)
    assert set(intent_how_audit.iter_skills([root])) == {skill, named}
    assert list(intent_how_audit.iter_skills([tmp_path / "missing"])) == []
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root)
    assert list(intent_how_audit.iter_skills([linked_root])) == []

    original_is_file = Path.is_file

    def broken_file(path: Path) -> bool:
        if path == named:
            raise OSError("metadata denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", broken_file)
    assert set(intent_how_audit.iter_skills([root])) == {skill}
    monkeypatch.setattr(Path, "is_file", original_is_file)

    original_rglob = Path.rglob

    def broken(path: Path, pattern: str):
        if path == root:
            raise OSError("denied")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", broken)
    assert list(intent_how_audit.iter_skills([root])) == []


def test_score_file_handles_errors_short_monitor_flag_and_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("short")
    assert intent_how_audit.score_file(path) is None
    path.write_text("plain text " * 20)
    assert intent_how_audit.score_file(path) is None

    monitor = procedural(90, intent="Use when the outcome requires acceptance verification.")
    path.write_text(monitor)
    scored = intent_how_audit.score_file(path)
    assert scored is not None and scored["flagged"] is False
    assert scored["recommendation"] == "monitor"

    path.write_text(procedural())
    flagged = intent_how_audit.score_file(path)
    assert flagged is not None and flagged["flagged"] is True
    assert flagged["how_hits"] >= 3

    path.write_text(procedural(constraint="Never post without approval; confirm blast radius."))
    protected = intent_how_audit.score_file(path)
    assert protected is not None and protected["flagged"] is False
    assert protected["constraint_hits"] == 2

    original = Path.read_text

    def denied(target: Path, *args: object, **kwargs: object) -> str:
        if target == path:
            raise OSError("denied")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert intent_how_audit.score_file(path) is None


def test_build_and_render_report_are_bounded_and_deterministic() -> None:
    items: list[intent_how_audit.AuditItem] = [
        {
            "path": "b",
            "lines": 100,
            "how_hits": 3,
            "intent_hits": 0,
            "constraint_hits": 0,
            "recommendation": "review",
            "flagged": True,
        },
        {
            "path": "a",
            "lines": 100,
            "how_hits": 3,
            "intent_hits": 0,
            "constraint_hits": 0,
            "recommendation": "review",
            "flagged": True,
        },
        {
            "path": "monitor",
            "lines": 90,
            "how_hits": 2,
            "intent_hits": 1,
            "constraint_hits": 0,
            "recommendation": "monitor",
            "flagged": False,
        },
    ]
    report = intent_how_audit.build_report(
        items, 4, datetime(2026, 1, 2, tzinfo=timezone.utc), 1
    )
    assert report["flagged"] == 2
    assert report["items"][0]["path"] == "a"
    assert report["remaining_flagged"] == 1
    report["items"] = ["bad", report["items"][0]]
    markdown = intent_how_audit.render_markdown(report, "2026-01-02", 5)
    assert "`a`" in markdown
    assert "1 additional flagged" in markdown
    report["items"] = "bad"
    report["remaining_flagged"] = "bad"
    assert "`a`" not in intent_how_audit.render_markdown(report, "2026-01-02", 5)


def test_main_writes_stable_outputs_and_supports_json_and_human_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "SKILL.md").write_text(procedural())
    (root / "short_skill.md").write_text("short")
    diagnostics = tmp_path / "diagnostics"
    monkeypatch.setattr(intent_how_audit, "ROOTS", [root, root])
    monkeypatch.setattr(intent_how_audit, "DIAG", diagnostics)

    assert intent_how_audit.main(["--limit", "0"]) == 2
    capsys.readouterr()
    assert intent_how_audit.main(["--json", "--limit", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scanned_unique"] == 2 and report["flagged"] == 1
    assert (diagnostics / "intent_how_audit_latest.json").exists()
    assert (diagnostics / "intent_how_audit_latest.md").exists()
    assert len(list(diagnostics.glob("intent_how_audit_????-??-??.json"))) == 1

    assert intent_how_audit.main([]) == 0
    assert "flagged=1 report=" in capsys.readouterr().out
