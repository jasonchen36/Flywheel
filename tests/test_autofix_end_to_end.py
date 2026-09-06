from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import skill_autofix
import skill_burnin


@dataclass
class Entry:
    timestamp: str
    rating: float
    skill: str
    sentiment_summary: str = "quality signal"
    response_preview: str = ""
    skill_candidates: list[str] = field(default_factory=list)


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    commands = tmp_path / "commands"
    pi_skills = tmp_path / "pi-skills"
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    for path in (commands, pi_skills, state, diagnostics):
        path.mkdir(parents=True)
    ledger = state / "skill_autofix_ledger.json"
    repo = state / "skillfix_repo"
    ratings = tmp_path / "ratings.jsonl"
    for module in (skill_autofix,):
        monkeypatch.setattr(module, "COMMANDS_DIR", commands)
        monkeypatch.setattr(module, "PI_SKILLS_DIR", pi_skills)
        monkeypatch.setattr(module, "STATE_DIR", state)
        monkeypatch.setattr(module, "LEDGER_FILE", ledger)
        monkeypatch.setattr(module, "SNAP_REPO", repo)
        monkeypatch.setattr(module, "DIAG_DIR", diagnostics)
        monkeypatch.setattr(module, "RATINGS_FILE", ratings)
    monkeypatch.setattr(skill_burnin, "LEDGER_FILE", ledger)
    monkeypatch.setattr(skill_burnin, "DIAG_DIR", diagnostics)
    monkeypatch.setattr(skill_burnin, "RATINGS_FILE", ratings)
    monkeypatch.setattr(skill_burnin, "load_ledger", skill_autofix.load_ledger)
    monkeypatch.setattr(skill_burnin, "save_ledger", skill_autofix.save_ledger)
    monkeypatch.setattr(skill_burnin, "skill_sessions", skill_autofix.skill_sessions)
    monkeypatch.setattr(skill_burnin, "fail_rate", skill_autofix.fail_rate)
    return {
        "commands": commands,
        "ledger": ledger,
        "repo": repo,
        "diagnostics": diagnostics,
    }


def _entries(skill: str, rating: float, count: int, *, day: int = 1) -> list[Entry]:
    return [
        Entry(
            timestamp=f"2026-09-{day + index:02d}T12:00:00Z",
            rating=rating,
            skill=skill,
        )
        for index in range(count)
    ]


def _apply_real_guardrail(
    skill: str,
    paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, dict]:
    live = paths["commands"] / f"{skill}.md"
    original = f"# {skill.title()}\n\nOriginal instructions.\n"
    live.write_text(original)
    monkeypatch.setattr(skill_autofix, "dominant_pattern", lambda _sessions: "tool_misuse")
    monkeypatch.setattr(
        skill_autofix,
        "generate_guardrail",
        lambda *_args, **_kwargs: "- Verify the exact command output before reporting success.",
    )
    ledger: dict = {"edits": [], "invalid_edits": [], "log": []}
    changes: list[str] = []
    skill_autofix.ensure_repo()
    candidates = skill_autofix.propose_new(
        ledger,
        _entries(skill, 2, skill_autofix.MIN_LOW),
        "2026-09-06",
        changes,
        use_llm=True,
        dry_run=False,
    )
    assert candidates and changes[-1].startswith(f"APPLIED /{skill}")
    assert ledger["edits"][0]["status"] == "active"
    assert skill_autofix.START in live.read_text()
    return live, original, ledger


def test_real_autofix_apply_can_be_confirmed_by_shared_burnin_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live, original, ledger = _apply_real_guardrail("deploy", paths, monkeypatch)
    skill_autofix.save_ledger(ledger)
    high = _entries("deploy", 10, skill_autofix.MIN_AFTER)
    monkeypatch.setattr(skill_burnin, "load_all_ratings", lambda _path: high)

    assert skill_burnin.main(["--provisional-measure", "--apply"]) == 0
    saved = skill_autofix.load_ledger()
    assert saved["edits"][0]["status"] == "confirmed"
    assert saved["edits"][0]["confirm_mode"] == "provisional_all_sessions"
    assert live.read_text() != original
    assert next(paths["diagnostics"].glob("skill_burnin_*.md")).exists()


def test_real_autofix_apply_can_be_restored_from_git_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    live, original, ledger = _apply_real_guardrail("review", paths, monkeypatch)
    post = _entries("review", 2, skill_autofix.MIN_AFTER, day=7)
    changes: list[str] = []

    skill_autofix.evaluate_active(ledger, post, "2026-09-20", changes, dry_run=False)
    edit = ledger["edits"][0]
    assert edit["status"] == "reverted"
    assert skill_autofix.COMMIT_RE.fullmatch(str(edit["rollback_commit"]))
    assert live.read_text() == original
    assert changes[0].startswith("REVERTED /review")
    assert skill_autofix.content_at("review", str(edit["rollback_commit"])) == original
