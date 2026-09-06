from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import evals
from self_improve import RatingEntry
from state_io import load_jsonl_objects, lock_path_for


def _case(
    eval_id: str = "proof_check",
    pattern: str = "missing_proof",
    version: int = 1,
    source: str = "manual",
) -> evals.Eval:
    return evals.Eval(
        id=eval_id,
        pattern=pattern,
        version=version,
        source=source,
        description="Requires proof",
        applies=lambda text: "claim" in text,
        check=lambda text: "proof" in text,
    )


def _entry(
    *,
    session: str = "session-a",
    timestamp: str = "2026-09-06T12:00:00Z",
    preview: str = "claim without evidence",
    captured: dict | None = None,
    rating: int = 2,
) -> RatingEntry:
    return RatingEntry(
        timestamp=timestamp,
        rating=rating,
        session_id=session,
        source="explicit",
        sentiment_summary="missing evidence",
        confidence=1.0,
        response_preview=preview,
        comment="",
        eval_results=captured or {},
    )


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    signals = tmp_path / "signals"
    state = tmp_path / "state"
    diagnostics = tmp_path / "diagnostics"
    signals.mkdir()
    state.mkdir()
    diagnostics.mkdir()
    paths = {
        "results": signals / "eval_results.jsonl",
        "registry": state / "eval_registry.json",
        "diagnostics": diagnostics,
        "ratings": signals / "ratings.jsonl",
    }
    monkeypatch.setattr(evals, "EVAL_RESULTS_FILE", paths["results"])
    monkeypatch.setattr(evals, "REGISTRY_FILE", paths["registry"])
    monkeypatch.setattr(evals, "DIAGNOSTICS", diagnostics)
    monkeypatch.setattr(evals, "RATINGS_FILE", paths["ratings"])
    return paths


@pytest.mark.parametrize(
    "cases, message",
    [
        ([_case(eval_id="Bad-ID")], "invalid eval id"),
        ([_case(pattern="bad pattern")], "invalid eval pattern"),
        ([_case(version=0)], "invalid eval version"),
        ([_case(), _case()], "duplicate eval id"),
    ],
)
def test_eval_catalog_rejects_ambiguous_code_state(
    cases: list[evals.Eval], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals, "EVALS", cases)
    with pytest.raises(ValueError, match=message):
        evals.eval_catalog()


def test_scoring_uses_validated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case()])
    assert evals.covered_patterns() == {"missing_proof"}
    assert evals.score_text("nothing")["proof_check"] == {
        "applied": False,
        "passed": None,
        "pattern": "missing_proof",
    }
    assert evals.score_text("claim with proof")["proof_check"]["passed"] is True
    assert evals.score_text("claim only")["proof_check"]["passed"] is False
    assert evals.score_session(_entry(preview="claim with proof"))["proof_check"]["passed"] is True


def test_objective_results_validate_schema_and_exact_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case()])
    path = tmp_path / "results.jsonl"
    rows = [
        {"turn_key": "s1|same", "eval_id": "proof_check", "pattern": "missing_proof", "passed": False},
        {"turn_key": "s1|same", "eval_id": "proof_check", "pattern": "missing_proof", "passed": True},
        {"session_id": "s2", "timestamp": "same", "eval_id": "proof_check", "pattern": "missing_proof", "passed": True},
        {"timestamp": "legacy", "eval_id": "proof_check", "pattern": "missing_proof", "passed": False},
        {"turn_key": "bad-id", "eval_id": "unknown", "pattern": "missing_proof", "passed": False},
        {"turn_key": "bad-pattern", "eval_id": "proof_check", "pattern": "other", "passed": False},
        {"turn_key": "bad-pass", "eval_id": "proof_check", "pattern": ["unhashable"], "passed": None},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n[]\n")

    failures = evals.load_objective_fails(path)
    assert failures == {
        "s1|same": {"missing_proof": True},
        "s2|same": {"missing_proof": False},
        "timestamp|legacy": {"missing_proof": True},
    }


def test_build_result_rows_prefers_valid_capture_and_falls_back_on_invalid_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case()])
    captured = _entry(
        captured={
            "proof_check": {"passed": True, "pattern": "missing_proof"},
            "unknown": {"passed": False, "pattern": "missing_proof"},
        }
    )
    invalid = _entry(
        session="session-b",
        captured={"proof_check": {"passed": None, "pattern": ["bad"]}},
    )
    rows = evals.build_result_rows([captured, invalid])

    assert len(rows) == 2
    assert rows[0]["turn_key"] == "session-a|2026-09-06T12:00:00Z"
    assert rows[0]["passed"] is True
    assert rows[1]["turn_key"] == "session-b|2026-09-06T12:00:00Z"
    assert rows[1]["passed"] is False
    assert all(row["eval_id"] == "proof_check" for row in rows)


