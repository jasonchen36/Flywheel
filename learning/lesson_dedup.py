#!/usr/bin/env python3
"""
lesson_dedup.py — dedup/merge pass across auto-generated lesson FILES themselves.

consolidate_memory.py already does two dedup-adjacent things: (1) collapses the
MEMORY.md INDEX lines for autogen lessons into one compact line, and (2) reports
autogen-pattern ↔ hand-written-feedback-file overlaps (report only, never touches
hand-written files). Neither of those looks at overlap BETWEEN autogen lesson files —
54 lesson_autogen_*.md accumulate over time and near-duplicates pile up (e.g.
formatting_error / formatting_error_in_output, variable_name_confusion /
variable_naming_error / variable_name_error, stateless_session_concern /
context_retention_doubt / session_memory_doubt). ACE (Zhang et al. 2025) calls this out
explicitly: "context items are refined and deduplicated periodically." This script is
that periodic pass, scoped to the RULE BODY text (not just filenames, which is a much
weaker signal than consolidate_memory's filename-token check).

METHOD
  1. For every pair of lesson_autogen_*.md files, compute token-set overlap (Jaccard)
     between their RULE bodies (first paragraph after frontmatter — the actual
     prevention rule, not the volatile evidence/Why sections).
  2. Pairs with Jaccard >= MERGE_THRESHOLD are merge candidates.
  3. Report-only by default (never merges). --apply queues each candidate pair into
     pending_human_review.jsonl (source="lesson_dedup") for the SAME approve/reject UX.
  4. On --approve (via review_queue.py extension below): the SURVIVOR is the pattern
     with the higher occurrence_count (more evidence); the LOSER's occurrence examples
     are merged into survivor's frontmatter evidence, then the loser file is deleted.
     A human must approve — never auto-merges.

SAFE BY CONSTRUCTION: never deletes or merges without an explicit approved review
record. Original files are git-untracked already (MEMORY_DIR isn't a repo) so this
prints a full backup path before any destructive action.

Usage:
  python3 lesson_dedup.py               # report only, no writes
  python3 lesson_dedup.py --apply       # queue merge candidates for human review
  python3 lesson_dedup.py --threshold 0.35
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from harness_paths import HARNESS_HOME

MEMORY_DIR = HARNESS_HOME / "MEMORY/lessons"
DIAGNOSTICS = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
REVIEW_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"
BACKUP_DIR = HARNESS_HOME / "MEMORY/STATE/lesson_dedup_backups"

MERGE_THRESHOLD = 0.30   # Jaccard overlap on rule-body tokens
STOPWORDS = {"this", "that", "with", "from", "have", "will", "your", "what", "when",
             "where", "which", "about", "would", "could", "should", "there", "their",
             "then", "them", "they", "were", "been", "being", "into", "more", "some",
             "than", "also", "just", "like", "want", "need", "make", "made", "does",
             "done", "using", "use", "the", "and", "for", "was", "are", "before",
             "never", "always", "every", "response", "avoid", "verify", "acting",
             "occur", "occured", "occurred", "check", "rule", "apply"}

# Template-fallback lessons (no LLM available at generation time) all read
# "Avoid X — verify before acting." — pure boilerplate wrapped around the pattern
# name. Rule-body Jaccard on these is meaningless (it only measures the shared
# boilerplate), so for template lessons this script relies on NAME overlap alone
# instead of blending in a noisy rule_overlap signal.
_TEMPLATE_RULE_RE = re.compile(r"^avoid .+ [\u2014-] verify before acting\.?$", re.I)


def is_template_rule(rule: str) -> bool:
    return bool(_TEMPLATE_RULE_RE.match(rule.strip()))


def parse_lesson(path: Path) -> dict:
    txt = path.read_text()
    m_pattern = re.search(r"^\s*pattern:\s*(\S+)", txt, re.M)
    m_count = re.search(r"^\s*occurrence_count:\s*(\d+)", txt, re.M)
    m_avg = re.search(r"^\s*avg_rating:\s*([\d.]+)", txt, re.M)
    parts = txt.split("---", 2)
    body = (parts[2] if len(parts) >= 3 else txt).lstrip("\n")
    rule = body.split("\n\n", 1)[0].strip()
    return {
        "path": path,
        "pattern": m_pattern.group(1) if m_pattern else path.stem.replace("lesson_autogen_", ""),
        "occurrence_count": int(m_count.group(1)) if m_count else 0,
        "avg_rating": float(m_avg.group(1)) if m_avg else 0.0,
        "rule": rule,
        "full_text": txt,
    }


def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 3 and w not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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


def find_merge_candidates(lessons: list[dict], threshold: float) -> list[dict]:
    candidates = []
    for a, b in combinations(lessons, 2):
        # Also weight in filename/pattern-name token overlap (cheap, high-signal for
        # cases like variable_name_error / variable_naming_error where the rule bodies
        # may be phrased differently but the pattern NAME itself is nearly identical).
        name_overlap = jaccard(tokens(a["pattern"].replace("_", " ")),
                               tokens(b["pattern"].replace("_", " ")))
        a_tmpl, b_tmpl = is_template_rule(a["rule"]), is_template_rule(b["rule"])
        if a_tmpl and b_tmpl:
            # Both fell back to the boilerplate template — rule-body Jaccard would only
            # measure shared boilerplate words, not real semantic overlap. Name overlap
            # alone decides, at a stricter bar than the blended score below.
            rule_overlap = 0.0
            score = name_overlap if name_overlap >= 0.5 else 0.0
        else:
            rule_overlap = jaccard(tokens(a["rule"]), tokens(b["rule"]))
            score = max(rule_overlap, name_overlap)
        if score >= threshold:
            survivor, loser = (a, b) if a["occurrence_count"] >= b["occurrence_count"] else (b, a)
            candidates.append({
                "survivor": survivor["pattern"], "loser": loser["pattern"],
                "rule_overlap": round(rule_overlap, 3), "name_overlap": round(name_overlap, 3),
                "score": round(score, 3),
                "survivor_n": survivor["occurrence_count"], "loser_n": loser["occurrence_count"],
            })
    return sorted(candidates, key=lambda c: -c["score"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="queue merge candidates for human review")
    ap.add_argument("--threshold", type=float, default=MERGE_THRESHOLD)
    args = ap.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    files = sorted(MEMORY_DIR.glob("lesson_autogen_*.md"))
    lessons = [parse_lesson(f) for f in files]
    print(f"[lesson_dedup] {len(lessons)} autogen lesson files loaded")

    candidates = find_merge_candidates(lessons, args.threshold)

    lines = [f"# Lesson Dedup — {today}", "",
             f"Lessons scanned: {len(lessons)} | Merge candidates (overlap >= {args.threshold}): "
             f"{len(candidates)}", ""]
    if candidates:
        lines += ["| survivor (more evidence) | loser (fewer occurrences) | rule overlap | name overlap | n(surv/lose) |",
                  "|---|---|---|---|---|"]
        for c in candidates:
            lines.append(f"| {c['survivor']} | {c['loser']} | {c['rule_overlap']} | "
                        f"{c['name_overlap']} | {c['survivor_n']}/{c['loser_n']} |")
    else:
        lines.append("No merge candidates above threshold.")
    report = "\n".join(lines) + "\n"
    print(report)

    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS / f"lesson_dedup_{today}.md").write_text(report)

    if not args.apply or not candidates:
        if not args.apply and candidates:
            print("[lesson_dedup] Re-run with --apply to queue candidates for human review.")
        return 0

    review_records = load_review_queue()
    already_queued = {(r.get("note", "").split("survivor=")[-1].split(",")[0]
                       if "survivor=" in r.get("note", "") else None)
                      for r in review_records
                      if r.get("status") == "pending" and r.get("source") == "lesson_dedup"}
    queued = []
    for c in candidates:
        key = f"{c['survivor']}<-{c['loser']}"
        if key in already_queued:
            continue
        review_records.append({
            "pattern": key, "detected_at": today, "delta": None,
            "after_n": c["survivor_n"] + c["loser_n"],
            "obj_verdict": "n/a", "judge_verdict": "n/a", "status": "pending",
            "reviewed_at": None, "reviewer": None, "source": "lesson_dedup",
            "note": f"survivor={c['survivor']}, loser={c['loser']}, rule_overlap={c['rule_overlap']}, "
                    f"name_overlap={c['name_overlap']}. Approve to delete lesson_autogen_{c['loser']}.md "
                    f"(backed up first) and fold its evidence into lesson_autogen_{c['survivor']}.md.",
        })
        queued.append(key)

    if queued:
        write_review_queue(review_records)
        print(f"[lesson_dedup] Queued {len(queued)} merge candidate(s): {queued}")
    else:
        print("[lesson_dedup] All candidates already queued.")
    return 0


def merge_lessons(survivor_pattern: str, loser_pattern: str, today: str) -> bool:
    """Approving a lesson_dedup record: fold loser's evidence bullets into survivor's
    frontmatter/evidence, backup + delete the loser file. Called by review_queue.py.
    Never touches hand-written feedback_*.md files (glob is lesson_autogen_ only)."""
    survivor_path = MEMORY_DIR / f"lesson_autogen_{survivor_pattern}.md"
    loser_path = MEMORY_DIR / f"lesson_autogen_{loser_pattern}.md"
    if not survivor_path.exists() or not loser_path.exists():
        print(f"WARNING: missing file(s) — survivor_exists={survivor_path.exists()} "
              f"loser_exists={loser_path.exists()}. No merge performed.")
        return False

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{loser_pattern}_{today}.md"
    shutil.copy2(loser_path, backup_path)

    survivor_txt = survivor_path.read_text()
    loser = parse_lesson(loser_path)
    m_count = re.search(r"^(\s*occurrence_count:\s*)(\d+)", survivor_txt, re.M)
    if m_count:
        new_count = int(m_count.group(2)) + loser["occurrence_count"]
        survivor_txt = survivor_txt[:m_count.start()] + f"{m_count.group(1)}{new_count}" + survivor_txt[m_count.end():]

    merge_note = (f"\n\n**Merged from lesson_autogen_{loser_pattern}.md ({today}, "
                 f"{loser['occurrence_count']} occurrences, backed up to "
                 f"{backup_path}):**\n{loser['rule']}\n")
    survivor_txt = survivor_txt.rstrip("\n") + merge_note + "\n"
    survivor_path.write_text(survivor_txt)
    loser_path.unlink()
    print(f"Merged lesson_autogen_{loser_pattern}.md into lesson_autogen_{survivor_pattern}.md "
          f"(backup: {backup_path}). Loser file deleted.")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
