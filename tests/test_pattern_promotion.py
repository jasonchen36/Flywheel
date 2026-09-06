from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import pattern_promotion
from review_store import load_reviews
from state_io import append_jsonl, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    signals = tmp_path / "signals"
    diagnostics = tmp_path / "diagnostics"
    signals.mkdir()
    paths = {
        "ledger": signals / "pattern_candidates.jsonl",
        "review": signals / "pending_human_review.jsonl",
        "diagnostics": diagnostics,
        "taxonomy": tmp_path / "self_improve.py",
    }
    monkeypatch.setattr(pattern_promotion, "CANDIDATES_FILE", paths["ledger"])
    monkeypatch.setattr(pattern_promotion, "REVIEW_FILE", paths["review"])
    monkeypatch.setattr(pattern_promotion, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(pattern_promotion, "SELF_IMPROVE_PY", paths["taxonomy"])
    return paths


def _entry(session_id: str, rating: int, summary: str) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        rating=rating,
        sentiment_summary=summary,
        patterns=[],
    )


def test_keyword_suggestions_and_pattern_validation():
    keywords = pattern_promotion.suggest_keywords(
        "missing_proof",
        ["Missing concrete deployment proof proof output", "Proof requires command output"],
        top_n=3,
    )
    assert keywords[:2] == ["missing", "proof"]
    assert "output" in keywords
    assert pattern_promotion.valid_pattern("missing_proof2") is True
    assert pattern_promotion.valid_pattern("Missing-Proof") is False
    assert pattern_promotion.valid_pattern("x\n}: exploit") is False


def test_main_rejects_nonpositive_occurrence_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configure(tmp_path, monkeypatch)
    assert pattern_promotion.main(["--min-occurrences", "0"]) == 2
    assert "must be positive" in capsys.readouterr().out


