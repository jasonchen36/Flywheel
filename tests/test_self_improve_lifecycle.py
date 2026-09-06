from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import self_improve
from state_io import append_jsonl, load_jsonl_objects


def _entry(
    *,
    rating: int = 2,
    timestamp: str = "2026-09-06T12:00:00Z",
    session_id: str = "session-1",
    summary: str = "failure",
    comment: str = "",
    preview: str = "",
) -> self_improve.RatingEntry:
    return self_improve.RatingEntry(
        timestamp=timestamp,
        rating=rating,
        session_id=session_id,
        source="test",
        sentiment_summary=summary,
        confidence=0.8,
        response_preview=preview,
        comment=comment,
    )


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    harness = tmp_path / "harness"
    learning = harness / "MEMORY" / "LEARNING"
    signals = learning / "SIGNALS"
    lessons = harness / "MEMORY" / "lessons"
    paths = {
        "harness": harness,
        "ratings": signals / "ratings.jsonl",
        "reclass": signals / "other_reclass.jsonl",
        "effectiveness": learning / "effectiveness_log.jsonl",
        "failures": learning / "FAILURES",
        "lessons": lessons,
        "diagnostics": learning / "DIAGNOSTICS",
        "lessons_log": learning / "lessons_log.jsonl",
        "candidates": signals / "eval_candidates.jsonl",
    }
    monkeypatch.setattr(self_improve, "HARNESS_HOME", harness)
    monkeypatch.setattr(self_improve, "RATINGS_FILE", paths["ratings"])
    monkeypatch.setattr(self_improve, "OTHER_RECLASS_FILE", paths["reclass"])
    monkeypatch.setattr(self_improve, "EFFECTIVENESS_LOG", paths["effectiveness"])
    monkeypatch.setattr(self_improve, "FAILURES_DIR", paths["failures"])
    monkeypatch.setattr(self_improve, "MEMORY_DIR", paths["lessons"])
    monkeypatch.setattr(self_improve, "DIAGNOSTICS", paths["diagnostics"])
    monkeypatch.setattr(self_improve, "LESSONS_LOG", paths["lessons_log"])
    monkeypatch.setattr(self_improve, "EVAL_CANDIDATES_FILE", paths["candidates"])
    monkeypatch.setattr(self_improve, "_HIST_EPOCH_CACHE", None)
    monkeypatch.setattr(self_improve, "_RECLASS_CACHE", None)
    return paths


def test_iso_dates_and_historical_epoch_ignore_malformed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    assert self_improve._iso_date(None) is None
    assert self_improve._iso_date("2026-02-29") is None
    assert self_improve._iso_date("2024-02-29T00:00:00Z") == "2024-02-29"
    paths["effectiveness"].parent.mkdir(parents=True)
    paths["effectiveness"].write_text(
        "bad json\n"
        "[]\n"
        + json.dumps({"pattern": "tool_misuse", "lesson_date": 4})
        + "\n"
        + json.dumps({"pattern": "tool_misuse", "lesson_date": "2025-99-99"})
        + "\n"
        + json.dumps({"pattern": "tool_misuse", "baseline_date": "2025-03-04"})
        + "\n"
        + json.dumps({"pattern": "tool_misuse", "lesson_date": "2024-01-02"})
        + "\n"
        + json.dumps({"pattern": 4, "lesson_date": "2020-01-01"})
        + "\n"
    )
    assert self_improve.historical_epoch("tool_misuse") == "2024-01-02"
    paths["effectiveness"].unlink()
    assert self_improve.historical_epoch("tool_misuse") == "2024-01-02"
    assert self_improve.historical_epoch("unknown") is None


