from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import lesson_evolve
from state_io import load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    memory = tmp_path / "lessons"
    signals = tmp_path / "signals"
    diagnostics = tmp_path / "diagnostics"
    backups = tmp_path / "backups"
    for path in (memory, signals, diagnostics):
        path.mkdir(parents=True)
    paths = {
        "memory": memory,
        "scores": tmp_path / "scores.json",
        "variants": signals / "lesson_variants.jsonl",
        "reviews": signals / "pending_human_review.jsonl",
        "diagnostics": diagnostics,
        "backups": backups,
    }
    monkeypatch.setattr(lesson_evolve, "MEMORY_DIR", memory)
    monkeypatch.setattr(lesson_evolve, "SCORES_JSON", paths["scores"])
    monkeypatch.setattr(lesson_evolve, "VARIANTS_FILE", paths["variants"])
    monkeypatch.setattr(lesson_evolve, "REVIEW_FILE", paths["reviews"])
    monkeypatch.setattr(lesson_evolve, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(lesson_evolve, "BACKUP_DIR", backups)
    return paths


def _lesson(path: Path, *, first_seen: bool = False, last_updated: bool = True) -> str:
    lines = ["---", "pattern: target"]
    if first_seen:
        lines.append("first_seen: 2026-08-01")
    if last_updated:
        lines.append("last_updated: 2026-09-01")
    lines.extend(["---", "", "Old rule.", "", "**Why:** evidence", ""])
    text = "\n".join(lines)
    path.write_text(text)
    return text


def _variant(
    *,
    variant_id: int = 0,
    text: str = "Rule: verify | Rationale: prevents drift | Applicability: deployments",
    proposed_at: str = "2026-09-01",
    status: str = "proposed",
    batch: str | None = None,
) -> dict:
    record = {
        "pattern": "target",
        "variant_id": variant_id,
        "text": text,
        "proposed_at": proposed_at,
        "status": status,
    }
    if batch is not None:
        record["batch_id"] = batch
    return record


def test_score_and_date_helpers_normalize_malformed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    paths["scores"].write_text("[]\n")
    assert lesson_evolve.load_scores() == {}
    paths["scores"].write_text(
        json.dumps(
            {
                "scores": {
                    "target": {"verdict": "flat"},
                    "../unsafe": {"verdict": "regressed"},
                    "bad": "not-an-object",
                }
            }
        )
    )
    assert lesson_evolve.load_scores() == {"target": {"verdict": "flat"}}

    assert lesson_evolve.escalation_verdict({"eval_covered": True, "obj_verdict": "regressed"}) == "regressed"
    assert lesson_evolve.escalation_verdict({"judge_covered": True, "judge_verdict": "flat"}) == "flat"
    assert lesson_evolve.escalation_verdict({"verdict": "working"}) == "working"
    assert lesson_evolve.escalation_verdict({"verdict": []}) == ""
    variants = [
        {"pattern": "target", "proposed_at": "bad"},
        {"pattern": "target", "proposed_at": "2026-09-02"},
        {"pattern": "other", "proposed_at": "2026-09-05"},
    ]
    assert lesson_evolve.last_mutation_date(variants, "target") == "2026-09-02"
    assert lesson_evolve.days_since("2026-09-02", "2026-09-06") == 4
    assert lesson_evolve.days_since("bad", "2026-09-06") == 9999


def test_rule_and_candidate_helpers_validate_and_bound_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    _lesson(lesson)
    assert lesson_evolve.current_rule("target") == "Old rule."
    assert lesson_evolve.current_rule("../unsafe") is None
    assert lesson_evolve.current_rule("missing") is None
    assert lesson_evolve.normalize_variant_text("  many   spaces  ") == "many spaces"
    assert lesson_evolve.normalize_variant_text([]) is None
    assert lesson_evolve.valid_generated_variant(
        "Rule: verify | Rationale: safety | Applicability: releases"
    )
    assert not lesson_evolve.valid_generated_variant("Just verify")
    assert lesson_evolve.proposal_id("target", "2026-09-06", 0, "text") == lesson_evolve.proposal_id(
        "target", "2026-09-06", 0, "text"
    )


def test_generate_variants_filters_duplicates_schema_and_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = "\n".join(
        [
            "1. Rule: verify | Rationale: safety | Applicability: deploys",
            "2. missing labels",
            "3. Rule: verify | Rationale: safety | Applicability: deploys",
            "4. Rule: inspect logs | Rationale: diagnosis | Applicability: failures",
        ]
    )
    monkeypatch.setattr(lesson_evolve, "call_llm", lambda *_args, **_kwargs: response)
    assert lesson_evolve.generate_variants("target", "old", 2) == [
        "Rule: verify | Rationale: safety | Applicability: deploys",
        "Rule: inspect logs | Rationale: diagnosis | Applicability: failures",
    ]
    assert lesson_evolve.generate_variants("../bad", "old", 2) == []
    assert lesson_evolve.generate_variants("target", "old", 0) == []
    monkeypatch.setattr(lesson_evolve, "call_llm", lambda *_args, **_kwargs: "")
    assert lesson_evolve.generate_variants("target", "old", 2) == []


def test_main_validates_bounds_and_reports_no_eligible_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    assert lesson_evolve.main(["--n-variants", "0"]) == 2
    assert "between 1" in capsys.readouterr().out
    assert lesson_evolve.main(["--cooldown-days", "-1"]) == 2
    assert "non-negative" in capsys.readouterr().out

    paths["scores"].write_text(json.dumps({"scores": {"target": {"verdict": "working"}}}))
    assert lesson_evolve.main([]) == 0
    assert "No lessons currently" in capsys.readouterr().out
    assert next(paths["diagnostics"].glob("lesson_evolve_*.md")).exists()


def test_main_creates_one_stable_batch_and_pauses_while_review_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    paths["scores"].write_text(
        json.dumps({"scores": {"target": {"eval_covered": True, "obj_verdict": "regressed"}}})
    )
    _lesson(paths["memory"] / "lesson_autogen_target.md")
    candidates = [
        "Rule: verify | Rationale: safety | Applicability: deploys",
        "Rule: inspect logs | Rationale: diagnosis | Applicability: failures",
    ]
    monkeypatch.setattr(lesson_evolve, "generate_variants", lambda *_args, **_kwargs: candidates)

    assert lesson_evolve.main([]) == 0
    records = load_jsonl_objects(paths["variants"]).records
    assert len(records) == 2
    assert len({record["batch_id"] for record in records}) == 1
    assert len({record["proposal_id"] for record in records}) == 2
    assert len(load_jsonl_objects(paths["reviews"]).records) == 1

    assert lesson_evolve.main([]) == 0
    assert len(load_jsonl_objects(paths["variants"]).records) == 2
    assert len(load_jsonl_objects(paths["reviews"]).records) == 1


def test_main_respects_cooldown_and_no_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    paths["scores"].write_text(json.dumps({"scores": {"target": {"verdict": "flat"}}}))
    _lesson(paths["memory"] / "lesson_autogen_target.md")
    lesson_evolve.append_variants([_variant(proposed_at="2999-01-01")])
    assert lesson_evolve.main([]) == 0
    assert len(load_jsonl_objects(paths["variants"]).records) == 1

    paths["variants"].unlink()
    assert lesson_evolve.main(["--no-llm"]) == 0
    assert not paths["reviews"].exists()


def test_apply_variant_uses_latest_batch_and_supersedes_all_stale_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    original = _lesson(paths["memory"] / "lesson_autogen_target.md")
    lesson_evolve.append_variants(
        [
            _variant(text="Old candidate", proposed_at="2026-08-01", batch="old"),
            _variant(text="Latest zero", proposed_at="2026-09-05", batch="latest"),
            _variant(variant_id=1, text="Latest one", proposed_at="2026-09-05", batch="latest"),
            {**_variant(text="Other pattern"), "pattern": "other"},
        ]
    )

    assert lesson_evolve.apply_variant("target", 1, "2026-09-06") is True
    updated = (paths["memory"] / "lesson_autogen_target.md").read_text()
    assert "Latest one" in updated and "Old candidate" not in updated
    records = load_jsonl_objects(paths["variants"]).records
    assert [record["status"] for record in records[:3]] == [
        "superseded",
        "superseded",
        "applied",
    ]
    assert records[3]["status"] == "proposed"
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False
    backups = list(paths["backups"].glob("target_2026-09-06_*.md"))
    assert len(backups) == 1 and backups[0].read_text() == original


def test_apply_variant_rejects_invalid_requests_and_bad_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    assert lesson_evolve.apply_variant("../bad", 0, "2026-09-06") is False
    assert lesson_evolve.apply_variant("target", -1, "2026-09-06") is False
    assert lesson_evolve.apply_variant("target", 0, "bad") is False

    lesson_evolve.append_variants([_variant()])
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False
    lesson = paths["memory"] / "lesson_autogen_target.md"
    lesson.write_text("not frontmatter")
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False
    lesson.write_text("---\nlast_updated: 2026-09-01\n---\n\nOld rule.\n")
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False


def test_apply_variant_restores_lesson_when_ledger_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    original = _lesson(lesson, first_seen=True)
    lesson_evolve.append_variants([_variant()])
    monkeypatch.setattr(
        lesson_evolve,
        "rewrite_jsonl_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )

    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False
    assert lesson.read_text() == original
    assert load_jsonl_objects(paths["variants"]).records[0]["status"] == "proposed"


def test_collision_safe_backups_preserve_every_prior_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    _lesson(lesson)
    selected_id = "a" * 20
    first = lesson_evolve._backup_path("target", "2026-09-06", selected_id)
    first.parent.mkdir(parents=True)
    first.write_text("one")
    second = lesson_evolve._backup_path("target", "2026-09-06", selected_id)
    second.write_text("two")
    third = lesson_evolve._backup_path("target", "2026-09-06", selected_id)
    assert first.name.endswith("aaaaaaaaaaaa.md")
    assert second.name.endswith("aaaaaaaaaaaa.2.md")
    assert third.name.endswith("aaaaaaaaaaaa.3.md")


def test_remaining_lesson_helper_and_generation_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    _lesson(lesson)
    assert lesson_evolve._date_value(None) is None

    original_read_text = Path.read_text

    def unreadable(path: Path, *args: object, **kwargs: object) -> str:
        if path == lesson:
            raise OSError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert lesson_evolve.current_rule("target") is None
    monkeypatch.setattr(Path, "read_text", original_read_text)

    monkeypatch.setattr(lesson_evolve, "call_llm", lambda *_args, **_kwargs: "   ")
    assert lesson_evolve.generate_variants("target", "old", 2) == []
    monkeypatch.setattr(
        lesson_evolve,
        "call_llm",
        lambda *_args, **_kwargs: (
            "Rule: first | Rationale: one | Applicability: all\n"
            "Rule: second | Rationale: two | Applicability: all"
        ),
    )
    assert lesson_evolve.generate_variants("target", "old", 1) == [
        "Rule: first | Rationale: one | Applicability: all"
    ]


def test_main_handles_missing_rule_pending_review_empty_generation_and_known_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    paths["scores"].write_text(json.dumps({"scores": {"target": {"verdict": "flat"}}}))
    assert lesson_evolve.main([]) == 0

    _lesson(paths["memory"] / "lesson_autogen_target.md")
    paths["reviews"].write_text(
        json.dumps({"pattern": "target", "status": "pending", "source": "lesson_evolve"}) + "\n"
    )
    assert lesson_evolve.main(["--cooldown-days", "0"]) == 0
    assert not paths["variants"].exists()

    paths["reviews"].unlink()
    monkeypatch.setattr(lesson_evolve, "generate_variants", lambda *_args, **_kwargs: [])
    assert lesson_evolve.main(["--cooldown-days", "0"]) == 0
    assert not paths["variants"].exists()

    candidate = "Rule: verify | Rationale: safety | Applicability: deploys"
    today = lesson_evolve.datetime.now(lesson_evolve.timezone.utc).strftime("%Y-%m-%d")
    known_id = lesson_evolve.proposal_id("target", today, 0, candidate)
    lesson_evolve.append_variants(
        [{**_variant(text=candidate, proposed_at=today), "proposal_id": known_id}]
    )
    monkeypatch.setattr(
        lesson_evolve, "generate_variants", lambda *_args, **_kwargs: [candidate]
    )
    assert lesson_evolve.main(["--cooldown-days", "0"]) == 0
    assert len(load_jsonl_objects(paths["variants"]).records) == 1
    assert len(load_jsonl_objects(paths["reviews"]).records) == 1


def test_apply_variant_rejects_nonlatest_id_and_handles_unreadable_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    _lesson(lesson)
    lesson_evolve.append_variants(
        [
            _variant(variant_id=0, text="old", proposed_at="2026-08-01", batch="old"),
            _variant(variant_id=1, text="latest", proposed_at="2026-09-01", batch="latest"),
        ]
    )
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False

    original_read_text = Path.read_text

    def unreadable(path: Path, *args: object, **kwargs: object) -> str:
        if path == lesson:
            raise OSError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert lesson_evolve.apply_variant("target", 1, "2026-09-06") is False


def test_apply_variant_preserves_existing_first_seen_and_adds_missing_last_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    lesson.write_text(
        "---\npattern: target\nfirst_seen: 2026-08-01\n---\n\nOld.\n"
    )
    lesson_evolve.append_variants(
        [{**_variant(text="New rule"), "proposal_id": "b" * 20}]
    )
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is True
    updated = lesson.read_text()
    assert updated.count("first_seen:") == 1
    assert "first_seen: 2026-08-01" in updated
    assert "last_updated: 2026-09-06" in updated


def test_apply_variant_reports_critical_restore_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    lesson = paths["memory"] / "lesson_autogen_target.md"
    _lesson(lesson)
    lesson_evolve.append_variants([_variant()])
    monkeypatch.setattr(
        lesson_evolve,
        "rewrite_jsonl_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ledger failed")),
    )
    real_atomic = lesson_evolve.atomic_write_text
    writes_to_lesson = 0

    def fail_restore(path: Path, text: str) -> None:
        nonlocal writes_to_lesson
        if path == lesson:
            writes_to_lesson += 1
            if writes_to_lesson == 2:
                raise OSError("restore failed")
        real_atomic(path, text)

    monkeypatch.setattr(lesson_evolve, "atomic_write_text", fail_restore)
    assert lesson_evolve.apply_variant("target", 0, "2026-09-06") is False
    assert "CRITICAL" in capsys.readouterr().out