def test_exact_turn_rows_prevent_timestamp_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case()])
    entries = [
        _entry(session="first", preview="claim only"),
        _entry(session="second", preview="claim with proof"),
    ]
    rows = evals.build_result_rows(entries)
    path = tmp_path / "results.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    failures = evals.load_objective_fails(path)
    assert failures["first|2026-09-06T12:00:00Z"]["missing_proof"] is True
    assert failures["second|2026-09-06T12:00:00Z"]["missing_proof"] is False


def test_registry_normalization_and_reconciliation_record_metadata_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(version=2, source="manual")
    monkeypatch.setattr(evals, "EVALS", [case])
    registry = {
        "updated": 123,
        "evals": {
            "proof_check": {
                "pattern": "old_pattern",
                "version": 1,
                "source": "seed",
                "status": "broken",
            },
            "orphan": {"pattern": "old", "version": 1, "source": "manual", "status": "active"},
            "bad": "not-an-object",
        },
        "invalid_evals": {"prior": ["bad"]},
        "log": [{"date": "old"}, "bad"],
    }
    changes, orphans = evals.reconcile_registry(registry, "2026-09-06")

    record = registry["evals"]["proof_check"]
    assert record["version"] == 2
    assert record["pattern"] == "missing_proof"
    assert record["source"] == "manual"
    assert record["status"] == "active"
    assert registry["evals"]["orphan"]["status"] == "orphaned"
    assert registry["invalid_evals"] == {"bad": "not-an-object", "prior": ["bad"]}
    assert orphans == ["orphan"]
    assert any(change.startswith("version proof_check") for change in changes)
    assert any(change.startswith("pattern proof_check") for change in changes)
    assert any(change.startswith("source proof_check") for change in changes)
    assert any(change.startswith("status proof_check") for change in changes)
    assert registry["log"][-1]["changes"] == changes


def test_registry_reactivates_retired_and_orphaned_code_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case("first"), _case("second")])
    registry = {
        "evals": {
            "first": {"pattern": "missing_proof", "version": 1, "source": "manual", "status": "retired"},
            "second": {"pattern": "missing_proof", "version": 1, "source": "manual", "status": "orphaned"},
        }
    }
    changes, orphans = evals.reconcile_registry(registry, "2026-09-06")
    assert orphans == []
    assert registry["evals"]["first"]["status"] == "active"
    assert registry["evals"]["second"]["status"] == "active"
    assert any("reactivated first" in change for change in changes)
    assert any("un-orphaned second" in change for change in changes)


def test_build_report_ignores_malformed_rows_and_surfaces_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case()])
    entry = _entry()
    monkeypatch.setattr(evals, "classify_entry", lambda _entry: ["uncovered"])
    report = evals.build_report(
        [entry],
        [
            {"eval_id": "proof_check", "passed": False},
            {"eval_id": "unknown", "passed": False},
            {"eval_id": "proof_check", "passed": None},
        ],
        {"evals": {"proof_check": {"status": "active"}}},
        ["changed"],
        ["old_eval"],
        "2026-09-06",
    )
    assert "| proof_check | missing_proof | 1 | 0 | 1 | 1.00 |" in report
    assert "uncovered" in report
    assert "changed" in report and "old_eval" in report


