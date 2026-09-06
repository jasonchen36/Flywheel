from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import self_improve
from state_io import append_jsonl, load_jsonl_objects


def _entry(
    *,
    timestamp: str = "2026-09-06T12:00:00Z",
    session_id: str = "session-1",
    rating: int = 2,
    summary: str = "unclassified failure",
    response: str = "unexpected behavior",
) -> self_improve.RatingEntry:
    return self_improve.RatingEntry(
        timestamp=timestamp,
        rating=rating,
        session_id=session_id,
        source="test",
        sentiment_summary=summary,
        confidence=1.0,
        response_preview=response,
        comment="",
    )


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    harness = tmp_path / "harness"
    learning = harness / "MEMORY" / "LEARNING"
    signals = learning / "SIGNALS"
    state = harness / "MEMORY" / "STATE"
    lessons = harness / "MEMORY" / "lessons"
    diagnostics = learning / "DIAGNOSTICS"
    paths = {
        "harness": harness,
        "learning": learning,
        "signals": signals,
        "state": state,
        "lessons": lessons,
        "diagnostics": diagnostics,
        "ratings": signals / "ratings.jsonl",
        "reclass": signals / "other_reclass.jsonl",
        "candidates": signals / "eval_candidates.jsonl",
        "effectiveness": learning / "effectiveness_log.jsonl",
        "lessons_log": learning / "lessons_log.jsonl",
        "failures": learning / "FAILURES",
    }
    monkeypatch.setattr(self_improve, "HARNESS_HOME", harness)
    monkeypatch.setattr(self_improve, "RATINGS_FILE", paths["ratings"])
    monkeypatch.setattr(self_improve, "FAILURES_DIR", paths["failures"])
    monkeypatch.setattr(self_improve, "MEMORY_DIR", lessons)
    monkeypatch.setattr(self_improve, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(self_improve, "LESSONS_LOG", paths["lessons_log"])
    monkeypatch.setattr(self_improve, "EVAL_CANDIDATES_FILE", paths["candidates"])
    monkeypatch.setattr(self_improve, "EFFECTIVENESS_LOG", paths["effectiveness"])
    monkeypatch.setattr(self_improve, "OTHER_RECLASS_FILE", paths["reclass"])
    monkeypatch.setattr(self_improve, "_HIST_EPOCH_CACHE", None)
    monkeypatch.setattr(self_improve, "_RECLASS_CACHE", None)
    for name in (
        "PAI_SELF_IMPROVE_LLM_DISABLED",
        "PAI_BACKGROUND_LLM_PROVIDER",
        "PAI_HAIKU_BACKGROUND_DISABLED",
        "PAI_CLAUDE_HEADLESS_DISABLED",
        "GROK_AGENT",
    ):
        monkeypatch.delenv(name, raising=False)
    return paths


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("gemini", "gemini"),
        ("google", "gemini"),
        ("vertex", "gemini"),
        ("flash", "gemini"),
        ("opencode", "opencode"),
        ("haiku", "haiku"),
        ("anthropic", "haiku"),
        ("claude", "haiku"),
    ],
)
def test_call_llm_routes_only_explicit_provider_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: str,
):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", configured)
    calls: list[tuple[str, int]] = []

    def provider(name: str):
        def invoke(_prompt: str, *, model: str, max_tokens: int, system: str):
            del model, system
            calls.append((name, max_tokens))
            return f"{name}-result"

        return invoke

    monkeypatch.setattr(self_improve, "_call_llm_gemini", provider("gemini"))
    monkeypatch.setattr(self_improve, "_call_llm_opencode", provider("opencode"))
    monkeypatch.setattr(self_improve, "_call_llm_haiku", provider("haiku"))

    assert self_improve.call_llm("prompt", max_tokens=1) == f"{expected}-result"
    assert calls == [(expected, 64)]
    assert self_improve.LAST_LLM_PROVIDER == expected
    assert self_improve.LAST_LLM_ERROR is None


