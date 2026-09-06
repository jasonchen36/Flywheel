from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import ace_playbook
import ace_reflector
from state_io import atomic_write_json


def _lesson(directory: Path, pattern: str, body: str, metadata: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"lesson_autogen_{pattern}.md"
    path.write_text(f"---\n{metadata}---\n\n{body}\n")
    return path


def test_reflector_weakness_quality_and_sections() -> None:
    weak = ace_reflector.is_weak_rule
    assert weak("")
    assert weak("Avoid missed context — verify before acting.")
    assert weak("Avoid mistakes")
    assert weak("Too short")
    assert weak("Avoid a very specific workflow issue and verify before acting with care")
    assert weak("Be careful when changing this value because it may be wrong")
    assert not weak("Never claim complete without running `pytest` and showing the exit code first.")

    score = ace_reflector.quality_score
    assert score("") == 0
    assert score("Avoid foo — verify before acting.") == 0
    assert score("Use a sufficiently detailed and concrete sentence about system behavior.") == 1
    assert score("Never claim done without `pytest`; run it before completion and show the exit code.") == 4

    assert ace_reflector._classify_section("known", "anything") == "strategy"
    assert ace_reflector._classify_section("custom", "Formula order: run bq query --dry-run") == "formula"
    assert ace_reflector._classify_section("custom", "Never skip this concrete verification step") == "pitfall"
    assert ace_reflector._classify_section("custom_failure", "A detailed neutral description of behavior") == "pitfall"
    assert ace_reflector._classify_section("custom", "A detailed neutral description of behavior") == "strategy"
    assert ace_reflector._pattern_phrase("some_pattern") == "some pattern"


def test_reflector_extracts_structured_fields_and_evidence() -> None:
    text = """---\npattern: sample\n---\n\n# Heading\n<!-- note -->\n**Root cause:** Missing actual state validation.\n**Where it happens:** deploy workflow\nNever publish without `pytest`; run it before completion.\n- [1] First evidence line\n- [22] Second evidence line\n- not numbered\n"""
    fields = ace_reflector.extract_structured_fields(text)
    assert fields["rule"] == "Never publish without `pytest`; run it before completion."
    assert fields["root_cause"] == "Missing actual state validation."
    assert fields["where"] == "deploy workflow"
    assert fields["evidence"] == ["First evidence line", "Second evidence line"]
    assert ace_reflector.extract_evidence_lines("\n".join(f"- [{i}] row {i}" for i in range(8)), 2) == [
        "row 0",
        "row 1",
    ]
    plain = ace_reflector.extract_structured_fields("A plain but sufficiently detailed rule without frontmatter")
    assert plain["rule"].startswith("A plain")


def test_evidence_distillation_and_heuristic_families() -> None:
    distill = ace_reflector._evidence_distill
    assert distill("x", []) is None
    assert distill("x", ["User forbade posting without permission"]).startswith("Draft")
    assert "APPROVED" in distill("x", ["The PR was already approved and got a second approval"])
    assert "Global replace" in distill("x", ["Changed SRE globally despite DBRE scope"])
    assert "Never re-run" in distill("x", ["Repeated the same failed command again"])
    assert "session amnesia" in distill("x", ["Claimed amnesia between sessions instead of retaining rules"])
    assert "Never claim" in distill("x", ["Claimed done without testing; unverified result"])
    assert distill("x", ["angry mild"]) is None
    correction = "Never publish a result without checking the exact output from the required tool first."
    assert distill("x", [correction]) == correction
    assert distill("x", ["A long narrative describing an issue without a direct instruction or known signal."]) is None

    heuristic = ace_reflector._heuristic_from_pattern
    assert "explicitly cover" in heuristic("missed_edge_case")
    assert "Do not claim done" in heuristic("missing_validation")
    assert "Never assert" in heuristic("unverified_state")
    assert "concrete check" in heuristic("context_doubt")
    assert "Caveman lite" in heuristic("robotic_tone")
    assert "Draft" in heuristic("permission_violation")
    assert "No identical" in heuristic("blind_retry")
    assert "Measure" in heuristic("performance_regression")
    assert "gather tool evidence" in heuristic("other_pattern")


def test_reflect_lesson_uses_each_quality_source(monkeypatch: pytest.MonkeyPatch) -> None:
    good = "Never claim done without `pytest`; run it before completing and show the exit code."
    passthrough = ace_reflector.reflect_lesson("custom", good, ["evidence"])
    assert passthrough.source == "passthrough" and passthrough.quality >= 3
    assert passthrough.as_dict()["pattern"] == "custom"

    bank = ace_reflector.reflect_lesson("blind_retry", "")
    assert bank.source == "bank" and bank.notes == "pattern bank"

    evidence = ace_reflector.reflect_lesson(
        "custom",
        "Avoid custom — verify before acting.",
        ["Never post a result without checking the exact live status output first."],
    )
    assert evidence.source == "evidence"

    root = ace_reflector.reflect_lesson(
        "custom",
        "Avoid custom — verify before acting.",
        root_cause="The system relied on stale cached state instead of the current source of truth.",
    )
    assert root.source == "evidence" and root.notes == "root_cause"
    imperative_root = ace_reflector.reflect_lesson(
        "custom",
        "Avoid custom — verify before acting.",
        root_cause="Never trust cached state without reading the current source of truth first.",
    )
    assert imperative_root.source == "evidence"

    monkeypatch.setattr(ace_reflector, "_reflect_llm", lambda *_args: "Never publish without `pytest`; run it first.")
    llm = ace_reflector.reflect_lesson("custom", "", use_llm=True)
    assert llm.source == "llm"
    monkeypatch.setattr(ace_reflector, "_reflect_llm", lambda *_args: None)
    heuristic = ace_reflector.reflect_lesson("custom", "", where="release flow", use_llm=True)
    assert heuristic.source == "heuristic" and "Hotspots" in heuristic.description

    middle = ace_reflector.reflect_lesson(
        "custom",
        "Always use a sufficiently detailed concrete workflow that prevents accidental release mistakes.",
    )
    assert middle.source == "passthrough"


def test_reflect_llm_handles_import_call_and_response_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def missing(name: str, *args: object, **kwargs: object):
        if name == "self_improve":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    assert ace_reflector._reflect_llm("x", "", [], "") is None
    monkeypatch.setattr(builtins, "__import__", original_import)

    import self_improve

    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert ace_reflector._reflect_llm("x", "", ["one"], "root") is None
    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: "")
    assert ace_reflector._reflect_llm("x", "", [], "") is None
    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: "- Never guess; run `pytest` first.\nextra")
    assert ace_reflector._reflect_llm("x", "", [], "") == "Never guess; run `pytest` first."
    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: "-   ")
    assert ace_reflector._reflect_llm("x", "", [], "") is None


