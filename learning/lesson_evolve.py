#!/usr/bin/env python3
"""
lesson_evolve.py — evolutionary variation for lessons that PROVABLY don't work.

measure_effectiveness.py already detects when a lesson's pattern is flat/regressed
(the failure rate didn't drop, or got worse, after the lesson was written). Today the
only response to that is: human manually rewrites the lesson prose by hand, one shot,
no structure. The harness post's evolutionary-search pattern (Promptbreeder, AlphaEvolve,
ShinkaEvolve) suggests something better: generate SEVERAL candidate rephrasings that
explicitly take a different angle than the one that just failed, let a human pick one (not
auto-apply — this still edits an active lesson file, so it stays gated), then let the
NEXT measure_effectiveness.py cycle judge whether the new phrasing actually works. Losing
variants are logged, not thrown away, so a pattern's mutation history is inspectable
instead of silently overwritten each time (same durability principle as pattern_promotion
ledger and lesson_dedup backups).

COOLDOWN: a pattern that was mutated in the last MUTATION_COOLDOWN_DAYS is skipped — gives
measure_effectiveness.py's before/after window (MIN_AFTER sessions) time to accumulate
before judging the new phrasing. Prevents thrashing a lesson every single session-end.

METHOD
  1. Load effectiveness_scores.json. Eligible patterns: verdict in (flat, regressed)
     [escalation_verdict tier, same as measure_effectiveness.escalate list] AND not
     mutated within cooldown AND has an existing lesson_autogen_<pattern>.md.
  2. For each eligible pattern, ask the LLM for N_VARIANTS candidate instructions,
     explicitly given the CURRENT (failing) instruction and told to take a different
     angle — not just reword it.
  3. Persist all candidates to lesson_variants.jsonl (full history, never overwritten).
  4. Queue ONE review record per pattern (source="lesson_evolve") with all candidate
     texts in the note, numbered 0..N-1.
  5. review_queue.py --approve <pattern> [--variant N] (default 0, the LLM's first/best
     candidate) applies variant N: backs up the current lesson file, replaces ONLY the
     rule paragraph (frontmatter/evidence/occurrence_count preserved), bumps
     last_updated to today. Bumping the date is intentional and mirrors
     write_lesson_file's own "bump only on material change" rule — it resets the
     before/after window so measure_effectiveness.py judges the NEW phrasing fresh,
     which is exactly the hillclimb step this script exists to trigger.

SAFE BY CONSTRUCTION: report/propose only by default. Applying still requires an
explicit human --approve through the existing review queue. Every applied mutation is
backed up before overwrite. No variant is EVER auto-applied.

Usage:
  python3 lesson_evolve.py               # propose variants for eligible patterns, no apply
  python3 lesson_evolve.py --no-llm      # report eligible patterns only, skip LLM step
  python3 lesson_evolve.py --n-variants 3
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import call_llm, _apply_pai_settings_env, MEMORY_DIR, DIAGNOSTICS  # noqa: E402
_apply_pai_settings_env()

STATE_DIR = Path.home() / ".claude/MEMORY/STATE"
SCORES_JSON = STATE_DIR / "effectiveness_scores.json"
VARIANTS_FILE = Path.home() / ".claude/MEMORY/LEARNING/SIGNALS/lesson_variants.jsonl"
REVIEW_FILE = Path.home() / ".claude/MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"
BACKUP_DIR = Path.home() / ".claude/MEMORY/STATE/lesson_evolve_backups"

N_VARIANTS = 2
MUTATION_COOLDOWN_DAYS = 7
ELIGIBLE_VERDICTS = {"flat", "regressed"}


def load_scores() -> dict:
    if not SCORES_JSON.exists():
        return {}
    try:
        return json.loads(SCORES_JSON.read_text()).get("scores", {})
    except (json.JSONDecodeError, OSError):
        return {}


def escalation_verdict(s: dict) -> str:
    """Mirror measure_effectiveness.escalation_verdict: obj tier if eval-covered,
    else judge tier if judged, else subjective."""
    if s.get("eval_covered"):
        return s.get("obj_verdict", "pending")
    if s.get("judge_covered"):
        return s.get("judge_verdict", "pending")
    return s.get("verdict", "pending")


def load_variants() -> list[dict]:
    if not VARIANTS_FILE.exists():
        return []
    out = []
    for line in VARIANTS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_variants(records: list[dict]) -> None:
    VARIANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VARIANTS_FILE, "a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def last_mutation_date(variants: list[dict], pattern: str) -> str | None:
    dates = [v["proposed_at"] for v in variants if v.get("pattern") == pattern]
    return max(dates) if dates else None


def days_since(date_str: str, today: str) -> int:
    try:
        return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(date_str, "%Y-%m-%d")).days
    except ValueError:
        return 9999


def current_rule(pattern: str) -> str | None:
    path = MEMORY_DIR / f"lesson_autogen_{pattern}.md"
    if not path.exists():
        return None
    txt = path.read_text()
    parts = txt.split("---", 2)
    body = (parts[2] if len(parts) >= 3 else txt).lstrip("\n")
    return body.split("\n\n", 1)[0].strip()


def generate_variants(pattern: str, failing_rule: str, n: int) -> list[str]:
    prompt = (
        f"An AI coding assistant keeps failing the pattern '{pattern.replace('_', ' ')}' "
        f"DESPITE having this standing instruction already in its context:\n\n"
        f'"{failing_rule}"\n\n'
        f"This instruction is NOT working — the failure rate did not improve after it was "
        f"added. Propose {n} DIFFERENT candidate instructions, each taking a genuinely "
        f"different angle than the one above (e.g. different trigger condition, a concrete "
        f"checklist instead of a general principle, a specific tool/command to run, an "
        f"explicit consequence, or reframing what to do instead of what to avoid).\n"
        f"Each candidate MUST include explicit RATIONALE and APPLICABILITY BOUNDS so the assistant understands "
        f"WHY the rule exists and WHEN it applies (e.g. 'Rule: ... | Rationale: ... | Applicability: ...'). Do NOT "
        f"just reword the same instruction.\n\n"
        f"Reply with exactly {n} lines, one candidate instruction per line, no numbering, "
        f"no preamble, no markdown."
    )
    raw = call_llm(prompt, max_tokens=400)
    if not raw:
        return []
    lines = [l.strip().lstrip("-*0123456789. ") for l in raw.strip().splitlines() if l.strip()]
    return lines[:n]


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="report eligible patterns only")
    ap.add_argument("--n-variants", type=int, default=N_VARIANTS)
    ap.add_argument("--cooldown-days", type=int, default=MUTATION_COOLDOWN_DAYS)
    args = ap.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scores = load_scores()
    variants = load_variants()

    eligible = []
    for pattern, s in scores.items():
        v = escalation_verdict(s)
        if v not in ELIGIBLE_VERDICTS:
            continue
        rule = current_rule(pattern)
        if rule is None:
            continue
        last_mut = last_mutation_date(variants, pattern)
        if last_mut and days_since(last_mut, today) < args.cooldown_days:
            continue
        eligible.append((pattern, v, rule))

    print(f"[lesson_evolve] {len(eligible)} pattern(s) eligible for mutation "
          f"(verdict in {ELIGIBLE_VERDICTS}, past cooldown)")

    lines = [f"# Lesson Evolution — {today}", "",
             f"Eligible patterns: {len(eligible)}", ""]
    if not eligible:
        lines.append("No lessons currently flat/regressed and past cooldown.")
        report = "\n".join(lines) + "\n"
        print(report)
        DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
        (DIAGNOSTICS / f"lesson_evolve_{today}.md").write_text(report)
        return 0

    review_records = load_review_queue()
    already_queued = {r["pattern"] for r in review_records
                       if r.get("status") == "pending" and r.get("source") == "lesson_evolve"}

    new_variant_records = []
    queued = []
    for pattern, verdict, rule in eligible:
        lines.append(f"## {pattern} (verdict: {verdict})")
        lines.append(f"Current (failing) instruction: {rule}")
        if args.no_llm:
            lines.append("(--no-llm: skipped candidate generation)\n")
            continue
        candidates = generate_variants(pattern, rule, args.n_variants)
        if not candidates:
            lines.append("(LLM unavailable or empty reply — skipped this run)\n")
            continue
        for i, c in enumerate(candidates):
            new_variant_records.append({
                "pattern": pattern, "variant_id": i, "text": c,
                "failing_rule": rule, "proposed_at": today, "status": "proposed",
            })
            lines.append(f"  [{i}] {c}")
        lines.append("")

        if pattern in already_queued:
            continue
        candidate_lines = "\n".join(f"  [{i}] {c}" for i, c in enumerate(candidates))
        review_records.append({
            "pattern": pattern, "detected_at": today, "delta": None,
            "after_n": 0, "obj_verdict": verdict, "judge_verdict": verdict,
            "status": "pending", "reviewed_at": None, "reviewer": None,
            "source": "lesson_evolve",
            "note": f"Lesson for '{pattern}' verdict={verdict} (didn't improve). "
                    f"Candidate rephrasings:\n{candidate_lines}\n"
                    f"Approve to apply variant 0 (or `--variant N` for another), "
                    f"which backs up the current lesson and resets its effectiveness window.",
        })
        queued.append(pattern)

    report = "\n".join(lines) + "\n"
    print(report)
    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTICS / f"lesson_evolve_{today}.md").write_text(report)

    if new_variant_records:
        append_variants(new_variant_records)
    if queued:
        write_review_queue(review_records)
        print(f"[lesson_evolve] Queued {len(queued)} pattern(s) for review: {queued}")
    return 0


def apply_variant(pattern: str, variant_id: int, today: str) -> bool:
    """Approving a lesson_evolve record: swap the lesson's rule paragraph for the chosen
    variant text. Preserves frontmatter/evidence/occurrence_count; bumps last_updated
    (intentional — resets the effectiveness before/after window for the new phrasing).
    Called by review_queue.py."""
    variants = load_variants()
    match = next((v for v in variants
                 if v["pattern"] == pattern and v["variant_id"] == variant_id
                 and v["status"] == "proposed"), None)
    if not match:
        # fall back to most recent proposal for this pattern+variant even if already applied
        # once before (re-approving after a later regression is a legitimate retry)
        match = next((v for v in reversed(variants)
                     if v["pattern"] == pattern and v["variant_id"] == variant_id), None)
    if not match:
        print(f"WARNING: no variant {variant_id} found for pattern '{pattern}'. No change made.")
        return False

    path = MEMORY_DIR / f"lesson_autogen_{pattern}.md"
    if not path.exists():
        print(f"WARNING: lesson_autogen_{pattern}.md not found. No change made.")
        return False

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{pattern}_{today}.md"
    shutil.copy2(path, backup_path)

    txt = path.read_text()
    parts = txt.split("---", 2)
    if len(parts) < 3:
        print(f"WARNING: {path.name} missing frontmatter structure. No change made.")
        return False
    frontmatter, body = parts[1], parts[2].lstrip("\n")
    body_parts = body.split("\n\n", 1)
    rest = body_parts[1] if len(body_parts) > 1 else ""
    new_body = match["text"].strip() + "\n\n" + rest

    # Preserve first_seen (measurement anchor). Only bump last_updated for content freshness.
    if re.search(r"^\s*first_seen:\s*\d{4}-\d{2}-\d{2}", frontmatter, re.M):
        pass  # keep existing first_seen
    else:
        # Derive from previous last_updated if present, else today
        m_old = re.search(r"last_updated:\s*(\d{4}-\d{2}-\d{2})", frontmatter)
        fs = m_old.group(1) if m_old else today
        frontmatter = re.sub(
            r"(pattern:\s*\S+\n)",
            rf"\1  first_seen: {fs}\n",
            frontmatter,
            count=1,
        )
    frontmatter = re.sub(r"(last_updated:\s*)\d{4}-\d{2}-\d{2}", rf"\g<1>{today}", frontmatter)

    new_txt = "---" + frontmatter + "---\n\n" + new_body
    path.write_text(new_txt)

    # Mark all proposals for this pattern+variant_id applied (idempotent history).
    for v in variants:
        if v["pattern"] == pattern and v["variant_id"] == variant_id:
            v["status"] = "applied"
    with open(VARIANTS_FILE, "w") as f:
        for v in variants:
            f.write(json.dumps(v) + "\n")

    print(f"Applied variant {variant_id} to lesson_autogen_{pattern}.md "
          f"(backup: {backup_path}). last_updated bumped to {today}; "
          f"first_seen preserved so effectiveness window does not reset.")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
