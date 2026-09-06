#!/usr/bin/env python3
"""
pattern_promotion.py — closes the "other forever" gap in classify_other_llm().

self_improve.py's classify_other_llm() (see its own docstring) re-labels the SAME head
of the 'other' bucket every run without ever persisting the new pattern name anywhere —
every session-end re-discovers the same drifting failure shape and it never enters
PATTERN_KEYWORDS, so it can never get a lesson, an eval, or enforcement. This is the gap
the harness post calls "meta-methodology": letting the model propose an edit to its OWN
taxonomy, then requiring a human to ratify before the mechanism changes (same discipline
review_queue.py already uses for lessons and enforcement_promotion.py uses for hook config).

METHOD
  1. Run classify_other_llm() over the CURRENT 'other' bucket (same call self_improve.py
     already makes) — LLM proposes a snake_case label per entry.
  2. Persist every (session_id -> label) into a running ledger (pattern_candidates.jsonl)
     instead of discarding it at process exit. This makes the labeling durable across runs
     — the SAME entries keep getting the SAME label instead of independent noise each time.
  3. Aggregate the ledger: a label that has accumulated >= MIN_OCCURRENCES distinct
     session_ids becomes a PROMOTION CANDIDATE.
  4. Promotion candidates are queued into pending_human_review.jsonl (source=
     "pattern_promotion") — same queue, same --approve/--reject UX as everything else.
  5. On --approve (via review_queue.py, extended below), mechanically appends a new entry
     to PATTERN_KEYWORDS in self_improve.py: the keyword list is the label's own words plus
     the most frequent tokens across its matched sentiment_summaries. A human reviews the
     generated keyword list in the review-queue note before approving.

SAFE BY CONSTRUCTION: this script only ever WRITES to pattern_candidates.jsonl and queues
a review record. It NEVER edits self_improve.py directly except through the explicit,
human-approved promotion step, which appends one clearly-marked block (never rewrites
existing entries).

Usage:
  python3 pattern_promotion.py               # classify + accumulate ledger + report
  python3 pattern_promotion.py --dry-run     # classify + report, write nothing
  python3 pattern_promotion.py --min-occurrences 3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from review_store import enqueue_pending
from state_io import atomic_write_text, load_jsonl_objects, rewrite_jsonl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import (  # noqa: E402
    load_all_ratings, classify_entry, classify_other_llm, rating_entry_key,
    RATINGS_FILE, DIAGNOSTICS,
)

SIGNALS_DIR = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS"
CANDIDATES_FILE = SIGNALS_DIR / "pattern_candidates.jsonl"
REVIEW_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"
SELF_IMPROVE_PY = Path(__file__).resolve().parent / "self_improve.py"

MIN_OCCURRENCES = 3   # distinct session_ids under one label before it's a promotion candidate
STOPWORDS = {"this", "that", "with", "from", "have", "will", "your", "what", "when",
             "where", "which", "about", "would", "could", "should", "there", "their",
             "then", "them", "they", "were", "been", "being", "into", "more", "some",
             "than", "also", "just", "like", "want", "need", "make", "made", "does",
             "done", "using", "use", "the", "and", "for", "was", "are"}


def load_ledger() -> list[dict]:
    return load_jsonl_objects(CANDIDATES_FILE).records


def write_ledger(records: list[dict]) -> None:
    rewrite_jsonl(CANDIDATES_FILE, records)


def suggest_keywords(label: str, summaries: list[str], top_n: int = 6) -> list[str]:
    """Derive a starter keyword list for a promoted pattern: the label's own words plus
    the most frequent non-stopword tokens across its matched sentiment summaries. A human
    reviews/edits this list in the review-queue note before it's appended to
    PATTERN_KEYWORDS — never auto-applied blind."""
    label_words = [w for w in label.split("_") if w]
    toks: Counter[str] = Counter()
    for s in summaries:
        for w in re.findall(r"[a-z]+", s.lower()):
            if len(w) > 3 and w not in STOPWORDS:
                toks[w] += 1
    top = [w for w, _ in toks.most_common(top_n) if w not in label_words]
    return label_words + top[:top_n]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-occurrences", type=int, default=MIN_OCCURRENCES)
    args = ap.parse_args(argv)
    if args.min_occurrences <= 0:
        print("[pattern_promotion] min-occurrences must be positive")
        return 2

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entries = load_all_ratings(RATINGS_FILE)
    for e in entries:
        e.patterns = classify_entry(e)  # against CURRENT PATTERN_KEYWORDS
    other_entries = [e for e in entries if e.patterns == ["other"]]

    print(f"[pattern_promotion] {len(other_entries)} entries currently unclassified ('other')")

    label_map: dict[str, str] = {}
    if other_entries:
        label_map = classify_other_llm(other_entries)
        print(f"[pattern_promotion] LLM labeled {len(label_map)}/{len(other_entries)}")

    # Merge into the durable ledger by exact turn identity. Legacy session-only rows
    # remain readable, while new rows cannot collide when one session has multiple turns.
    ledger = load_ledger()
    by_turn: dict[str, dict] = {}
    for record in ledger:
        turn_key = record.get("turn_key")
        session_id = record.get("session_id")
        key = turn_key if isinstance(turn_key, str) and turn_key else session_id
        if isinstance(key, str) and key:
            by_turn[key] = record
    id_to_entry = {
        key: entry
        for entry in other_entries
        if (key := rating_entry_key(entry))
    }
    for turn_key, label in label_map.items():
        if not isinstance(turn_key, str) or not isinstance(label, str) or not valid_pattern(label):
            continue
        representative = id_to_entry.get(turn_key)
        if representative is None:
            continue
        by_turn[turn_key] = {
            "turn_key": turn_key,
            "session_id": getattr(representative, "session_id", ""),
            "timestamp": getattr(representative, "timestamp", ""),
            "label": label,
            "labeled_at": today,
            "sentiment_summary": getattr(representative, "sentiment_summary", ""),
            "rating": getattr(representative, "rating", None),
            "status": by_turn.get(turn_key, {}).get("status", "pending"),
        }
    new_ledger = list(by_turn.values())

    if not args.dry_run:
        write_ledger(new_ledger)

    # Aggregate by label (only sessions still 'pending' promotion — already-promoted
    # labels are skipped so they don't get re-queued every run).
    by_label: dict[str, list[dict]] = defaultdict(list)
    for rec in new_ledger:
        record_label = rec.get("label")
        if (
            rec.get("status") == "pending"
            and isinstance(record_label, str)
            and valid_pattern(record_label)
        ):
            by_label[record_label].append(rec)

    def distinct_session_count(records: list[dict]) -> int:
        return len({
            str(record.get("session_id") or record.get("turn_key") or "")
            for record in records
            if record.get("session_id") or record.get("turn_key")
        })

    candidates = {
        label: recs
        for label, recs in by_label.items()
        if distinct_session_count(recs) >= args.min_occurrences
    }

    lines = [f"# Pattern Promotion — {today}", "",
             f"Ledger size: {len(new_ledger)} | Unique pending labels: {len(by_label)} "
             f"| Promotion candidates (>= {args.min_occurrences} occurrences): {len(candidates)}", ""]
    if candidates:
        lines += ["| label | occurrences | avg rating | suggested keywords |",
                  "|---|---|---|---|"]
        for label, recs in sorted(
            candidates.items(),
            key=lambda item: -distinct_session_count(item[1]),
        ):
            ratings = [r["rating"] for r in recs if isinstance(r.get("rating"), (int, float))]
            avg = sum(ratings) / max(1, len(ratings))
            summaries = [str(r.get("sentiment_summary") or "") for r in recs]
            kws = suggest_keywords(label, summaries)
            lines.append(
                f"| {label} | {distinct_session_count(recs)} | {avg:.1f} | {', '.join(kws)} |"
            )
    else:
        lines.append("No labels have crossed the promotion threshold yet.")
    report = "\n".join(lines) + "\n"
    print(report)

    if args.dry_run:
        print("[dry-run] no files written")
        return 0

    atomic_write_text(DIAGNOSTICS / f"pattern_promotion_{today}.md", report)

    if not candidates:
        return 0

    # Queue promotion candidates for human review — same queue/UX as lessons.
    pending_rows: list[dict] = []
    for label, recs in candidates.items():
        summaries = [str(r.get("sentiment_summary") or "") for r in recs]
        kws = suggest_keywords(label, summaries)
        ratings = [r["rating"] for r in recs if isinstance(r.get("rating"), (int, float))]
        avg = sum(ratings) / max(1, len(ratings))
        occurrence_count = distinct_session_count(recs)
        pending_rows.append({
            "pattern": label, "detected_at": today, "delta": None,
            "after_n": occurrence_count, "obj_verdict": "n/a", "judge_verdict": "n/a",
            "status": "pending", "reviewed_at": None, "reviewer": None,
            "source": "pattern_promotion",
            "note": f"New pattern '{label}' discovered by LLM classifier across "
                    f"{occurrence_count} previously-unclassified sessions (avg rating {avg:.1f}). "
                    f"Suggested keywords for PATTERN_KEYWORDS: {kws}. "
                    f"Approve to append to self_improve.py's taxonomy.",
        })
    added = enqueue_pending(REVIEW_FILE, pending_rows)
    queued = [record["pattern"] for record in added]
    if queued:
        print(f"[pattern_promotion] Queued {len(queued)} new pattern(s) for review: {queued}")
    return 0


def valid_pattern(pattern: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]*", pattern))


def promote_to_taxonomy(pattern: str, keywords: list[str]) -> bool:
    """Mechanically append ONE new PATTERN_KEYWORDS entry to self_improve.py. Called by
    review_queue.py on --approve for source=pattern_promotion records. Never rewrites or
    reorders existing entries — inserts a new dict entry immediately before the closing
    brace of PATTERN_KEYWORDS, tagged with a comment noting it was promoted (and when)."""
    if not valid_pattern(pattern):
        return False
    normalized_keywords = list(dict.fromkeys(
        keyword.strip().lower()
        for keyword in keywords
        if isinstance(keyword, str) and keyword.strip()
    ))
    if not normalized_keywords:
        return False
    text = SELF_IMPROVE_PY.read_text()
    marker = "PATTERN_KEYWORDS: dict[str, list[str]] = {"
    idx = text.find(marker)
    if idx == -1:
        return False
    # Find the matching closing brace of this dict literal (first top-level '}\n' after idx).
    close_idx = text.find("\n}\n", idx)
    if close_idx == -1:
        return False
    if f"    {json.dumps(pattern)}:" in text[idx:close_idx]:
        return True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    kw_literal = ", ".join(json.dumps(keyword) for keyword in normalized_keywords)
    new_entry = (
        f'    # promoted {today} via pattern_promotion.py (LLM-discovered, human-ratified)\n'
        f"    {json.dumps(pattern)}: [{kw_literal}],\n"
    )
    new_text = text[:close_idx + 1] + new_entry + text[close_idx + 1:]
    atomic_write_text(SELF_IMPROVE_PY, new_text)
    return True


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