def test_historical_epoch_tolerates_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["effectiveness"].parent.mkdir(parents=True)
    paths["effectiveness"].write_text("{}\n")
    original_open = Path.open

    def fail_open(path: Path, *args: object, **kwargs: object):
        if path == paths["effectiveness"]:
            raise OSError("unreadable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    assert self_improve.historical_epoch("tool_misuse") is None


def test_failure_directory_loading_filters_domains_and_normalizes_bad_ratings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    assert self_improve.load_failures(paths["failures"]) == []
    month = paths["failures"] / "2026-09"
    month.mkdir(parents=True)
    (paths["failures"] / "not-a-month").write_text("x")
    (month / "not-a-directory").write_text("x")
    (month / "2026-09-06-12-00-00-missing-context").mkdir()

    pr = month / "2026-09-06-12-00-00-pr-review"
    pr.mkdir()
    (pr / "CONTEXT.md").write_text(
        "rating: 99\nSummary: Missed inline findings\n\n"
        "## What Happened\n\nSkipped two comments.\n---\n"
    )
    dag = month / "2026-09-06-12-01-00-airflow-dag"
    dag.mkdir()
    (dag / "CONTEXT.md").write_text("rating: 2\nNo explicit summary\n")

    all_rows = self_improve.load_failures(paths["failures"])
    assert len(all_rows) == 2
    assert all_rows[0].rating == 3
    assert all_rows[0].sentiment_summary == "Missed inline findings"
    assert all_rows[0].response_preview == "Skipped two comments."
    assert all_rows[1].rating == 2
    assert len(self_improve.load_failures(paths["failures"], "pr_review")) == 1
    assert len(self_improve.load_failures(paths["failures"], "dag")) == 1


def test_failure_directory_loading_skips_unreadable_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    context = paths["failures"] / "2026-09" / "2026-09-06-12-00-00-pr" / "CONTEXT.md"
    context.parent.mkdir(parents=True)
    context.write_text("rating: 2")
    original_read = Path.read_text

    def fail_read(path: Path, *args: object, **kwargs: object):
        if path == context:
            raise OSError("denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert self_improve.load_failures(paths["failures"]) == []


def test_rating_loader_normalizes_shapes_integral_values_and_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    rows = [
        {"rating": True},
        {"rating": 2.5},
        {"rating": 11},
        {"rating": "bad"},
        {
            "rating": "3",
            "confidence": "nan",
            "timestamp": 4,
            "session_id": "s1",
            "tools_used": ["Read", 4],
            "files_touched": "bad",
            "skill_candidates": ["review", None],
            "eval_results": [],
        },
        {
            "rating": 4.0,
            "confidence": 7,
            "timestamp": "ts",
            "session_id": "s2",
            "eval_results": {"e": {"passed": False, "pattern": "tool_misuse"}},
        },
    ]
    for row in rows:
        append_jsonl(paths["ratings"], row)
    loaded = self_improve.load_all_ratings(paths["ratings"])
    assert [row.rating for row in loaded] == [3, 4]
    assert loaded[0].confidence == 0.0
    assert loaded[0].timestamp == ""
    assert loaded[0].tools_used == ["Read"]
    assert loaded[0].files_touched == []
    assert loaded[0].skill_candidates == ["review"]
    assert loaded[0].eval_results == {}
    assert loaded[1].confidence == 1.0


def test_reclassification_loading_and_classifier_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["reclass"].parent.mkdir(parents=True)
    paths["reclass"].write_text(
        "bad\n[]\n"
        + json.dumps({"session_id": "s", "timestamp": "t", "patterns": "bad"})
        + "\n"
        + json.dumps({"session_id": "s", "timestamp": "t", "patterns": ["unknown", 4]})
        + "\n"
        + json.dumps({"session_id": "s", "timestamp": "t", "patterns": ["blind_retry"]})
        + "\n"
    )
    entry = _entry(session_id="s", timestamp="t", summary="used wrong tool")
    assert self_improve.classify_entry(entry) == ["blind_retry"]

    monkeypatch.setattr(self_improve, "OTHER_RECLASS_FILE", tmp_path / "missing.jsonl")
    monkeypatch.setattr(self_improve, "_RECLASS_CACHE", None)
    eval_entry = _entry(summary="neutral")
    eval_entry.eval_results = {
        "one": {"passed": False, "pattern": "tool_misuse"},
        "two": {"passed": True, "pattern": "blind_retry"},
        "three": "bad",
    }
    assert self_improve.classify_entry(eval_entry) == ["tool_misuse"]
    assert self_improve.classify_entry(_entry(summary="no recognizable signal")) == ["other"]


def test_provider_adapters_build_expected_requests_and_handle_empty_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    calls: dict[str, object] = {}

    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")

    class HttpOptions:
        def __init__(self, **kwargs: object):
            calls["http"] = kwargs

    class ThinkingConfig:
        def __init__(self, **kwargs: object):
            calls["thinking"] = kwargs

    class GenerateContentConfig:
        def __init__(self, **kwargs: object):
            calls["gemini_config"] = kwargs

    class Models:
        def generate_content(self, **kwargs: object):
            calls["gemini_request"] = kwargs
            return SimpleNamespace(text="  gemini text  ")

    class GeminiClient:
        def __init__(self, **kwargs: object):
            calls["gemini_client"] = kwargs
            self.models = Models()

    genai_module.Client = GeminiClient  # type: ignore[attr-defined]
    genai_module.types = SimpleNamespace(  # type: ignore[attr-defined]
        HttpOptions=HttpOptions,
        ThinkingConfig=ThinkingConfig,
        GenerateContentConfig=GenerateContentConfig,
    )
    google_module.genai = genai_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROJECT", "project")
    monkeypatch.setenv("PAI_BACKGROUND_LLM_LOCATION", "region")
    assert self_improve._call_llm_gemini("prompt", model="wrong", max_tokens=4, system="sys") == "gemini text"
    assert calls["gemini_client"] == {
        "vertexai": True,
        "project": "project",
        "location": "region",
        "http_options": ANY,
    }
    assert calls["gemini_request"]  # request was issued

    openai_module = ModuleType("openai")

    class Completions:
        def create(self, **kwargs: object):
            calls["openai_request"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=" open text "))]
            )

    class OpenAI:
        def __init__(self, **kwargs: object):
            calls["openai_client"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())

    openai_module.OpenAI = OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("PAI_OPENAI_API_KEY", "token")
    monkeypatch.setenv("PAI_OPENAI_BASE_URL", "https://example.test")
    assert self_improve._call_llm_opencode("prompt", max_tokens=2, system="sys") == "open text"
    assert calls["openai_request"]  # request was issued
    monkeypatch.delenv("PAI_OPENAI_API_KEY")
    assert self_improve._call_llm_opencode("prompt") is None

    anthropic_module = ModuleType("anthropic")

    class AnthropicVertex:
        def __init__(self, **kwargs: object):
            calls["anthropic_client"] = kwargs
            def create(**request: object):
                calls["anthropic_request"] = request
                return SimpleNamespace(content=[SimpleNamespace(text="haiku text")])

            self.messages = SimpleNamespace(create=create)

    anthropic_module.AnthropicVertex = AnthropicVertex  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "project")
    result = self_improve._call_llm_haiku("prompt", system="sys")
    assert result == "haiku text"