def test_fallback_file_reflection_and_self_test(capsys: pytest.CaptureFixture[str]) -> None:
    entries = [
        "User forbade this action without permission",
        SimpleNamespace(sentiment_summary="Summary", comment="Comment"),
        SimpleNamespace(sentiment_summary="", comment=""),
    ]
    rule = ace_reflector.fallback_rule_from_examples("acting_without_permission", entries)
    assert rule.startswith("Draft")
    reflected = ace_reflector.reflect_from_lesson_file(
        "---\n---\nNever claim done without `pytest`; run it first and show output.\n",
        "custom",
    )
    assert reflected.pattern == "custom"
    assert ace_reflector.self_test() == 0
    assert "PASS" in capsys.readouterr().out


def test_reflector_main_validates_and_tolerates_unreadable_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ace_reflector, "LESSONS_DIR", tmp_path / "missing")
    assert ace_reflector.main(["--max", "0"]) == 2
    assert ace_reflector.main([]) == 1
    lessons = tmp_path / "lessons"
    good = _lesson(lessons, "good", "Never claim done without `pytest`; run it first and show output.")
    bad = _lesson(lessons, "bad", "Avoid bad — verify before acting.")
    monkeypatch.setattr(ace_reflector, "LESSONS_DIR", lessons)
    original = Path.read_text

    def read(path: Path, *args: object, **kwargs: object) -> str:
        if path == bad:
            raise OSError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert ace_reflector.main(["--dry-run", "--max", "1"]) == 0
    assert good.name in {p.name for p in lessons.iterdir()}
    assert "unreadable lesson" in capsys.readouterr().out
    assert ace_reflector.main(["--self-test"]) == 0