def test_call_llm_rejects_unknown_disabled_empty_and_failed_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        self_improve,
        "_call_llm_gemini",
        lambda *_args, **_kwargs: calls.append("gemini") or None,
    )
    monkeypatch.setattr(
        self_improve,
        "_call_llm_opencode",
        lambda *_args, **_kwargs: calls.append("opencode") or None,
    )
    monkeypatch.setattr(
        self_improve,
        "_call_llm_haiku",
        lambda *_args, **_kwargs: calls.append("haiku") or None,
    )

    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "typo-provider")
    self_improve.LAST_LLM_PROVIDER = "stale"
    assert self_improve.call_llm("prompt") is None
    assert calls == []
    assert self_improve.LAST_LLM_PROVIDER is None
    assert "unknown background LLM provider" in (self_improve.LAST_LLM_ERROR or "")

    monkeypatch.setenv("PAI_SELF_IMPROVE_LLM_DISABLED", "1")
    assert self_improve.call_llm("prompt") is None
    assert "disabled" in (self_improve.LAST_LLM_ERROR or "")
    monkeypatch.delenv("PAI_SELF_IMPROVE_LLM_DISABLED")

    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "gemini")
    assert self_improve.call_llm("prompt", max_tokens="bad") is None  # type: ignore[arg-type]
    assert calls == ["gemini"]
    assert self_improve.LAST_LLM_ERROR == "gemini returned no content"

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("offline")

    monkeypatch.setattr(self_improve, "_call_llm_gemini", unavailable)
    assert self_improve.call_llm("prompt") is None
    assert self_improve.LAST_LLM_ERROR == "RuntimeError: offline"


def test_call_llm_honors_headless_and_haiku_disablement_without_clobbering_opencode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    called: list[str] = []
    monkeypatch.setattr(
        self_improve,
        "_call_llm_gemini",
        lambda *_args, **_kwargs: called.append("gemini") or "ok",
    )
    monkeypatch.setattr(
        self_improve,
        "_call_llm_opencode",
        lambda *_args, **_kwargs: called.append("opencode") or "ok",
    )
    monkeypatch.setattr(
        self_improve,
        "_call_llm_haiku",
        lambda *_args, **_kwargs: called.append("haiku") or "ok",
    )

    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "haiku")
    monkeypatch.setenv("PAI_HAIKU_BACKGROUND_DISABLED", "1")
    assert self_improve.call_llm("prompt") == "ok"
    assert called == ["gemini"]

    called.clear()
    monkeypatch.delenv("PAI_HAIKU_BACKGROUND_DISABLED")
    monkeypatch.setenv("PAI_CLAUDE_HEADLESS_DISABLED", "1")
    assert self_improve.call_llm("prompt") == "ok"
    assert called == ["gemini"]

    called.clear()
    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "opencode")
    assert self_improve.call_llm("prompt") == "ok"
    assert called == ["opencode"]


def test_structured_lesson_parsing_and_schema_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    assert self_improve._parse_json_object("no object") is None
    assert self_improve._parse_json_object("{bad}") is None
    assert self_improve._parse_json_object("prefix [] suffix") is None
    raw = r"""```json
    {"instruction":"  Do not guess.\nVerify with a tool.  ",
     "root_cause":"  Missing   evidence ",
     "what_went_wrong":" Guessed ",
     "suggested_eval":{"id":"proof_required","predicate":"Response includes concrete tool evidence","pattern":"tool_misuse"}}
    ```"""
    parsed = self_improve._parse_json_object(raw)
    normalized = self_improve.normalize_structured_lesson(parsed, "tool_misuse")
    assert normalized == {
        "instruction": "Do not guess. Verify with a tool.",
        "root_cause": "Missing evidence",
        "what_went_wrong": "Guessed",
        "suggested_eval": {
            "id": "proof_required",
            "predicate": "Response includes concrete tool evidence",
            "pattern": "tool_misuse",
        },
    }
    assert self_improve.normalize_structured_lesson({"instruction": []}, "tool_misuse") is None
    without_eval = self_improve.normalize_structured_lesson(
        {
            "instruction": "Check evidence before responding.",
            "suggested_eval": {
                "id": "../bad",
                "predicate": "short",
                "pattern": "other",
            },
        },
        "tool_misuse",
    )
    assert without_eval == {
        "instruction": "Check evidence before responding.",
        "root_cause": "",
        "what_went_wrong": "",
    }


def test_generate_structured_lesson_falls_back_on_bad_model_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    examples = [_entry()]
    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: "[]")
    assert self_improve.generate_lesson_structured("tool_misuse", examples) is None
    monkeypatch.setattr(
        self_improve,
        "call_llm",
        lambda *_args, **_kwargs: json.dumps({"instruction": {"bad": True}}),
    )
    assert self_improve.generate_lesson_structured("tool_misuse", examples) is None