def test_generate_lesson_classification_parsing_and_reclass_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    entries = [_entry(rating=1), _entry(rating=4, session_id="s2", timestamp="t2")]
    monkeypatch.setattr(self_improve, "call_llm", lambda prompt, **_kwargs: prompt.split("Failures:", 1)[0])
    assert "Analyze these AI assistant failures" in (
        self_improve.generate_lesson_llm("tool_misuse", entries) or ""
    )

    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: None)
    assert self_improve.classify_other_llm([]) == {}
    assert self_improve.classify_other_llm(entries) == {}

    monkeypatch.setattr(
        self_improve,
        "call_llm",
        lambda *_args, **_kwargs: "bad\n2: tool_misuse\n0: made_up\n1: blind_retry",
    )
    result = self_improve.classify_other_llm(entries, batch_size=1)
    assert result[self_improve.rating_entry_key(entries[0])] == "other"
    assert result[self_improve.rating_entry_key(entries[1])] == "other"


def test_existing_rule_validation_report_and_run_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    missing = paths["lessons"] / "missing.md"
    assert self_improve._existing_rule_and_date(missing) == (None, None, None)
    missing.parent.mkdir(parents=True)
    missing.write_text(
        "---\nname: lesson\nlast_updated: 2025-99-99\nfirst_seen: 2024-01-02\n---\n\nRule.\n\nWhy.\n"
    )
    assert self_improve._existing_rule_and_date(missing) == ("Rule.", None, "2024-01-02")
    assert self_improve.validate_lesson_format("body") is False
    assert self_improve.validate_lesson_format("---\nname: x\n---\nbody") is False
    assert self_improve.validate_lesson_format(
        "---\nname: x\ndescription: y\nmetadata:\n---\n```\n"
    ) is False
    assert "validation-error" in capsys.readouterr().out

    rows = [_entry(rating=2, summary="bad", session_id="s1"), _entry(rating=8, summary="good", session_id="s2")]
    rows[0].skill = "review"
    rows[1].skill = "review"
    data = {
        "tool_misuse": {
            "count": 1,
            "avg_rating": 2.0,
            "examples": [rows[0]],
            "action": "lesson_written",
        },
        "other": {
            "count": 1,
            "avg_rating": 2.0,
            "examples": [rows[0]],
            "action": "skip_unclassified",
        },
    }
    report = self_improve.generate_report(rows, [rows[0]], data, paths["diagnostics"], threshold=3)
    text = report.read_text()
    assert "Low-rated (≤3 threshold)" in text
    assert "Skill Failure Concentration" in text
    assert "Tool Misuse" in text
    self_improve.log_run(data, paths["lessons_log"])
    assert load_jsonl_objects(paths["lessons_log"]).records[0]["patterns"]["tool_misuse"]["count"] == 1