def test_playbook_numeric_score_and_lesson_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for value in (True, None, object(), "bad", float("inf"), -2):
        assert ace_playbook.safe_nonnegative_int(value) == 0
    assert ace_playbook.safe_nonnegative_int("3") == 3
    for value in (True, None, object(), "bad", float("inf")):
        assert ace_playbook.safe_finite_float(value) == 0.0
    assert ace_playbook.safe_finite_float("2.5") == 2.5

    scores = tmp_path / "scores.json"
    monkeypatch.setattr(ace_playbook, "SCORES_FILE", scores)
    assert ace_playbook.load_scores() == {}
    scores.write_text("[]")
    assert ace_playbook.load_scores() == {}
    atomic_write_json(scores, {"scores": {"good": {"verdict": "flat"}, "": {}, "bad": []}})
    assert ace_playbook.load_scores() == {"good": {"verdict": "flat"}}

    monkeypatch.setattr(ace_playbook, "LESSONS_DIR", tmp_path / "missing")
    assert ace_playbook.load_lessons() == []
    lessons = tmp_path / "lessons"
    _lesson(
        lessons,
        "good",
        "Never claim done without `pytest`; run it first and show output.",
        "occurrence_count: 9\navg_rating: 2.5\n",
    )
    unreadable = _lesson(lessons, "unreadable", "Rule")
    monkeypatch.setattr(ace_playbook, "LESSONS_DIR", lessons)
    original = Path.read_text

    def read(path: Path, *args: object, **kwargs: object) -> str:
        if path == unreadable:
            raise OSError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    loaded = ace_playbook.load_lessons()
    assert loaded[0]["occurrence_count"] == 9
    assert loaded[0]["avg_rating"] == 2.5
    assert "unreadable lesson" in capsys.readouterr().out


def _bullet(pattern: str, description: str, **extra: object) -> dict:
    return {
        "id": ace_playbook.bullet_id(pattern, description),
        "pattern": pattern,
        "description": description,
        "helpful": 1,
        "harmful": 0,
        "quality": 2,
        "priority": 0,
        "verdict": "flat",
        "section": "strategy",
        **extra,
    }


def test_playbook_token_dedupe_sludge_and_injection() -> None:
    assert ace_playbook.tokenize("This verifies concrete output after tests") == {"verifies", "concrete", "output", "tests"}
    assert ace_playbook.jaccard(set(), {"x"}) == 0.0
    assert ace_playbook.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert ace_playbook.is_sludge("")
    assert ace_playbook.is_sludge("too short")
    assert ace_playbook.is_sludge("Avoid anything — verify before acting.")
    assert not ace_playbook.is_sludge("Never publish without running the exact required verification command first.")

    inject = ace_playbook._injection_section
    assert inject("flat", "strategy", 4, 2, "Avoid x — verify before acting.") == "deferred"
    assert inject("flat", "strategy", 1, 2, "A sufficiently detailed and concrete instruction for the workflow.") == "deferred"
    assert inject("pending", "strategy", 4, 2, "A sufficiently detailed and concrete instruction for the workflow.") == "deferred"
    assert inject("working", "strategy", 4, 2, "A sufficiently detailed and concrete instruction for the workflow.") == "resolved"
    assert inject("flat", "pitfall", 4, 2, "A sufficiently detailed and concrete instruction for the workflow.") == "pitfall"
    assert inject("flat", "unknown", 4, 2, "A sufficiently detailed and concrete instruction for the workflow.") == "strategy"

    same_low = _bullet("same", "Never publish without running command alpha first.", quality=2)
    same_high = _bullet("same", "Never publish without running `pytest` and showing output first.", quality=4)
    same = ace_playbook.dedupe_bullets([same_low, same_high])
    assert len(same) == 1 and same[0]["quality"] == 4
    assert same[0]["aliases"] == ["same"]

    near_low = _bullet("scope_alpha", "Never publish output without running exact validation command alpha first.", quality=2)
    near_high = _bullet("scope_beta", "Never publish output without running exact validation command beta first.", quality=4)
    merged = ace_playbook.dedupe_bullets([near_low, near_high], threshold=0.4)
    assert len(merged) == 1
    assert merged[0]["helpful"] == 2

    seed = _bullet("same", "Never publish without running `pytest` and showing output first.", seed=True, quality=4)
    assert ace_playbook.dedupe_bullets([same_high, seed])[0]["seed"] is True


