from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import review_queue
from review_store import enqueue_pending, load_reviews
from state_io import load_jsonl_objects

TODAY = "2026-09-05"


@pytest.fixture
def review_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    review_file = tmp_path / "pending_human_review.jsonl"
    monkeypatch.setattr(review_queue, "REVIEW_FILE", review_file)
    monkeypatch.setattr(review_queue, "AUDIT_FILE", tmp_path / "review_audit.jsonl")
    monkeypatch.setattr(review_queue, "SCORES_FILE", tmp_path / "scores.json")
    return review_file


def _row(pattern: str, source: str = "base", **values: object) -> dict:
    return {
        "pattern": pattern,
        "source": source,
        "status": "pending",
        "detected_at": TODAY,
        "delta": 0.2,
        "after_n": 5,
        **values,
    }


def test_approval_is_claimed_finalized_and_audited(
    review_paths: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    enqueue_pending(review_paths, [_row("alpha", delta="bad", after_n="bad")])
    monkeypatch.setattr(review_queue, "_run_approval_side_effect", lambda *_args: True)

    assert review_queue.cmd_approve_reject(
        [], "alpha", "approved", "looks good", TODAY
    ) == 0
    record = load_reviews(review_paths)[0]
    assert record["status"] == "approved"
    assert record["reviewer"] == "USER"
    assert record["action_attempts"] == 1
    assert "claim_id" not in record
    audit = load_jsonl_objects(review_queue.AUDIT_FILE).records
    assert audit[-1]["action"] == "approved"
    assert audit[-1]["delta"] == 0.0
    assert audit[-1]["after_n"] == 0
    assert "Pattern enters escalation" in capsys.readouterr().out


def test_failed_side_effect_requires_explicit_retry(
    review_paths: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    enqueue_pending(review_paths, [_row("alpha", "lesson_evolve")])
    monkeypatch.setattr(review_queue, "_run_approval_side_effect", lambda *_args: False)
    assert review_queue.cmd_approve_reject(
        [], "alpha", "approved", "", TODAY, source="lesson_evolve"
    ) == 2
    failed = load_reviews(review_paths)[0]
    assert failed["status"] == "action_failed"
    assert "returned failure" in failed["action_error"]
    assert "--retry-failed" in capsys.readouterr().out

    assert review_queue.cmd_approve_reject(
        [], "alpha", "approved", "", TODAY, source="lesson_evolve"
    ) == 1
    monkeypatch.setattr(review_queue, "_run_approval_side_effect", lambda *_args: True)
    assert review_queue.cmd_approve_reject(
        [], "alpha", "approved", "", TODAY, source="lesson_evolve", retry_failed=True
    ) == 0
    approved = load_reviews(review_paths)[0]
    assert approved["status"] == "approved"
    assert approved["action_attempts"] == 2


def test_side_effect_exception_and_missing_finalization_are_visible(
    review_paths: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    enqueue_pending(review_paths, [_row("explode")])

    def explode(*_args: object) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(review_queue, "_run_approval_side_effect", explode)
    assert review_queue.cmd_approve_reject([], "explode", "approved", "", TODAY) == 2
    assert "RuntimeError: boom" in load_reviews(review_paths)[0]["action_error"]

    enqueue_pending(review_paths, [_row("vanish")])
    monkeypatch.setattr(review_queue, "_run_approval_side_effect", lambda *_args: True)
    monkeypatch.setattr(review_queue, "finalize_claim", lambda *_args, **_kwargs: None)
    assert review_queue.cmd_approve_reject([], "vanish", "approved", "", TODAY) == 2
    assert "disappeared before finalization" in capsys.readouterr().out
    vanish = next(row for row in load_reviews(review_paths) if row["pattern"] == "vanish")
    assert vanish["status"] == "processing"


def test_ambiguous_and_missing_review_selection_is_non_mutating(
    review_paths: Path, capsys: pytest.CaptureFixture[str]
):
    enqueue_pending(review_paths, [_row("same"), _row("same", "lesson_evolve")])
    assert review_queue.cmd_approve_reject([], "same", "approved", "", TODAY) == 1
    assert "AMBIGUOUS" in capsys.readouterr().out
    assert review_queue.cmd_approve_reject(
        [], "same", "approved", "", TODAY, source="missing"
    ) == 1
    assert all(row["status"] == "pending" for row in load_reviews(review_paths))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("enforcement_promotion", "Left at 'warn'"),
        ("pattern_promotion", "NOT promoted"),
        ("held_out_regression", "dismissed as noise"),
        ("lesson_dedup", "No merge performed"),
        ("lesson_evolve", "No variant applied"),
        ("base", "revise lesson_autogen"),
    ],
)
def test_rejection_is_atomic_and_never_runs_approval_side_effect(
    review_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    expected: str,
):
    enqueue_pending(review_paths, [_row("alpha", source)])

    def forbidden(*_args: object) -> bool:
        raise AssertionError("approval side effect must not run")

    monkeypatch.setattr(review_queue, "_run_approval_side_effect", forbidden)
    assert review_queue.cmd_approve_reject(
        [], "alpha", "rejected", "not useful", TODAY, source=source
    ) == 0
    record = load_reviews(review_paths)[0]
    assert record["status"] == "rejected"
    assert record["reason"] == "not useful"
    assert expected in capsys.readouterr().out


def test_approval_side_effect_dispatch(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    monkeypatch.setattr(
        review_queue, "_promote_config_only_pattern",
        lambda *_args: calls.append("config") or True,
    )
    monkeypatch.setattr(
        review_queue, "_apply_lesson_variant",
        lambda *_args: calls.append("evolve") or True,
    )
    monkeypatch.setattr(
        review_queue, "_promote_pattern_to_taxonomy",
        lambda *_args: calls.append("taxonomy") or True,
    )
    monkeypatch.setattr(
        review_queue, "_merge_dedup_pattern",
        lambda *_args: calls.append("dedup") or True,
    )

    for source in (
        "enforcement_promotion",
        "lesson_evolve",
        "pattern_promotion",
        "lesson_dedup",
    ):
        assert review_queue._run_approval_side_effect(_row("p", source), 2, TODAY)
    assert review_queue._run_approval_side_effect(_row("p", "base"), 0, TODAY)
    assert calls == ["config", "evolve", "taxonomy", "dedup"]


def test_auto_drain_claims_with_automation_identity(
    review_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    enqueue_pending(review_paths, [_row("alpha")])
    monkeypatch.setattr(review_queue, "_run_approval_side_effect", lambda *_args: True)
    assert review_queue.cmd_auto_drain(load_reviews(review_paths), 0, "base", TODAY) == 0
    assert load_reviews(review_paths)[0]["reviewer"] == "auto-drain"
    assert review_queue.cmd_auto_drain(load_reviews(review_paths), 0, "unknown", TODAY) == 0
    assert review_queue.cmd_auto_drain(load_reviews(review_paths), 0, "base", TODAY) == 0


def test_bulk_approval_executes_actions_and_reports_partial_failure(
    review_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    enqueue_pending(review_paths, [_row("good"), _row("bad", "lesson_evolve")])
    monkeypatch.setattr(
        review_queue,
        "_run_approval_side_effect",
        lambda record, _variant, _today: record["pattern"] == "good",
    )
    assert review_queue.cmd_bulk_approve(load_reviews(review_paths), 0, True, TODAY) == 1
    statuses = {row["pattern"]: row["status"] for row in load_reviews(review_paths)}
    assert statuses == {"good": "approved", "bad": "action_failed"}


def test_bulk_approval_confirmation_can_abort(
    review_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    enqueue_pending(review_paths, [_row("alpha")])
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert review_queue.cmd_bulk_approve(load_reviews(review_paths), 0, False, TODAY) == 1
    assert load_reviews(review_paths)[0]["status"] == "pending"


def test_list_surfaces_processing_and_failed_actions(
    review_paths: Path, capsys: pytest.CaptureFixture[str]
):
    from state_io import rewrite_jsonl

    rewrite_jsonl(
        review_paths,
        [
            _row(
                "running",
                status="processing",
                detected_at=42,
                action_started_at="2026-09-05T12:00:00+00:00",
            ),
            _row(
                "failed",
                "lesson_evolve",
                status="action_failed",
                action_attempts=2,
                action_error="variant unavailable",
            ),
        ],
    )
    assert review_queue.cmd_list(load_reviews(review_paths), TODAY) == 0
    output = capsys.readouterr().out
    assert "processing: 1 | failed: 1" in output
    assert "Approval actions still processing" in output
    assert "variant unavailable" in output
    assert "--approve failed --source lesson_evolve --retry-failed" in output
    assert "--reject failed --source lesson_evolve --retry-failed" in output
    assert "Queue empty" not in output