def test_main_covers_report_and_failures_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    failure = paths["failures"] / "2026-09" / "2026-09-06-12-00-00-pr-review"
    failure.mkdir(parents=True)
    (failure / "CONTEXT.md").write_text(
        "rating: 2\nSummary: approved twice without checking\n\n"
        "## What Happened\n\nRepeated approval.\n---\n"
    )
    assert self_improve.main(["--source", "pr_review", "--report"]) == 0
    assert list(paths["diagnostics"].glob("diagnostic_*.md"))

    assert self_improve.main(
        ["--source", "pr_review", "--no-llm", "--min-occurrences", "2", "--dry-run"]
    ) == 0


def test_historical_cache_blank_rows_and_classifier_duplicate_objective_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["effectiveness"].parent.mkdir(parents=True)
    paths["effectiveness"].write_text(
        "\n"
        + json.dumps({"pattern": "tool_misuse", "lesson_date": "2024-01-01"})
        + "\n"
        + json.dumps({"pattern": "tool_misuse", "lesson_date": "2025-01-01"})
        + "\n"
    )
    assert self_improve.historical_epoch("tool_misuse") == "2024-01-01"

    entry = _entry(summary="used the wrong tool")
    entry.eval_results = {"duplicate": {"passed": False, "pattern": "tool_misuse"}}
    assert self_improve.classify_entry(entry).count("tool_misuse") == 1

    (paths["harness"] / "settings.json").write_text(json.dumps({"env": ["invalid"]}))
    self_improve._apply_pai_settings_env()


def test_call_llm_handles_signal_limitations_timeouts_and_empty_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    import signal

    installed: dict[str, object] = {}

    def install(_kind: object, handler: object):
        installed["handler"] = handler
        return object()

    def trigger_timeout(seconds: int):
        if seconds == 30:
            handler = installed["handler"]
            assert callable(handler)
            handler(None, None)

    monkeypatch.setattr(signal, "signal", install)
    monkeypatch.setattr(signal, "alarm", trigger_timeout)
    assert self_improve.call_llm("prompt") is None
    assert "TimeoutError" in (self_improve.LAST_LLM_ERROR or "")

    def unsupported_signal(*_args: object):
        raise ValueError("not main thread")

    monkeypatch.setattr(signal, "signal", unsupported_signal)
    monkeypatch.setattr(signal, "alarm", lambda _seconds: None)
    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "opencode")
    monkeypatch.setattr(self_improve, "_call_llm_opencode", lambda *_args, **_kwargs: None)
    assert self_improve.call_llm("prompt") is None
    assert self_improve.LAST_LLM_ERROR == "opencode returned no content"

    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "gemini")

    def failed_provider(*_args: object, **_kwargs: object):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(self_improve, "_call_llm_gemini", failed_provider)
    assert self_improve.call_llm("prompt") is None
    assert self_improve.LAST_LLM_ERROR == "RuntimeError: provider failed"

    monkeypatch.setenv("PAI_BACKGROUND_LLM_PROVIDER", "haiku")
    monkeypatch.setattr(self_improve, "_call_llm_haiku", lambda *_args, **_kwargs: None)
    assert self_improve.call_llm("prompt") is None
    assert self_improve.LAST_LLM_ERROR == "haiku returned no content"

    def broken_alarm(_seconds: int):
        raise AttributeError("alarm unavailable")

    monkeypatch.setattr(signal, "alarm", broken_alarm)
    assert self_improve.call_llm("prompt") is None