def test_build_playbook_handles_signals_stats_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    lessons = [
        {
            "pattern": "custom_flat",
            "rule": "Never publish without running `pytest` and showing exact output first.",
            "occurrence_count": 12,
            "avg_rating": 2.0,
            "quality": 4,
            "reflect_source": "evidence",
            "section": "pitfall",
            "weak_input": True,
        },
        {
            "pattern": "custom_pending",
            "rule": "A sufficiently detailed but still pending workflow instruction for later use.",
            "occurrence_count": 12,
            "avg_rating": 4.0,
            "quality": 2,
            "reflect_source": "passthrough",
            "section": "bad-section",
            "weak_input": False,
        },
        {
            "pattern": "custom_sludge",
            "rule": "Avoid anything — verify before acting.",
            "occurrence_count": "bad",
            "avg_rating": 1,
            "quality": "bad",
            "reflect_source": "heuristic",
            "section": "strategy",
            "weak_input": True,
        },
        {
            "pattern": "unverified_completion",
            "rule": "Never claim done without extra evidence and a command.",
            "occurrence_count": 4,
            "avg_rating": 1,
            "quality": 3,
            "reflect_source": "bank",
            "section": "strategy",
            "weak_input": False,
        },
    ]
    scores = {
        "unverified_completion": {"verdict": "pending", "obj_verdict": "regressed"},
        "incomplete_analysis": {"verdict": "pending", "judge_verdict": "working"},
        "custom_flat": {"verdict": "flat", "injectable": True, "delta": 0.2},
        "custom_pending": {"verdict": "pending", "injectable": False},
        "custom_sludge": {"verdict": "pending", "judge_verdict": "flat"},
    }
    monkeypatch.setattr(ace_playbook, "load_lessons", lambda use_llm=False: lessons)
    monkeypatch.setattr(ace_playbook, "load_scores", lambda: scores)
    playbook = ace_playbook.build_playbook(100, min_quality=2)
    by_pattern = {row["pattern"]: row for row in playbook["bullets"]}
    assert by_pattern["unverified_completion"]["verdict"] == "regressed"
    assert by_pattern["incomplete_analysis"]["verdict"] == "working"
    assert by_pattern["custom_flat"]["section"] == "pitfall"
    assert by_pattern["custom_flat"]["helpful"] >= 4
    assert by_pattern["custom_pending"]["section"] == "deferred"
    assert by_pattern["custom_sludge"]["section"] == "deferred"
    assert playbook["stats"]["weak_input"] == 2
    assert playbook["stats"]["deferred_pending"] == 1
    assert playbook["stats"]["deferred_sludge"] == 1
    assert playbook["sections"]["pitfall"]

    limited = ace_playbook.build_playbook(3, min_quality=2)
    assert limited["bullet_count"] == 3
    assert all(row.get("seed") for row in limited["bullets"])
    unlimited = ace_playbook.build_playbook(0, min_quality=2)
    assert unlimited["bullet_count"] >= len(ace_playbook.SEED_BULLETS)


def test_render_and_main_validate_dry_run_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bullets = [
        _bullet("strategy", "Never publish without running `pytest` and showing output first.", section="strategy"),
        _bullet("pitfall", "Never repeat a failed action without new diagnostic evidence first.", section="pitfall"),
        _bullet("formula", "Before release, run the exact dry-run formula and inspect output.", section="formula"),
        _bullet("resolved", "Always keep this resolved instruction for matched tasks only.", section="resolved"),
        _bullet("deferred", "A sufficiently detailed deferred instruction for later review.", section="deferred"),
    ]
    playbook = {
        "bullet_count": len(bullets),
        "generated_at": "2026-01-01T00:00:00Z",
        "stats": {"weak_input": 0, "reflected": 0, "avg_quality": 3, "weak_output": 0, "by_source": {}},
        "bullets": bullets,
        "sections": {name: [row["id"] for row in bullets if row["section"] == name] for name in ("strategy", "pitfall", "formula", "resolved", "deferred")},
    }
    rendered = ace_playbook.render_md(playbook)
    for heading in ("Strategies", "Pitfalls", "Formulas", "Resolved", "Deferred"):
        assert heading in rendered

    monkeypatch.setattr(ace_playbook, "build_playbook", lambda *_args, **_kwargs: playbook)
    out_json = tmp_path / "state" / "ace.json"
    out_md = tmp_path / "state" / "ace.md"
    diag = tmp_path / "diag"
    monkeypatch.setattr(ace_playbook, "OUT_JSON", out_json)
    monkeypatch.setattr(ace_playbook, "OUT_MD", out_md)
    monkeypatch.setattr(ace_playbook, "DIAG", diag)
    assert ace_playbook.main(["--max", "0"]) == 2
    assert ace_playbook.main(["--min-quality", "5"]) == 2
    assert ace_playbook.main(["--dry-run"]) == 0
    assert not out_json.exists()
    assert ace_playbook.main([]) == 0
    assert json.loads(out_json.read_text())["bullet_count"] == 5
    assert out_md.read_text() == rendered
    assert len(list(diag.glob("ace_playbook_*.md"))) == 1
    assert "Wrote" in capsys.readouterr().out

    playbook["stats"]["weak_output"] = 1
    assert ace_playbook.main(["--dry-run"]) == 0
    assert "WARNING" in capsys.readouterr().out


