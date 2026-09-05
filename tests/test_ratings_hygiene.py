from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import ratings_hygiene
from state_io import append_jsonl, load_jsonl_objects


def _configure_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    signals = tmp_path / "signals"
    ratings = signals / "ratings.jsonl"
    monkeypatch.setattr(ratings_hygiene, "SIGNALS", signals)
    monkeypatch.setattr(ratings_hygiene, "RATINGS", ratings)
    monkeypatch.setattr(ratings_hygiene, "BACKUP_DIR", signals / "backups")
    return ratings


def _clean(**extra: object) -> dict:
    return {
        "rating": 8,
        "source": "implicit",
        "response_preview": "A sufficiently detailed useful response with concrete evidence.",
        "skill": "review",
        **extra,
    }


def test_is_junk_preserves_explicit_scores_and_classifies_all_noise_shapes():
    assert ratings_hygiene.is_junk({"source": "explicit", "rating": None}) is False
    assert ratings_hygiene.is_junk({"source": "implicit", "rating": None}) is True
    assert ratings_hygiene.is_junk({"source": "implicit", "rating": 3, "response_preview": "short"}) is True
    assert ratings_hygiene.is_junk({"source": "implicit", "rating": 3, "confidence": 0, "response_preview": "INFERENCE_FAILED but padded beyond forty characters here"}) is True
    for preview in (
        'payload with "extracted_entities" and enough text for detection',
        'payload with "summaries": [] and enough text for detection',
        "PAI harness graph sync produced this system-generated response",
        "Above data unverified — sourced from a system message and padded",
    ):
        assert ratings_hygiene.is_junk({"source": "system", "rating": 3, "response_preview": preview}) is True
    assert ratings_hygiene.is_junk(_clean()) is False


def test_load_rows_handles_missing_valid_and_corrupt_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ratings = _configure_paths(tmp_path, monkeypatch)
    assert ratings_hygiene.load_rows() == []
    ratings.parent.mkdir(parents=True)
    ratings.write_text('{"rating": 8}\nnot-json\n[]\n')
    assert ratings_hygiene.load_rows() == [{"rating": 8}]


def test_main_reports_empty_and_low_attribution_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ratings = _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ratings_hygiene.py", "--stats"])
    assert ratings_hygiene.main() == 0
    empty = json.loads(capsys.readouterr().out)
    assert empty["total"] == 0
    assert empty["skill_attribution_healthy"] is False

    append_jsonl(ratings, _clean(skill="general-session", skill_candidates=["a", "b"], agent="claude"))
    monkeypatch.setattr(sys, "argv", ["ratings_hygiene.py"])
    assert ratings_hygiene.main() == 0
    output = capsys.readouterr().out
    report, warning = output.split("\n[ratings_hygiene]", 1)
    payload = json.loads(report)
    assert payload["clean_with_multi_skill_candidates"] == 1
    assert payload["clean_with_agent"] == 1
    assert payload["clean_with_skill_non_general_rate"] == 0.0
    assert "WARNING" in warning


def test_apply_reloads_under_lock_preserves_late_clean_row_and_creates_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ratings = _configure_paths(tmp_path, monkeypatch)
    append_jsonl(ratings, {"rating": None, "source": "implicit", "response_preview": "junk"})
    append_jsonl(ratings, _clean(skill="review"))
    original_load = ratings_hygiene.load_rows
    calls = 0

    def append_before_locked_reload() -> list[dict]:
        nonlocal calls
        calls += 1
        rows = original_load()
        if calls == 1:
            append_jsonl(ratings, _clean(skill="testing", response_preview="A second clean response appended after the report snapshot was loaded."))
        return rows

    monkeypatch.setattr(ratings_hygiene, "load_rows", append_before_locked_reload)
    monkeypatch.setattr(sys, "argv", ["ratings_hygiene.py", "--apply"])
    assert ratings_hygiene.main() == 0
    output = capsys.readouterr().out
    assert "rewrote" in output
    records = load_jsonl_objects(ratings).records
    assert [record["skill"] for record in records] == ["review", "testing"]
    backups = list((ratings.parent / "backups").glob("ratings.jsonl.*.bak"))
    assert len(backups) == 1
    assert len(load_jsonl_objects(backups[0]).records) == 3


def test_apply_with_only_clean_rows_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ratings = _configure_paths(tmp_path, monkeypatch)
    append_jsonl(ratings, _clean(skill="review"))
    before = ratings.read_text()
    monkeypatch.setattr(sys, "argv", ["ratings_hygiene.py", "--apply"])
    assert ratings_hygiene.main() == 0
    assert ratings.read_text() == before
    assert "rewrote" not in capsys.readouterr().out
    assert not ratings_hygiene.BACKUP_DIR.exists()