def test_eval_candidates_are_validated_and_semantically_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    candidate = {
        "id": "proof_required",
        "predicate": "Response includes concrete tool evidence",
        "pattern": "tool_misuse",
    }
    assert self_improve.append_eval_candidate("tool_misuse", candidate, dry_run=True) is False
    assert not paths["candidates"].exists()
    assert self_improve.append_eval_candidate("tool_misuse", {**candidate, "pattern": "other"}) is False
    assert self_improve.append_eval_candidate("tool_misuse", candidate) is True
    assert self_improve.append_eval_candidate("tool_misuse", candidate) is False
    assert self_improve.append_eval_candidate(
        "tool_misuse",
        {**candidate, "id": "second_id"},
    ) is False
    rows = load_jsonl_objects(paths["candidates"]).records
    assert len(rows) == 1
    assert rows[0]["status"] == "proposed"


def test_reclassification_is_exact_validated_and_replaced_transactionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    entry = _entry()
    assert self_improve.rating_entry_key(entry) == "session-1|2026-09-06T12:00:00Z"
    assert self_improve.rating_entry_key(_entry(session_id="")) == "timestamp|2026-09-06T12:00:00Z"
    assert self_improve.rating_entry_key(_entry(session_id="", timestamp="")) == ""
    assert self_improve._append_other_reclass(entry, ["tool_misuse"], "judge") is True
    assert self_improve._append_other_reclass(entry, ["blind_retry"], "judge") is True
    assert self_improve._append_other_reclass(entry, ["unknown"], "judge") is False
    assert self_improve._append_other_reclass(_entry(session_id="", timestamp=""), ["tool_misuse"], "judge") is False
    rows = load_jsonl_objects(paths["reclass"]).records
    assert len(rows) == 1
    assert rows[0]["patterns"] == ["blind_retry"]
    assert self_improve.classify_entry(entry) == ["blind_retry"]


def test_classify_other_llm_uses_exact_turn_keys_and_validates_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    entries = [
        _entry(session_id="same", timestamp="2026-09-06T12:00:00Z"),
        _entry(session_id="same", timestamp="2026-09-06T12:01:00Z"),
        _entry(session_id="", timestamp=""),
    ]
    with pytest.raises(ValueError, match="positive"):
        self_improve.classify_other_llm(entries, batch_size=0)
    monkeypatch.setattr(
        self_improve,
        "call_llm",
        lambda *_args, **_kwargs: "0: tool_misuse\n1: made_up\n2: blind_retry\n99: tool_misuse\nbad line",
    )
    result = self_improve.classify_other_llm(entries)
    assert result == {
        "same|2026-09-06T12:00:00Z": "tool_misuse",
        "same|2026-09-06T12:01:00Z": "other",
    }


def test_lesson_write_preserves_epoch_and_rejects_invalid_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    examples = [_entry(), _entry(timestamp="2026-09-06T12:01:00Z", session_id="session-2")]
    append_jsonl(
        paths["effectiveness"],
        {"pattern": "tool_misuse", "lesson_date": "2024-01-02"},
    )
    path = self_improve.write_lesson_file(
        "tool_misuse",
        "Do not guess; verify with a tool before answering.",
        examples,
        paths["lessons"],
    )
    assert path is not None and path.exists()
    text = path.read_text()
    assert "first_seen: 2024-01-02" in text
    assert "content_version:" in text

    assert self_improve.write_lesson_file("../escape", "rule", examples, paths["lessons"]) is None
    assert self_improve.write_lesson_file("tool_misuse", "rule", [], paths["lessons"]) is None
    assert self_improve.write_lesson_file(
        "bad_fence",
        "Do not emit an unmatched ``` fence.",
        examples,
        paths["lessons"],
    ) is None
    assert not (paths["lessons"] / "lesson_autogen_bad_fence.md").exists()
    assert "validation-error" in capsys.readouterr().out