def test_provider_adapter_optional_fields_and_empty_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    calls: dict[str, object] = {"gemini_text": None, "openai_text": None}

    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")

    class Config:
        def __init__(self, **kwargs: object):
            calls.setdefault("configs", []).append(kwargs)  # type: ignore[union-attr]

    class GeminiClient:
        def __init__(self, **_kwargs: object):
            self.models = SimpleNamespace(
                generate_content=lambda **_request: SimpleNamespace(text=calls["gemini_text"])
            )

    genai_module.Client = GeminiClient  # type: ignore[attr-defined]
    genai_module.types = SimpleNamespace(  # type: ignore[attr-defined]
        HttpOptions=Config,
        ThinkingConfig=Config,
        GenerateContentConfig=Config,
    )
    google_module.genai = genai_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setenv("VERTEX_AI_PROJECT", "project")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "region")
    assert self_improve._call_llm_gemini("prompt", model="gemini-valid") is None

    openai_module = ModuleType("openai")

    class OpenAI:
        def __init__(self, **_kwargs: object):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_request: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=calls["openai_text"]))]
                    )
                )
            )

    openai_module.OpenAI = OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("PAI_OPENAI_API_KEY", "key")
    monkeypatch.setenv("PAI_OPENAI_BASE_URL", "https://example.test")
    assert self_improve._call_llm_opencode("prompt", model="model") is None

    anthropic_module = ModuleType("anthropic")

    class AnthropicVertex:
        def __init__(self, **kwargs: object):
            calls["anthropic_kwargs"] = kwargs
            self.messages = SimpleNamespace(
                create=lambda **request: SimpleNamespace(
                    content=[SimpleNamespace(text=request["messages"][0]["content"])]
                )
            )

    anthropic_module.AnthropicVertex = AnthropicVertex  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_BASE_URL", "https://anthropic.test")
    assert self_improve._call_llm_haiku("prompt") == "prompt"
    assert calls["anthropic_kwargs"]["base_url"] == "https://anthropic.test"  # type: ignore[index]


def test_normalization_and_classification_edge_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    assert self_improve.normalize_eval_candidate("tool_misuse", None) is None
    assert self_improve.normalize_structured_lesson(None, "tool_misuse") is None
    assert self_improve._normalized_text("abcdef", maximum=3) == "abc"

    entries = [_entry(session_id="", timestamp="")]
    monkeypatch.setattr(self_improve, "call_llm", lambda *_args, **_kwargs: "0: tool_misuse")
    assert self_improve.classify_other_llm(entries) == {}


def test_rich_lesson_metadata_structured_diagnostics_and_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    examples = [_entry(), _entry(session_id="s2", timestamp="t2", rating=3)]
    for entry in examples:
        entry.repo = "repo"
        entry.tools_used = ["Read"]
        entry.skill = "review"
    structured = {
        "instruction": "Do not guess; inspect evidence first.",
        "root_cause": "Evidence was not inspected.",
        "what_went_wrong": "The response guessed.",
    }
    preview = self_improve.write_lesson_file(
        "tool_misuse",
        structured["instruction"],
        examples,
        paths["lessons"],
        dry_run=True,
        structured=structured,
    )
    assert preview is not None
    assert not preview.exists()

    written = self_improve.write_lesson_file(
        "tool_misuse",
        structured["instruction"],
        examples,
        paths["lessons"],
        structured=structured,
    )
    assert written is not None
    text = written.read_text()
    assert "repos: repo (×2)" in text
    assert "tools: Read (×2)" in text
    assert "skills: /review (×2)" in text
    assert "**Root cause:** Evidence was not inspected." in text
    assert "**What went wrong:** The response guessed." in text

    first = self_improve._existing_rule_and_date(written)
    rewritten = self_improve.write_lesson_file(
        "tool_misuse",
        structured["instruction"],
        examples,
        paths["lessons"],
        structured={"instruction": structured["instruction"], "root_cause": 4},
    )
    assert rewritten is not None
    assert self_improve._existing_rule_and_date(rewritten)[1:] == first[1:]


def test_empty_report_is_dry_run_safe_and_uses_no_skill_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    report = self_improve.generate_report([], [], {}, paths["diagnostics"], dry_run=True)
    assert not report.exists()

    entry = _entry()
    data = {
        "other": {
            "count": 1,
            "avg_rating": 2.0,
            "examples": [entry],
            "action": "skip_unclassified",
        }
    }
    report = self_improve.generate_report([entry], [entry], data, paths["diagnostics"])
    assert "No skill attribution yet" in report.read_text()