def test_transactional_write_and_main_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(evals, "EVALS", [_case()])
    entries = [_entry(preview="claim only")]
    monkeypatch.setattr(evals, "load_all_ratings", lambda _path: entries)

    assert evals.main([]) == 0
    rows = load_jsonl_objects(paths["results"]).records
    assert rows[0]["turn_key"].startswith("session-a|")
    registry = json.loads(paths["registry"].read_text())
    assert registry["evals"]["proof_check"]["status"] == "active"
    assert (paths["diagnostics"] / "evals_2026-09-06.md").exists()
    assert not lock_path_for(paths["results"]).exists()
    assert not lock_path_for(paths["registry"]).exists()

    before_results = paths["results"].read_text()
    assert evals.main(["--dry-run"]) == 0
    assert paths["results"].read_text() == before_results
    assert "[dry-run]" in capsys.readouterr().out

    assert evals.main(["--coverage"]) == 0
    assert "Active eval patterns" in capsys.readouterr().out

    monkeypatch.setattr(sys, "stdin", io.StringIO("claim with proof"))
    assert evals.main(["--score-stdin"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["proof_check"]["passed"] is True


def test_reconcile_write_preserves_existing_log_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(evals, "EVALS", [_case(version=2)])
    paths["registry"].write_text(
        json.dumps(
            {
                "updated": "2026-09-05",
                "evals": {
                    "proof_check": {
                        "pattern": "missing_proof",
                        "version": 1,
                        "source": "manual",
                        "status": "active",
                    }
                },
                "log": [{"date": "2026-09-05", "changes": ["old"]}],
            }
        )
    )
    entry = _entry(preview="claim only")
    rows = evals.build_result_rows([entry])
    evals._reconcile_report_and_write([entry], rows, "2026-09-06", dry_run=False)
    registry = json.loads(paths["registry"].read_text())
    assert registry["log"][0]["changes"] == ["old"]
    assert any("version proof_check" in change for change in registry["log"][1]["changes"])


@pytest.mark.parametrize(
    "text",
    [
        "```output```",
        "evidence at https://example.com/run",
        "see reports/output.txt",
        "created result.json",
        "EXIT: 0",
        "pytest reports 12 tests passed",
        "$ 12 rows",
    ],
)
def test_weak_artifact_accepts_each_supported_evidence_shape(text: str) -> None:
    assert evals.has_artifact(text) is True


def test_weak_artifact_rejects_bare_counts_and_plain_claims() -> None:
    assert evals.has_artifact("12 rows") is False
    assert evals.has_artifact("everything is done") is False


@pytest.mark.parametrize(
    "text",
    [
        "```\nreal output\n```",
        "verified at https://example.com/run",
        "exit code: 0",
        "pytest: 12 passed",
        "$ bq query returned 12 rows",
        "proof: output shows success",
        "| repo | PR |\n|---|---|\n| a | PR #12 |\n| b | PR #13 |",
        "- fixed `module.py`\n- tested `function_name`",
        "36 tests all passing",
    ],
)
def test_strong_artifact_accepts_each_supported_proof_shape(text: str) -> None:
    assert evals.has_strong_artifact(text) is True


def test_strong_artifact_rejects_paths_headers_and_bare_metrics() -> None:
    assert evals.has_strong_artifact("created module.py") is False
    assert evals.has_strong_artifact("| repo | PR |\n|---|---|") is False
    assert evals.has_strong_artifact("5000 rows") is False
    assert evals.has_strong_artifact("done") is False


def test_remaining_turn_key_registry_report_and_fallback_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evals, "EVALS", [_case()])
    assert evals._result_turn_key({"session_id": "session-only"}) == "session-only"
    assert evals._result_turn_key({}) == ""
    assert evals.normalize_registry({"evals": {1: {"status": "active"}}})["evals"] == {}

    report = evals.build_report(
        [],
        [
            "not-a-row",  # type: ignore[list-item]
            {"eval_id": "proof_check", "passed": True},
        ],
        {"evals": []},
        [],
        [],
        "2026-09-06",
    )
    assert "| proof_check | missing_proof | 1 | 1 | 0 | 0.00 |" in report

    no_fire = _entry(session="quiet", preview="nothing to evaluate", captured={"bad": []})
    assert evals.build_result_rows([no_fire]) == []
