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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import (  # noqa: E402
    load_all_ratings, classify_entry, classify_other_llm,
    RATINGS_FILE, DIAGNOSTICS,
)

SIGNALS_DIR = Path.home() / ".claude/MEMORY/LEARNING/SIGNALS"
CANDIDATES_FILE = SIGNALS_DIR / "pattern_candidates.jsonl"
REVIEW_FILE = Path.home() / ".claude/MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"
SELF_IMPROVE_PY = Path(__file__).resolve().parent / "self_improve.py"

MIN_OCCURRENCES = 3   # distinct session_ids under one label before it's a promotion candidate
STOPWORDS = {"this", "that", "with", "from", "have", "will", "your", "what", "when",
             "where", "which", "about", "would", "could", "should", "there", "their",
             "then", "them", "they", "were", "been", "being", "into", "more", "some",
             "than", "also", "just", "like", "want", "need", "make", "made", "does",
             "done", "using", "use", "the", "and", "for", "was", "are"}


def load_ledger() -> list[dict]:
    if not CANDIDATES_FILE.exists():
        return []
    out = []
    for line in CANDIDATES_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_ledger(records: list[dict]) -> None:
    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATES_FILE, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def load_review_queue() -> list[dict]:
    if not REVIEW_FILE.exists():
        return []
    out = []
    for line in REVIEW_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_review_queue(records: list[dict]) -> None:
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_FILE, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def suggest_keywords(label: str, summaries: list[str], top_n: int = 6) -> list[str]:
    """Derive a starter keyword list for a promoted pattern: the label's own words plus
    the most frequent non-stopword tokens across its matched sentiment summaries. A human
    reviews/edits this list in the review-queue note before it's appended to
    PATTERN_KEYWORDS — never auto-applied blind."""
    label_words = [w for w in label.split("_") if w]
    toks = Counter()
    for s in summaries:
        for w in re.findall(r"[a-z]+", s.lower()):
            if len(w) > 3 and w not in STOPWORDS:
                toks[w] += 1
    top = [w for w, _ in toks.most_common(top_n) if w not in label_words]
    return label_words + top[:top_n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-occurrences", type=int, default=MIN_OCCURRENCES)
    args = ap.parse_args()

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

    # Merge into the durable ledger, keyed by session_id (idempotent — same entry always
    # gets its latest label, never double-counted).
    ledger = load_ledger()
    by_session = {r["session_id"]: r for r in ledger}
    id_to_entry = {e.session_id: e for e in other_entries}
    for sid, label in label_map.items():
        e = id_to_entry.get(sid)
        by_session[sid] = {
            "session_id": sid, "label": label, "labeled_at": today,
            "sentiment_summary": e.sentiment_summary if e else "",
            "rating": e.rating if e else None,
            "status": by_session.get(sid, {}).get("status", "pending"),
        }
    new_ledger = list(by_session.values())

    if not args.dry_run:
        write_ledger(new_ledger)

    # Aggregate by label (only sessions still 'pending' promotion — already-promoted
    # labels are skipped so they don't get re-queued every run).
    by_label: dict[str, list[dict]] = defaultdict(list)
    for rec in new_ledger:
        if rec.get("status") == "pending":
            by_label[rec["label"]].append(rec)

    candidates = {label: recs for label, recs in by_label.items()
                 if len(recs) >= args.min_occurrences}

    lines = [f"# Pattern Promotion — {today}", "",
             f"Ledger size: {len(new_ledger)} | Unique pending labels: {len(by_label)} "
             f"| Promotion candidates (>= {args.min_occurrences} occurrences): {len(candidates)}", ""]
    if candidates:
        lines += ["| label | occurrences | avg rating | suggested keywords |",
                  "|---|---|---|---|"]
        for label, recs in sorted(candidates.items(), key=lambda kv: -len(kv[1])):
            avg = sum(r["rating"] for r in recs if r.get("rating")) / max(
                1, sum(1 for r in recs if r.get("rating")))
            kws = suggest_keywords(label, [r["sentiment_summary"] for r in recs])
            lines.append(f"| {label} | {len(recs)} | {avg:.1f} | {', '.join(kws)} |")
    else:
        lines.append("No labels have crossed the promotion threshold yet.")
    report = "\n".join(lines) + "\n"
    print(report)

    if args.dry_run:
        print("[dry-run] no files written")
        return 0

    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS / f"pattern_promotion_{today}.md").write_text(report)

    if not candidates:
        return 0

    # Queue promotion candidates for human review — same queue/UX as lessons.
    review_records = load_review_queue()
    already_queued = {r["pattern"] for r in review_records
                       if r.get("status") == "pending" and r.get("source") == "pattern_promotion"}
    queued = []
    for label, recs in candidates.items():
        if label in already_queued:
            continue
        kws = suggest_keywords(label, [r["sentiment_summary"] for r in recs])
        avg = sum(r["rating"] for r in recs if r.get("rating")) / max(
            1, sum(1 for r in recs if r.get("rating")))
        review_records.append({
            "pattern": label, "detected_at": today, "delta": None,
            "after_n": len(recs), "obj_verdict": "n/a", "judge_verdict": "n/a",
            "status": "pending", "reviewed_at": None, "reviewer": None,
            "source": "pattern_promotion",
            "note": f"New pattern '{label}' discovered by LLM classifier across "
                    f"{len(recs)} previously-unclassified sessions (avg rating {avg:.1f}). "
                    f"Suggested keywords for PATTERN_KEYWORDS: {kws}. "
                    f"Approve to append to self_improve.py's taxonomy.",
        })
        queued.append(label)

    if queued:
        write_review_queue(review_records)
        print(f"[pattern_promotion] Queued {len(queued)} new pattern(s) for review: {queued}")
    return 0


def promote_to_taxonomy(pattern: str, keywords: list[str]) -> bool:
    """Mechanically append ONE new PATTERN_KEYWORDS entry to self_improve.py. Called by
    review_queue.py on --approve for source=pattern_promotion records. Never rewrites or
    reorders existing entries — inserts a new dict entry immediately before the closing
    brace of PATTERN_KEYWORDS, tagged with a comment noting it was promoted (and when)."""
    text = SELF_IMPROVE_PY.read_text()
    marker = "PATTERN_KEYWORDS: dict[str, list[str]] = {"
    idx = text.find(marker)
    if idx == -1:
        return False
    # Find the matching closing brace of this dict literal (first top-level '}\n' after idx).
    close_idx = text.find("\n}\n", idx)
    if close_idx == -1:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    kw_literal = ", ".join(json.dumps(k) for k in keywords)
    new_entry = (
        f'    # promoted {today} via pattern_promotion.py (LLM-discovered, human-ratified)\n'
        f'    "{pattern}": [{kw_literal}],\n'
    )
    new_text = text[:close_idx + 1] + new_entry + text[close_idx + 1:]
    SELF_IMPROVE_PY.write_text(new_text)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