def test_main_exercises_skip_structured_fallback_and_validation_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    paths["lessons"].mkdir(parents=True)
    (paths["lessons"] / "MEMORY.md").write_text("# Lessons\n")

    rows = [
        _entry(session_id="s1", timestamp="t1", summary="wrong tool"),
        _entry(session_id="s2", timestamp="t2", summary="wrong tool"),
        _entry(session_id="s3", timestamp="t3", summary="unrecognized phrase"),
    ]
    for row in rows:
        append_jsonl(
            paths["ratings"],
            {
                "rating": row.rating,
                "timestamp": row.timestamp,
                "session_id": row.session_id,
                "source": row.source,
                "sentiment_summary": row.sentiment_summary,
                "confidence": row.confidence,
                "response_preview": row.response_preview,
                "comment": row.comment,
            },
        )

    structured = {
        "instruction": "Do not guess; verify with tools.",
        "root_cause": "No evidence.",
        "what_went_wrong": "The response guessed.",
        "suggested_eval": {
            "id": "requires_tool_evidence",
            "predicate": "Response contains concrete tool evidence",
            "pattern": "tool_misuse",
        },
    }
    monkeypatch.setattr(
        self_improve,
        "generate_lesson_structured",
        lambda pattern, _examples: structured if pattern == "tool_misuse" else None,
    )
    assert self_improve.main(["--min-occurrences", "2"]) == 0
    assert load_jsonl_objects(paths["candidates"]).records[0]["id"] == "requires_tool_evidence"
    assert "eval candidate logged" in capsys.readouterr().out
    assert self_improve.main(["--min-occurrences", "2"]) == 0
    assert len(load_jsonl_objects(paths["candidates"]).records) == 1

    paths["ratings"].unlink()
    append_jsonl(
        paths["ratings"],
        {
            "rating": 2,
            "timestamp": "t4",
            "session_id": "s4",
            "source": "test",
            "sentiment_summary": "wrong tool",
            "confidence": 1,
            "response_preview": "",
            "comment": "",
        },
    )
    assert self_improve.main(["--no-llm", "--min-occurrences", "2", "--dry-run"]) == 0
    assert "skip" in capsys.readouterr().out

    monkeypatch.setattr(self_improve, "generate_lesson_structured", lambda *_args, **_kwargs: None)
    assert self_improve.main(["--min-occurrences", "1", "--dry-run"]) == 0

    monkeypatch.setattr(self_improve, "write_lesson_file", lambda *_args, **_kwargs: None)
    assert self_improve.main(["--no-llm", "--min-occurrences", "1"]) == 0
    log = load_jsonl_objects(paths["lessons_log"]).records[-1]
    assert log["patterns"]["tool_misuse"]["action"] == "validation_failed"


def test_main_uses_reflector_and_heuristic_fallbacks_for_new_dag_patterns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    entries = [
        _entry(session_id="s1", summary="novel dag failure"),
        _entry(session_id="s2", summary="novel dag failure"),
    ]
    monkeypatch.setattr(self_improve, "load_failures", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(self_improve, "DAG_PATTERN_KEYWORDS", {"novel_pattern": ["novel"]})
    monkeypatch.setattr(self_improve, "DAG_LESSON_TEMPLATES", {})
    assert self_improve.main(["--source", "dag", "--no-llm", "--min-occurrences", "2"]) == 0
    assert (paths["lessons"] / "lesson_autogen_novel_pattern.md").exists()
    assert "[reflector]" in capsys.readouterr().out

    original_import = __import__

    def fail_reflector(name: str, *args: object, **kwargs: object):
        if name == "ace_reflector":
            raise ImportError("missing reflector")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_reflector)
    assert self_improve.main(["--source", "dag", "--no-llm", "--min-occurrences", "2"]) == 0
    assert "heuristic-fallback" in capsys.readouterr().out


def test_main_classify_other_tolerates_entries_without_turn_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    append_jsonl(
        paths["ratings"],
        {
            "rating": 2,
            "timestamp": "",
            "session_id": "",
            "source": "test",
            "sentiment_summary": "unrecognized",
            "confidence": 1,
            "response_preview": "",
            "comment": "",
        },
    )
    monkeypatch.setattr(self_improve, "classify_other_llm", lambda _entries: {})
    assert self_improve.main(["--classify-other", "--no-llm", "--dry-run"]) == 0


def test_main_covers_all_failure_source_taxonomies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(self_improve, "load_failures", lambda *_args, **_kwargs: [])
    assert self_improve.main(["--source", "failures", "--dry-run", "--no-llm"]) == 0
    assert self_improve.main(["--source", "dag", "--dry-run", "--no-llm"]) == 0