def test_memory_index_is_idempotent_locked_and_dry_run_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["lessons"].mkdir(parents=True)
    index = paths["lessons"] / "MEMORY.md"
    index.write_text("# Lessons\n")
    lesson = paths["lessons"] / "lesson_autogen_tool_misuse.md"
    assert self_improve.update_memory_index([("tool_misuse", lesson)], paths["lessons"], True) == 1
    assert index.read_text() == "# Lessons\n"
    assert self_improve.update_memory_index([("tool_misuse", lesson)], paths["lessons"]) == 1
    assert self_improve.update_memory_index([("tool_misuse", lesson)], paths["lessons"]) == 0
    assert index.read_text().count("lesson_autogen_tool_misuse.md") == 1
    index.write_text("# Lessons\n[Auto-lessons (4)](LESSONS_INDEX.md)\n")
    assert self_improve.update_memory_index([("other", lesson)], paths["lessons"]) == 0
    index.unlink()
    assert self_improve.update_memory_index([("other", lesson)], paths["lessons"]) == 0


def test_main_applies_exact_turn_reclassification_and_writes_one_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    paths["lessons"].mkdir(parents=True)
    (paths["lessons"] / "MEMORY.md").write_text("# Lessons\n")
    first = _entry()
    second = _entry(timestamp="2026-09-06T12:01:00Z", session_id="session-2")
    for entry in (first, second):
        append_jsonl(
            paths["ratings"],
            {
                "timestamp": entry.timestamp,
                "rating": entry.rating,
                "session_id": entry.session_id,
                "source": entry.source,
                "sentiment_summary": entry.sentiment_summary,
                "confidence": entry.confidence,
                "response_preview": entry.response_preview,
                "comment": entry.comment,
            },
        )
    monkeypatch.setattr(
        self_improve,
        "classify_other_llm",
        lambda entries: {self_improve.rating_entry_key(entry): "tool_misuse" for entry in entries},
    )

    assert self_improve.main(
        ["--no-llm", "--classify-other", "--min-occurrences", "2"]
    ) == 0
    lesson = paths["lessons"] / "lesson_autogen_tool_misuse.md"
    assert lesson.exists()
    assert "lesson_autogen_tool_misuse.md" in (paths["lessons"] / "MEMORY.md").read_text()
    assert "2 lessons" not in capsys.readouterr().out


def test_main_rejects_invalid_bounds_and_dry_run_creates_no_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as threshold_error:
        self_improve.main(["--threshold", "0"])
    assert threshold_error.value.code == 2
    with pytest.raises(SystemExit) as occurrence_error:
        self_improve.main(["--min-occurrences", "0"])
    assert occurrence_error.value.code == 2

    isolated = tmp_path / "isolated" / "harness"
    monkeypatch.setattr(self_improve, "HARNESS_HOME", isolated)
    monkeypatch.setattr(self_improve, "RATINGS_FILE", isolated / "MEMORY/LEARNING/SIGNALS/ratings.jsonl")
    monkeypatch.setattr(self_improve, "MEMORY_DIR", isolated / "MEMORY/lessons")
    monkeypatch.setattr(self_improve, "DIAGNOSTICS", isolated / "MEMORY/LEARNING/DIAGNOSTICS")
    monkeypatch.setattr(self_improve, "LESSONS_LOG", isolated / "MEMORY/LEARNING/lessons_log.jsonl")
    assert self_improve.main(["--dry-run", "--no-llm"]) == 0
    assert not isolated.exists()


def test_cloud_provider_adapters_require_explicit_project_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    for name in (
        "PAI_BACKGROUND_LLM_PROJECT",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "VERTEX_AI_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert self_improve._call_llm_gemini("prompt") is None
    assert self_improve._call_llm_haiku("prompt") is None


def test_pai_settings_override_only_supported_environment_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["harness"].mkdir(parents=True)
    (paths["harness"] / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "PAI_BACKGROUND_LLM_PROVIDER": "opencode",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "configured-haiku",
                    "UNRELATED_SECRET": "must-not-copy",
                    "PAI_NON_STRING": 4,
                }
            }
        )
    )
    self_improve._apply_pai_settings_env()
    assert self_improve.os.environ["PAI_BACKGROUND_LLM_PROVIDER"] == "opencode"
    assert self_improve.os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "configured-haiku"
    assert "UNRELATED_SECRET" not in self_improve.os.environ
    assert "PAI_NON_STRING" not in self_improve.os.environ

    (paths["harness"] / "settings.json").write_text("not-json")
    self_improve._apply_pai_settings_env()