def test_reflector_residual_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ace_reflector._classify_section("blind_retry", "neutral") == "pitfall"
    assert ace_reflector.extract_evidence_lines("- [1]\n- [2] useful") == ["useful"]
    empty_fields = ace_reflector.extract_structured_fields("# Heading\n**Root cause:** short\n<!-- only metadata -->")
    assert empty_fields["rule"] == ""
    weak_root = ace_reflector.reflect_lesson(
        "custom",
        "",
        root_cause="Do something sufficiently descriptive yet lacking specificity and actionable evidence for the situation.",
    )
    assert weak_root.source == "heuristic"
    no_hotspot = ace_reflector.reflect_lesson("unknown_case", "")
    assert no_hotspot.source == "heuristic" and "Hotspots" not in no_hotspot.description

    original_weak = ace_reflector.is_weak_rule
    monkeypatch.setattr(ace_reflector, "is_weak_rule", lambda _value: False)
    assert ace_reflector.self_test() == 1
    monkeypatch.setattr(ace_reflector, "is_weak_rule", original_weak)
    assert "FAIL" in capsys.readouterr().out

    lessons = tmp_path / "lessons"
    lessons.mkdir()
    monkeypatch.setattr(ace_reflector, "LESSONS_DIR", lessons)
    assert ace_reflector.main([]) == 0
    for index in range(4):
        _lesson(lessons, f"case_{index}", "Avoid custom — verify before acting.")
    assert ace_reflector.main(["--max", "1"]) == 0
    assert "scanned=1" in capsys.readouterr().out


def test_playbook_residual_loading_and_dedupe_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lessons = tmp_path / "lessons"
    plain = _lesson(lessons, "plain", "# Heading\n**Root cause:** metadata only")
    monkeypatch.setattr(ace_playbook, "LESSONS_DIR", lessons)
    weak_ref = ace_reflector.ReflectedBullet(
        pattern="plain",
        description="Avoid plain — verify before acting.",
        source="passthrough",
        weak_input=True,
    )
    repaired = ace_reflector.ReflectedBullet(
        pattern="plain",
        description="Never publish without running `pytest` and showing output first.",
        quality=4,
        source="heuristic",
    )
    monkeypatch.setattr(ace_playbook, "reflect_from_lesson_file", lambda *_args, **_kwargs: weak_ref)
    monkeypatch.setattr(ace_playbook, "reflect_lesson", lambda *_args, **_kwargs: repaired)
    loaded = ace_playbook.load_lessons(use_llm=True)
    assert loaded[0]["occurrence_count"] == 0
    assert loaded[0]["avg_rating"] == 0.0
    assert loaded[0]["raw_rule"] == ""
    assert loaded[0]["rule"] == repaired.description
    assert plain.exists()

    same_low = _bullet(
        "same",
        "Never publish without running command alpha first and checking the result.",
        quality=2,
        priority=100,
    )
    same_high = _bullet(
        "same",
        "Never publish without running `pytest` and showing the exact output first.",
        quality=4,
        priority=0,
    )
    same = ace_playbook.dedupe_bullets([same_low, same_high])
    assert same[0]["description"] == same_high["description"]

    near_low = _bullet(
        "scope_alpha",
        "Never publish output without running exact validation command alpha first.",
        quality=2,
        priority=100,
    )
    near_high = _bullet(
        "scope_beta",
        "Never publish output without running exact validation command beta first.",
        quality=4,
        priority=0,
    )
    near = ace_playbook.dedupe_bullets([near_low, near_high], threshold=0.4)
    assert near[0]["quality"] == 4
    assert near[0]["id"] == ace_playbook.bullet_id("scope_alpha", near_high["description"])


