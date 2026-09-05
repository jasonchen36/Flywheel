"""Transactional state machine for Flywheel's shared human-review queue."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from state_io import exclusive_lock, load_jsonl_objects, rewrite_jsonl_unlocked

ReviewRecord = dict[str, Any]
ClaimStatus = Literal["claimed", "ambiguous", "not_found"]
_ACTIVE_STATUSES = frozenset({"pending", "processing", "action_failed"})


@dataclass(frozen=True)
class ClaimResult:
    status: ClaimStatus
    record: ReviewRecord | None = None
    matches: tuple[ReviewRecord, ...] = ()


def review_source(record: ReviewRecord) -> str:
    value = record.get("source")
    return value if isinstance(value, str) and value else "base"


def review_key(record: ReviewRecord) -> tuple[str, str] | None:
    pattern = record.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return None
    return pattern, review_source(record)


def load_reviews(path: Path) -> list[ReviewRecord]:
    return load_jsonl_objects(path).records


def enqueue_pending(path: Path, candidates: Iterable[ReviewRecord]) -> list[ReviewRecord]:
    """Append unique pending candidates without dropping concurrent queue updates."""
    prepared = [dict(candidate) for candidate in candidates]
    with exclusive_lock(path):
        records = load_reviews(path)
        active = {
            key
            for record in records
            if record.get("status") in _ACTIVE_STATUSES
            if (key := review_key(record)) is not None
        }
        added: list[ReviewRecord] = []
        for candidate in prepared:
            key = review_key(candidate)
            if key is None or key in active:
                continue
            candidate["source"] = key[1]
            candidate["status"] = "pending"
            for field in (
                "claim_id", "action_started_at", "action_completed_at", "action_error"
            ):
                candidate.pop(field, None)
            records.append(candidate)
            active.add(key)
            added.append(candidate)
        if added:
            rewrite_jsonl_unlocked(path, records)
        return added


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recover_stale_claims(
    records: list[ReviewRecord], now: datetime, stale_after_seconds: int
) -> None:
    for record in records:
        if record.get("status") != "processing":
            continue
        started = _parse_timestamp(record.get("action_started_at"))
        if started is not None and (now - started).total_seconds() < stale_after_seconds:
            continue
        record["status"] = "action_failed"
        record["action_error"] = "interrupted or stale review action"
        record["action_completed_at"] = now.isoformat()
        record.pop("claim_id", None)


def claim_review(
    path: Path,
    target: str,
    *,
    source: str | None,
    reviewer: str,
    now: datetime | None = None,
    retry_failed: bool = False,
    stale_after_seconds: int = 900,
) -> ClaimResult:
    """Atomically claim one pending review for side effects outside the queue lock."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with exclusive_lock(path):
        records = load_reviews(path)
        _recover_stale_claims(records, current_time, stale_after_seconds)
        allowed = {"pending"}
        if retry_failed:
            allowed.add("action_failed")
        matches = [
            record
            for record in records
            if record.get("pattern") == target and record.get("status") in allowed
        ]
        if source is not None:
            matches = [record for record in matches if review_source(record) == source]
        if len(matches) > 1:
            rewrite_jsonl_unlocked(path, records)
            return ClaimResult("ambiguous", matches=tuple(dict(record) for record in matches))
        if not matches:
            rewrite_jsonl_unlocked(path, records)
            return ClaimResult("not_found")

        record = matches[0]
        claim_id = uuid.uuid4().hex
        record["status"] = "processing"
        record["claim_id"] = claim_id
        record["action_started_at"] = current_time.isoformat()
        record["reviewer"] = reviewer
        try:
            attempts = int(record.get("action_attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        record["action_attempts"] = attempts + 1
        record.pop("action_error", None)
        rewrite_jsonl_unlocked(path, records)
        return ClaimResult("claimed", record=dict(record))


def finalize_claim(
    path: Path,
    claim_id: str,
    *,
    success: bool,
    now: datetime | None = None,
    error: str = "",
) -> ReviewRecord | None:
    """Finalize a claimed action as approved or action_failed."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with exclusive_lock(path):
        records = load_reviews(path)
        for record in records:
            if record.get("status") != "processing" or record.get("claim_id") != claim_id:
                continue
            record["status"] = "approved" if success else "action_failed"
            record["action_completed_at"] = current_time.isoformat()
            record.pop("claim_id", None)
            if success:
                record["reviewed_at"] = current_time.date().isoformat()
                record.pop("action_error", None)
            else:
                record["action_error"] = error or "review action failed"
            rewrite_jsonl_unlocked(path, records)
            return dict(record)
        return None


def expire_pending(
    path: Path,
    *,
    today: date,
    max_age_days: int,
) -> list[str]:
    """Atomically mark overdue pending records as auto-escalated."""
    with exclusive_lock(path):
        records = load_reviews(path)
        expired: list[str] = []
        for record in records:
            if record.get("status") != "pending":
                continue
            detected_value = record.get("detected_at")
            try:
                detected = date.fromisoformat(detected_value) if isinstance(detected_value, str) else None
            except ValueError:
                detected = None
            age = (today - detected).days if detected is not None else max_age_days + 1
            if age <= max_age_days:
                continue
            record["status"] = "auto-escalated"
            record["reviewed_at"] = today.isoformat()
            record["reviewer"] = "auto-expire"
            pattern = record.get("pattern")
            if isinstance(pattern, str):
                expired.append(pattern)
        if expired:
            rewrite_jsonl_unlocked(path, records)
        return expired


def reject_review(
    path: Path,
    target: str,
    *,
    source: str | None,
    reviewer: str,
    reason: str,
    now: datetime | None = None,
    retry_failed: bool = False,
) -> ClaimResult:
    """Atomically reject one pending record; rejection has no external side effect."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with exclusive_lock(path):
        records = load_reviews(path)
        allowed = {"pending"}
        if retry_failed:
            allowed.add("action_failed")
        matches = [
            record
            for record in records
            if record.get("pattern") == target and record.get("status") in allowed
        ]
        if source is not None:
            matches = [record for record in matches if review_source(record) == source]
        if len(matches) > 1:
            return ClaimResult("ambiguous", matches=tuple(dict(record) for record in matches))
        if not matches:
            return ClaimResult("not_found")
        record = matches[0]
        record["status"] = "rejected"
        record["reviewed_at"] = current_time.date().isoformat()
        record["reviewer"] = reviewer
        if reason:
            record["reason"] = reason
        rewrite_jsonl_unlocked(path, records)
        return ClaimResult("claimed", record=dict(record))
