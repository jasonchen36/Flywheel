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
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from review_store import enqueue_pending, load_reviews
from state_io import (
    append_jsonl_many,
    atomic_write_text,
    exclusive_locks,
    load_jsonl_objects,
    rewrite_jsonl_unlocked,
    try_read_json_object,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import call_llm, _apply_pai_settings_env, MEMORY_DIR, DIAGNOSTICS  # noqa: E402
_apply_pai_settings_env()

STATE_DIR = HARNESS_HOME / "MEMORY/STATE"
SCORES_JSON = STATE_DIR / "effectiveness_scores.json"
VARIANTS_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/lesson_variants.jsonl"
REVIEW_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/pending_human_review.jsonl"
BACKUP_DIR = HARNESS_HOME / "MEMORY/STATE/lesson_evolve_backups"

N_VARIANTS = 2
MUTATION_COOLDOWN_DAYS = 7
ELIGIBLE_VERDICTS = {"flat", "regressed"}
MAX_VARIANTS = 10
MAX_VARIANT_CHARS = 1200
_PATTERN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def valid_pattern(pattern: object) -> bool:
    return isinstance(pattern, str) and bool(_PATTERN_RE.fullmatch(pattern))


def load_scores() -> dict[str, dict]:
    data, _error = try_read_json_object(SCORES_JSON)
    scores = data.get("scores")
    if not isinstance(scores, dict):
        return {}
    return {
        pattern: score
        for pattern, score in scores.items()
        if valid_pattern(pattern) and isinstance(score, dict)
    }


def escalation_verdict(score: dict) -> str:
    """Return the highest-priority well-formed verdict from persisted score state."""
    if score.get("eval_covered") is True:
        value = score.get("obj_verdict")
    elif score.get("judge_covered") is True:
        value = score.get("judge_verdict")
    else:
        value = score.get("verdict")
    return value if isinstance(value, str) else ""


def load_variants() -> list[dict]:
    return load_jsonl_objects(VARIANTS_FILE).records


def append_variants(records: list[dict]) -> None:
    append_jsonl_many(VARIANTS_FILE, records)


def _date_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def last_mutation_date(variants: list[dict], pattern: str) -> str | None:
    dates = [
        normalized
        for variant in variants
        if variant.get("pattern") == pattern
        and (normalized := _date_value(variant.get("proposed_at"))) is not None
    ]
    return max(dates) if dates else None


def days_since(date_str: str, today: str) -> int:
    start = _date_value(date_str)
    end = _date_value(today)
    if start is None or end is None:
        return 9999
    return (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days


def current_rule(pattern: str) -> str | None:
    if not valid_pattern(pattern):
        return None
    path = MEMORY_DIR / f"lesson_autogen_{pattern}.md"
    if not path.is_file():
        return None
    try:
        txt = path.read_text()
    except OSError:
        return None
    parts = txt.split("---", 2)
    body = (parts[2] if len(parts) >= 3 else txt).lstrip("\n")
    return body.split("\n\n", 1)[0].strip()


def normalize_variant_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())[:MAX_VARIANT_CHARS]
    return text or None


def valid_generated_variant(text: str) -> bool:
    lowered = text.lower()
    return all(label in lowered for label in ("rule:", "rationale:", "applicability:"))


def proposal_id(pattern: str, proposed_at: str, variant_id: int, text: str) -> str:
    payload = f"{pattern}\0{proposed_at}\0{variant_id}\0{text}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def generate_variants(pattern: str, failing_rule: str, n: int) -> list[str]:
    if not valid_pattern(pattern) or not 1 <= n <= MAX_VARIANTS:
        return []
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
    candidates: list[str] = []
    for line in raw.strip().splitlines():
        candidate = normalize_variant_text(line.strip().lstrip("-*0123456789. "))
        if (
            candidate is not None
            and valid_generated_variant(candidate)
            and candidate not in candidates
        ):
            candidates.append(candidate)
        if len(candidates) >= n:
            break
    return candidates


def load_review_queue() -> list[dict]:
    return load_reviews(REVIEW_FILE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="report eligible patterns only")
    ap.add_argument("--n-variants", type=int, default=N_VARIANTS)
    ap.add_argument("--cooldown-days", type=int, default=MUTATION_COOLDOWN_DAYS)
    args = ap.parse_args(argv)
    if not 1 <= args.n_variants <= MAX_VARIANTS:
        print(f"[lesson_evolve] n-variants must be between 1 and {MAX_VARIANTS}")
        return 2
    if args.cooldown_days < 0:
        print("[lesson_evolve] cooldown-days must be non-negative")
        return 2

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
        atomic_write_text(DIAGNOSTICS / f"lesson_evolve_{today}.md", report)
        return 0

    review_records = load_review_queue()
    already_queued = {
        review_pattern
        for record in review_records
        if record.get("status") == "pending"
        and record.get("source") == "lesson_evolve"
        and isinstance((review_pattern := record.get("pattern")), str)
        and valid_pattern(review_pattern)
    }
    known_proposals = {
        value
        for record in variants
        if isinstance((value := record.get("proposal_id")), str) and value
    }

    new_variant_records = []
    pending_rows: list[dict] = []
    for pattern, verdict, rule in eligible:
        lines.append(f"## {pattern} (verdict: {verdict})")
        lines.append(f"Current (failing) instruction: {rule}")
        if pattern in already_queued:
            lines.append("(existing lesson-evolution review is still pending — no new batch generated)\n")
            continue
        if args.no_llm:
            lines.append("(--no-llm: skipped candidate generation)\n")
            continue
        candidates = generate_variants(pattern, rule, args.n_variants)
        if not candidates:
            lines.append("(LLM unavailable or empty reply — skipped this run)\n")
            continue
        batch_id = hashlib.sha256(
            f"{pattern}\0{today}\0".encode() + "\0".join(candidates).encode()
        ).hexdigest()[:20]
        for i, candidate in enumerate(candidates):
            candidate_id = proposal_id(pattern, today, i, candidate)
            if candidate_id not in known_proposals:
                new_variant_records.append({
                    "proposal_id": candidate_id,
                    "batch_id": batch_id,
                    "pattern": pattern,
                    "variant_id": i,
                    "text": candidate,
                    "failing_rule": rule,
                    "proposed_at": today,
                    "status": "proposed",
                })
                known_proposals.add(candidate_id)
            lines.append(f"  [{i}] {candidate}")
        lines.append("")

        candidate_lines = "\n".join(f"  [{i}] {c}" for i, c in enumerate(candidates))
        pending_rows.append({
            "pattern": pattern, "detected_at": today, "delta": None,
            "after_n": 0, "obj_verdict": verdict, "judge_verdict": verdict,
            "status": "pending", "reviewed_at": None, "reviewer": None,
            "source": "lesson_evolve",
            "note": f"Lesson for '{pattern}' verdict={verdict} (didn't improve). "
                    f"Candidate rephrasings:\n{candidate_lines}\n"
                    f"Approve to apply variant 0 (or `--variant N` for another), "
                    f"which backs up the current lesson while preserving its effectiveness window.",
        })

    report = "\n".join(lines) + "\n"
    print(report)
    atomic_write_text(DIAGNOSTICS / f"lesson_evolve_{today}.md", report)

    if new_variant_records:
        append_variants(new_variant_records)
    added = enqueue_pending(REVIEW_FILE, pending_rows)
    queued = [record["pattern"] for record in added]
    if queued:
        print(f"[lesson_evolve] Queued {len(queued)} pattern(s) for review: {queued}")
    return 0


def _backup_path(pattern: str, today: str, selected_id: str) -> Path:
    base = BACKUP_DIR / f"{pattern}_{today}_{selected_id[:12]}.md"
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = base.with_name(f"{base.stem}.{suffix}{base.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def apply_variant(pattern: str, variant_id: int, today: str) -> bool:
    """Apply the latest exact proposed variant and commit its ledger transition."""
    if (
        not valid_pattern(pattern)
        or isinstance(variant_id, bool)
        or not isinstance(variant_id, int)
        or variant_id < 0
        or _date_value(today) is None
    ):
        print("WARNING: invalid lesson variant request. No change made.")
        return False

    path = MEMORY_DIR / f"lesson_autogen_{pattern}.md"
    with exclusive_locks([VARIANTS_FILE, path]):
        variants = load_variants()
        proposed_indices = [
            index
            for index, candidate in enumerate(variants)
            if candidate.get("pattern") == pattern
            and candidate.get("status") == "proposed"
            and normalize_variant_text(candidate.get("text")) is not None
        ]
        if not proposed_indices:
            print(f"WARNING: no proposed variant {variant_id} found for pattern '{pattern}'. No change made.")
            return False
        latest = variants[proposed_indices[-1]]
        latest_batch_value = latest.get("batch_id")
        latest_batch = (
            latest_batch_value
            if isinstance(latest_batch_value, str) and latest_batch_value
            else f"legacy:{latest.get('proposed_at', '')}"
        )
        match_index: int | None = None
        selected_text: str | None = None
        for index in reversed(proposed_indices):
            candidate = variants[index]
            candidate_id = candidate.get("variant_id")
            batch_value = candidate.get("batch_id")
            candidate_batch = (
                batch_value
                if isinstance(batch_value, str) and batch_value
                else f"legacy:{candidate.get('proposed_at', '')}"
            )
            if (
                candidate_batch == latest_batch
                and isinstance(candidate_id, int)
                and not isinstance(candidate_id, bool)
                and candidate_id == variant_id
                and (selected_text := normalize_variant_text(candidate.get("text"))) is not None
            ):
                match_index = index
                break
        if match_index is None or selected_text is None:
            print(f"WARNING: no proposed variant {variant_id} found for pattern '{pattern}'. No change made.")
            return False
        if not path.is_file():
            print(f"WARNING: lesson_autogen_{pattern}.md not found. No change made.")
            return False

        try:
            original = path.read_text()
        except OSError as exc:
            print(f"WARNING: could not read {path.name}: {exc}. No change made.")
            return False
        parts = original.split("---", 2)
        if len(parts) < 3:
            print(f"WARNING: {path.name} missing frontmatter structure. No change made.")
            return False
        frontmatter, body = parts[1], parts[2].lstrip("\n")
        if not re.search(r"^\s*pattern:\s*\S+", frontmatter, re.M):
            print(f"WARNING: {path.name} missing pattern metadata. No change made.")
            return False
        body_parts = body.split("\n\n", 1)
        rest = body_parts[1] if len(body_parts) > 1 else ""
        new_body = selected_text + "\n\n" + rest

        if not re.search(r"^\s*first_seen:\s*\d{4}-\d{2}-\d{2}", frontmatter, re.M):
            old_date = re.search(r"last_updated:\s*(\d{4}-\d{2}-\d{2})", frontmatter)
            first_seen = old_date.group(1) if old_date else today
            frontmatter = re.sub(
                r"(pattern:\s*\S+\n)",
                rf"\1  first_seen: {first_seen}\n",
                frontmatter,
                count=1,
            )
        if re.search(r"last_updated:\s*\d{4}-\d{2}-\d{2}", frontmatter):
            frontmatter = re.sub(
                r"(last_updated:\s*)\d{4}-\d{2}-\d{2}",
                rf"\g<1>{today}",
                frontmatter,
            )
        else:
            frontmatter = frontmatter.rstrip() + f"\nlast_updated: {today}\n"
        updated = "---" + frontmatter + "---\n\n" + new_body

        selected = variants[match_index]
        selected_id = selected.get("proposal_id")
        if not isinstance(selected_id, str) or not selected_id:
            proposed_at = _date_value(selected.get("proposed_at")) or today
            selected_id = proposal_id(pattern, proposed_at, variant_id, selected_text)
            selected["proposal_id"] = selected_id
        backup_path = _backup_path(pattern, today, selected_id)
        try:
            atomic_write_text(backup_path, original)
            atomic_write_text(path, updated)
            selected["status"] = "applied"
            selected["applied_at"] = today
            selected["backup"] = str(backup_path)
            for index in proposed_indices:
                if index == match_index:
                    continue
                sibling = variants[index]
                sibling["status"] = "superseded"
                sibling["superseded_at"] = today
            rewrite_jsonl_unlocked(VARIANTS_FILE, variants)
        except (OSError, TimeoutError) as exc:
            try:
                atomic_write_text(path, original)
            except OSError as restore_exc:
                print(
                    f"CRITICAL: variant ledger commit failed and lesson restore failed: "
                    f"{restore_exc}"
                )
                return False
            print(f"WARNING: variant apply failed; lesson restored: {exc}")
            return False

    print(
        f"Applied variant {variant_id} to lesson_autogen_{pattern}.md "
        f"(backup: {backup_path}). last_updated bumped to {today}; "
        f"first_seen preserved so effectiveness window does not reset."
    )
    return True


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