def test_playbook_residual_signal_and_render_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    lessons = [
        {
            "pattern": "injectable_alt",
            "rule": "Never publish without running `pytest` and showing exact output first.",
            "occurrence_count": 6,
            "avg_rating": 2,
            "quality": 3,
            "reflect_source": "bank",
            "section": "strategy",
            "weak_input": False,
        },
        {
            "pattern": "valid_false",
            "rule": "Never repeat a failed action without gathering different diagnostic evidence first.",
            "occurrence_count": 6,
            "avg_rating": 2,
            "quality": 2,
            "reflect_source": "evidence",
            "section": "pitfall",
            "weak_input": False,
        },
    ]
    scores = {
        "unverified_completion": {"verdict": "flat"},
        "injectable_alt": {"injectable": True, "verdict": "pending", "judge_verdict": "working"},
        "valid_false": {"injectable": False, "verdict": "flat"},
    }
    monkeypatch.setattr(ace_playbook, "load_lessons", lambda use_llm=False: lessons)
    monkeypatch.setattr(ace_playbook, "load_scores", lambda: scores)
    playbook = ace_playbook.build_playbook(100)
    rows = {row["pattern"]: row for row in playbook["bullets"]}
    assert rows["injectable_alt"]["verdict"] == "working"
    assert rows["injectable_alt"]["helpful"] >= 3
    assert rows["valid_false"]["section"] == "pitfall"

    one = _bullet(
        "only",
        "Never publish without running `pytest` and showing exact output first.",
        section="strategy",
    )
    rendered = ace_playbook.render_md(
        {
            "bullet_count": 1,
            "generated_at": "now",
            "stats": {},
            "bullets": [one],
        }
    )
    assert "Strategies" in rendered and "Pitfalls" not in rendered


def test_ace_final_branch_arcs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lessons_dir = tmp_path / "reflector-lessons"
    _lesson(lessons_dir, "one", "Never claim done without `pytest`; run it and show output first.")
    _lesson(lessons_dir, "two", "Never publish without `pytest`; run it and show output first.")
    monkeypatch.setattr(ace_reflector, "LESSONS_DIR", lessons_dir)
    assert ace_reflector.main(["--max", "3"]) == 0
    assert "scanned=2" in capsys.readouterr().out

    lessons = [
        {
            "pattern": "explicit_missing",
            "rule": "Never publish without running `pytest` and showing exact output first.",
            "occurrence_count": 0,
            "avg_rating": 0,
            "quality": 4,
            "reflect_source": "passthrough",
            "section": "strategy",
            "weak_input": False,
        },
        {
            "pattern": "implicit_missing",
            "rule": "Before changing database state, inspect the migration plan and preserve a rollback artifact.",
            "occurrence_count": 0,
            "avg_rating": 0,
            "quality": 4,
            "reflect_source": "passthrough",
            "section": "strategy",
            "weak_input": False,
        },
        {
            "pattern": "quality_only",
            "rule": "Always preserve a sufficiently detailed and concrete workflow description for review.",
            "occurrence_count": 0,
            "avg_rating": 0,
            "quality": 1,
            "reflect_source": "passthrough",
            "section": "strategy",
            "weak_input": False,
        },
    ]
    monkeypatch.setattr(ace_playbook, "load_lessons", lambda use_llm=False: lessons)
    monkeypatch.setattr(
        ace_playbook,
        "load_scores",
        lambda: {
            "explicit_missing": {"injectable": True, "verdict": "pending"},
            "implicit_missing": {"verdict": "pending"},
            "quality_only": {"verdict": "flat"},
        },
    )
    playbook = ace_playbook.build_playbook(100, min_quality=2)
    rows = {row["pattern"]: row for row in playbook["bullets"]}
    assert rows["explicit_missing"]["section"] == "deferred"
    assert rows["implicit_missing"]["section"] == "deferred"
    assert rows["quality_only"]["section"] == "deferred"