def test_main_filters_invalid_labels_and_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    entries = [_entry("one", 2, "Missing deployment proof"), _entry("two", 3, "Missing command proof")]
    monkeypatch.setattr(pattern_promotion, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(pattern_promotion, "classify_entry", lambda _entry: ["other"])
    monkeypatch.setattr(
        pattern_promotion,
        "classify_other_llm",
        lambda _entries: {
            "one": "missing_proof",
            "two": "invalid-label",
            3: "missing_proof",
        },
    )
    assert pattern_promotion.main(["--dry-run", "--min-occurrences", "1"]) == 0
    output = capsys.readouterr().out
    assert "Promotion candidates" in output
    assert "missing_proof" in output
    assert "invalid-label" not in output
    assert "no files written" in output
    assert not paths["ledger"].exists()
    assert not paths["review"].exists()
    assert not paths["diagnostics"].exists()


def test_main_accumulates_valid_sessions_and_queues_candidate_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    append_jsonl(paths["ledger"], {"bad": "row"})
    append_jsonl(
        paths["ledger"],
        {"session_id": "promoted", "label": "old_label", "status": "promoted", "rating": "bad"},
    )
    entries = [
        _entry("one", 2, "Missing deployment proof and command evidence"),
        _entry("two", 4, "Missing output proof and deployment evidence"),
        _entry("classified", 1, "Known failure"),
    ]
    monkeypatch.setattr(pattern_promotion, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(
        pattern_promotion,
        "classify_entry",
        lambda entry: ["known"] if entry.session_id == "classified" else ["other"],
    )
    monkeypatch.setattr(
        pattern_promotion,
        "classify_other_llm",
        lambda _entries: {"one": "missing_proof", "two": "missing_proof", "unknown": "missing_proof"},
    )

    assert pattern_promotion.main(["--min-occurrences", "2"]) == 0
    output = capsys.readouterr().out
    assert "Queued 1 new pattern" in output
    ledger = load_jsonl_objects(paths["ledger"]).records
    assert {row.get("session_id") for row in ledger} == {"promoted", "one", "two"}
    reviews = load_reviews(paths["review"])
    assert [row["pattern"] for row in reviews] == ["missing_proof"]
    assert "avg rating 3.0" in reviews[0]["note"]
    assert list(paths["diagnostics"].glob("pattern_promotion_*.md"))

    assert pattern_promotion.main(["--min-occurrences", "2"]) == 0
    assert len(load_reviews(paths["review"])) == 1


def test_main_without_other_entries_or_candidates_writes_empty_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    entry = _entry("known", 8, "working")
    monkeypatch.setattr(pattern_promotion, "load_all_ratings", lambda _path: [entry])
    monkeypatch.setattr(pattern_promotion, "classify_entry", lambda _entry: ["known"])
    monkeypatch.setattr(
        pattern_promotion,
        "classify_other_llm",
        lambda _entries: pytest.fail("classifier must not run without other entries"),
    )
    assert pattern_promotion.main([]) == 0
    assert "No labels have crossed" in capsys.readouterr().out
    assert load_jsonl_objects(paths["ledger"]).records == []
    assert not paths["review"].exists()


def test_taxonomy_promotion_validates_normalizes_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["taxonomy"].write_text(
        "PATTERN_KEYWORDS: dict[str, list[str]] = {\n"
        "    \"existing\": [\"old\"],\n"
        "}\n\nNEXT = 1\n"
    )
    assert pattern_promotion.promote_to_taxonomy("bad-label", ["proof"]) is False
    assert pattern_promotion.promote_to_taxonomy("new_pattern", []) is False
    assert pattern_promotion.promote_to_taxonomy(
        "new_pattern",
        [" Proof ", "proof", "quote\"value", ""],
    ) is True
    text = paths["taxonomy"].read_text()
    assert '"new_pattern": ["proof", "quote\\\"value"]' in text
    assert pattern_promotion.promote_to_taxonomy("new_pattern", ["different"]) is True
    assert paths["taxonomy"].read_text() == text


def test_taxonomy_promotion_handles_missing_or_unclosed_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["taxonomy"].write_text("VALUE = {}\n")
    assert pattern_promotion.promote_to_taxonomy("new_pattern", ["proof"]) is False
    paths["taxonomy"].write_text("PATTERN_KEYWORDS: dict[str, list[str]] = {\n")
    assert pattern_promotion.promote_to_taxonomy("new_pattern", ["proof"]) is False


def test_taxonomy_idempotency_ignores_pattern_text_outside_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["taxonomy"].write_text(
        "PATTERN_KEYWORDS: dict[str, list[str]] = {\n"
        "    \"existing\": [\"old\"],\n"
        "}\n\n"
        "EXAMPLE = '    \"new_pattern\": [\"not-real\"]'\n"
    )
    assert pattern_promotion.promote_to_taxonomy("new_pattern", ["proof"]) is True
    registry = paths["taxonomy"].read_text().split("}\n", 1)[0]
    assert '"new_pattern": ["proof"]' in registry


def test_exact_turn_ledger_does_not_inflate_distinct_session_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    entries = [
        SimpleNamespace(
            session_id="same-session",
            timestamp="2026-09-06T12:00:00Z",
            rating=2,
            sentiment_summary="Missing deployment proof",
            patterns=[],
        ),
        SimpleNamespace(
            session_id="same-session",
            timestamp="2026-09-06T12:01:00Z",
            rating=3,
            sentiment_summary="Missing command proof",
            patterns=[],
        ),
    ]
    monkeypatch.setattr(pattern_promotion, "load_all_ratings", lambda _path: entries)
    monkeypatch.setattr(pattern_promotion, "classify_entry", lambda _entry: ["other"])
    monkeypatch.setattr(
        pattern_promotion,
        "classify_other_llm",
        lambda _entries: {
            "same-session|2026-09-06T12:00:00Z": "missing_proof",
            "same-session|2026-09-06T12:01:00Z": "missing_proof",
        },
    )

    assert pattern_promotion.main(["--min-occurrences", "2"]) == 0
    ledger = load_jsonl_objects(paths["ledger"]).records
    assert {row["turn_key"] for row in ledger} == {
        "same-session|2026-09-06T12:00:00Z",
        "same-session|2026-09-06T12:01:00Z",
    }
    assert not paths["review"].exists()
