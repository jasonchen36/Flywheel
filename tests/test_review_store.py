from __future__ import annotations

import multiprocessing
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

from review_store import (
    claim_review,
    enqueue_pending,
    expire_pending,
    finalize_claim,
    load_reviews,
    reject_review,
    review_key,
    review_source,
)

NOW = datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc)


def _candidate(pattern: str, source: object = "base", **extra: object) -> dict:
    return {"pattern": pattern, "source": source, "status": "pending", **extra}


def _enqueue_worker(path: str, start: multiprocessing.synchronize.Event, pattern: str) -> None:
    start.wait()
    enqueue_pending(Path(path), [_candidate(pattern, "concurrent")])


def test_review_keys_normalize_sources_and_reject_invalid_patterns():
    assert review_source({}) == "base"
    assert review_source({"source": ""}) == "base"
    assert review_source({"source": 3}) == "base"
    assert review_source({"source": "lesson_evolve"}) == "lesson_evolve"
    assert review_key({}) is None
    assert review_key({"pattern": 4}) is None
    assert review_key({"pattern": ""}) is None
    assert review_key({"pattern": "p"}) == ("p", "base")


def test_enqueue_pending_preserves_existing_and_deduplicates_active_keys(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    first = enqueue_pending(
        path,
        [
            _candidate("alpha", ""),
            _candidate("alpha", "base"),
            _candidate("beta", "lesson_evolve", status="approved"),
            _candidate("", "base"),
        ],
    )
    assert [(row["pattern"], row["source"]) for row in first] == [
        ("alpha", "base"),
        ("beta", "lesson_evolve"),
    ]
    assert all(row["status"] == "pending" for row in first)

    records = load_reviews(path)
    records[0]["status"] = "approved"
    from state_io import rewrite_jsonl

    rewrite_jsonl(path, records)
    added = enqueue_pending(
        path,
        [
            _candidate("alpha", "base", claim_id="old", action_error="old"),
            _candidate("beta", "lesson_evolve"),
        ],
    )
    assert [row["pattern"] for row in added] == ["alpha"]
    assert "claim_id" not in added[0]
    assert "action_error" not in added[0]
    assert enqueue_pending(path, [_candidate("beta", "lesson_evolve")]) == []


def test_concurrent_enqueue_preserves_unique_records_and_deduplicates_same_key(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    context = multiprocessing.get_context("fork")
    start = context.Event()
    patterns = ["one", "two", "three", "four", "same", "same"]
    workers = [context.Process(target=_enqueue_worker, args=(str(path), start, item)) for item in patterns]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert {row["pattern"] for row in load_reviews(path)} == {
        "one",
        "two",
        "three",
        "four",
        "same",
    }


def test_claim_reports_ambiguous_and_not_found(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    enqueue_pending(path, [_candidate("same", "base"), _candidate("same", "lesson_evolve")])
    ambiguous = claim_review(path, "same", source=None, reviewer="USER", now=NOW)
    assert ambiguous.status == "ambiguous"
    assert len(ambiguous.matches) == 2

    missing_source = claim_review(
        path, "same", source="lesson_dedup", reviewer="USER", now=NOW
    )
    assert missing_source.status == "not_found"
    assert claim_review(path, "missing", source=None, reviewer="USER").status == "not_found"


def test_claim_and_successful_finalize_are_durable(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    enqueue_pending(path, [_candidate("alpha", "base", action_attempts="bad", action_error="old")])
    claimed = claim_review(path, "alpha", source="base", reviewer="alice", now=NOW)
    assert claimed.status == "claimed"
    assert claimed.record is not None
    assert claimed.record["status"] == "processing"
    assert claimed.record["action_attempts"] == 1
    assert "action_error" not in claimed.record

    claim_id = claimed.record["claim_id"]
    finalized = finalize_claim(path, claim_id, success=True, now=NOW + timedelta(seconds=1))
    assert finalized is not None
    assert finalized["status"] == "approved"
    assert finalized["reviewed_at"] == "2026-09-05"
    assert "claim_id" not in finalized
    assert "action_error" not in finalized
    assert finalize_claim(path, claim_id, success=True, now=NOW) is None


def test_failed_claim_requires_explicit_retry_and_counts_attempts(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    enqueue_pending(path, [_candidate("alpha", "base", action_attempts=2)])
    first = claim_review(path, "alpha", source="base", reviewer="alice", now=NOW)
    assert first.record is not None
    failed = finalize_claim(
        path,
        first.record["claim_id"],
        success=False,
        now=NOW + timedelta(seconds=1),
        error="side effect failed",
    )
    assert failed is not None
    assert failed["status"] == "action_failed"
    assert failed["action_error"] == "side effect failed"
    assert "reviewed_at" not in failed

    assert claim_review(path, "alpha", source="base", reviewer="alice", now=NOW).status == "not_found"
    retry = claim_review(
        path,
        "alpha",
        source="base",
        reviewer="bob",
        now=NOW + timedelta(seconds=2),
        retry_failed=True,
    )
    assert retry.record is not None
    assert retry.record["action_attempts"] == 4
    default_error = finalize_claim(
        path, retry.record["claim_id"], success=False, now=NOW + timedelta(seconds=3)
    )
    assert default_error is not None
    assert default_error["action_error"] == "review action failed"


def test_stale_and_malformed_processing_claims_recover_for_retry(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    from state_io import rewrite_jsonl

    rewrite_jsonl(
        path,
        [
            _candidate("fresh", status="processing", action_started_at=NOW.isoformat()),
            _candidate("naive", status="processing", action_started_at="2026-09-05T18:00:00"),
            _candidate(
                "stale",
                status="processing",
                action_started_at=(NOW - timedelta(hours=1)).isoformat(),
                claim_id="old",
            ),
            _candidate("invalid", status="processing", action_started_at="bad", claim_id="old"),
            _candidate("missing", status="processing", claim_id="old"),
            _candidate("pending", status="pending"),
        ],
    )
    claimed = claim_review(
        path,
        "stale",
        source="base",
        reviewer="retry",
        now=NOW,
        retry_failed=True,
    )
    assert claimed.status == "claimed"
    records = {row["pattern"]: row for row in load_reviews(path)}
    assert records["fresh"]["status"] == "processing"
    assert records["naive"]["status"] == "processing"
    assert records["invalid"]["status"] == "action_failed"
    assert records["missing"]["status"] == "action_failed"
    assert records["invalid"]["action_error"].startswith("interrupted")
    assert "claim_id" not in records["invalid"]


def test_expire_pending_updates_only_overdue_records(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    from state_io import rewrite_jsonl

    rewrite_jsonl(
        path,
        [
            _candidate("old", detected_at="2026-08-01"),
            _candidate("fresh", detected_at="2026-09-01"),
            _candidate("invalid", detected_at="bad"),
            _candidate("missing"),
            _candidate("non_string", detected_at=42),
            _candidate("done", detected_at="2026-08-01", status="approved"),
            {"status": "pending", "detected_at": "2026-08-01"},
        ],
    )
    expired = expire_pending(path, today=date(2026, 9, 5), max_age_days=14)
    assert expired == ["old", "invalid", "missing", "non_string"]
    records = {row.get("pattern", "no-pattern"): row for row in load_reviews(path)}
    for pattern in expired:
        assert records[pattern]["status"] == "auto-escalated"
        assert records[pattern]["reviewed_at"] == "2026-09-05"
        assert records[pattern]["reviewer"] == "auto-expire"
    assert records["fresh"]["status"] == "pending"
    assert records["done"]["status"] == "approved"
    assert records["no-pattern"]["status"] == "auto-escalated"
    assert expire_pending(path, today=date(2026, 9, 5), max_age_days=14) == []


def test_reject_handles_ambiguity_missing_source_and_optional_reason(tmp_path: Path):
    path = tmp_path / "reviews.jsonl"
    enqueue_pending(path, [_candidate("same", "base"), _candidate("same", "lesson_evolve")])
    assert reject_review(
        path, "same", source=None, reviewer="USER", reason="", now=NOW
    ).status == "ambiguous"
    assert reject_review(
        path, "same", source="missing", reviewer="USER", reason="", now=NOW
    ).status == "not_found"

    rejected = reject_review(
        path,
        "same",
        source="lesson_evolve",
        reviewer="alice",
        reason="not useful",
        now=NOW,
    )
    assert rejected.record is not None
    assert rejected.record["status"] == "rejected"
    assert rejected.record["reason"] == "not useful"
    assert reject_review(
        path, "same", source="lesson_evolve", reviewer="alice", reason=""
    ).status == "not_found"

    base = reject_review(path, "same", source="base", reviewer="bob", reason="", now=NOW)
    assert base.record is not None
    assert "reason" not in base.record

    failed_path = tmp_path / "failed.jsonl"
    from state_io import rewrite_jsonl

    rewrite_jsonl(failed_path, [_candidate("failed", status="action_failed")])
    assert reject_review(
        failed_path, "failed", source="base", reviewer="bob", reason="", now=NOW
    ).status == "not_found"
    resolved = reject_review(
        failed_path,
        "failed",
        source="base",
        reviewer="bob",
        reason="abandon retry",
        now=NOW,
        retry_failed=True,
    )
    assert resolved.record is not None
    assert resolved.record["status"] == "rejected"
