#!/usr/bin/env python3
"""
ace_playbook.py — Agentic Context Engineering (ACE) curator for the harness.

Lil'Log (Weng 2026, "Harness Engineering for Self-Improvement") cites ACE
(Zhang et al. 2025): context as an evolving *playbook* of itemized bullets,
not a lengthening prompt blob. Key design:

  Generator  → task trajectories (ratings + failures; already captured)
  Reflector  → ace_reflector.py: weak-rule repair, evidence distill, quality score
  Curator    → THIS FILE: structured bullets merged with deterministic logic;
               dedupe, rank, emit STATE/ace_playbook.*

Bullets carry helpful/harmful counters (effectiveness verdicts) so injection
can boost still-failing patterns and suppress resolved ones — without
rewriting a monolithic prompt.

Quality gate (2026-07-10): never inject stub rules
  ("Avoid X — verify before acting"). Reflector upgrades or demotes them.

Usage:
  python3 ace_playbook.py            # rebuild playbook from lessons + scores
  python3 ace_playbook.py --dry-run  # report only
  python3 ace_playbook.py --max 40   # cap bullet count
  python3 ace_playbook.py --llm      # allow Reflector LLM for residual weak rules
  python3 ace_playbook.py --min-quality 2
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from datetime import datetime, timezone
from typing import Any, TypedDict

from harness_paths import HARNESS_HOME, LESSONS_DIR
from state_io import atomic_write_json, atomic_write_text, exclusive_locks, try_read_json_object

SCORES_FILE = HARNESS_HOME / "MEMORY/STATE/effectiveness_scores.json"
OUT_JSON = HARNESS_HOME / "MEMORY/STATE/ace_playbook.json"
OUT_MD = HARNESS_HOME / "MEMORY/STATE/ace_playbook.md"
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
NEG_FILE = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/negative_results.jsonl"
LEARNING = HARNESS_HOME / "MEMORY/LEARNING"

sys.path.insert(0, str(LEARNING))
from ace_reflector import (  # noqa: E402
    is_weak_rule,
    quality_score,
    reflect_from_lesson_file,
    reflect_lesson,
)

# ACE-style: curator never full-rewrites prose into one blob.
# Verdict → (helpful_delta, harmful_delta) for counters.
VERDICT_COUNTERS = {
    "resolved":  (3, 0),
    "improving": (2, 0),
    "working":   (1, 0),
    "pending":   (0, 0),
    "stale-pending": (0, 0),
    "flat":      (0, 1),
    "regressed": (0, 3),
    "no-baseline": (0, 0),
}

# Only real hill-climb outcomes get active injection. Pending sea must not drown playbook.
INJECTABLE_VERDICTS = frozenset({
    "regressed", "flat", "improving", "working", "resolved",
})

# Template sludge — never inject even if quality_score is borderline.
SLUDGE_RES = [
    re.compile(r"^avoid .+ [\u2014-] verify before acting\.?$", re.I),
    re.compile(r"^when .+ risk appears:\s*stop,?\s*gather tool evidence", re.I),
    re.compile(r"^before every response where .+ could occur", re.I),
    re.compile(r"^check this rule\.?$", re.I),
]

class SeedBullet(TypedDict):
    pattern: str
    description: str
    priority: int
    section: str


# Fixed anti-hallucination seed bullets — always kept at top of strategy section.
# Not derived from lessons; closed-loop guards that must survive ACE rebuilds.
SEED_BULLETS: list[SeedBullet] = [
    {
        "pattern": "unverified_completion",
        "description": (
            "Never claim done/fixed/complete without STRONG paper trace: fenced CLI/test "
            "output, exit codes, pass counts next to a test runner, or live URL. Bare paths "
            "and bare 'N rows/tests' are NOT evidence. If you cannot fence proof, say what "
            "is still unverified — do not say done."
        ),
        "priority": 70,
        "section": "strategy",
    },
    {
        "pattern": "incomplete_analysis",
        "description": (
            "Before concluding or agreeing: read ALL relevant context (full diff, existing "
            "PR comments, ticket, related files, CLAUDE.md). Never say looks-unrelated / "
            "you're-right / same-issue without a research trace (I read X / gh pr diff / "
            "fenced tool output). Research first, respond second."
        ),
        "priority": 68,
        "section": "strategy",
    },
    {
        "pattern": "unverified_claims",
        "description": (
            "Never assert system state (schema/CI/PR/partition/row counts) without tool "
            "output. Tag [GUESS]/unverified when unverified. Never invent metrics, PR numbers, "
            "or line refs."
        ),
        "priority": 58,
        "section": "strategy",
    },
    {
        "pattern": "duplicate_approval",
        "description": (
            "If reviewDecision is already APPROVED, skip — do not approve again. Saying "
            "'already APPROVED, skipping second approval' is correct. Never claim you "
            "approved again / just in case / left a second approval."
        ),
        "priority": 56,
        "section": "pitfall",
    },
    {
        "pattern": "silent_completion",
        "description": (
            "After any tool use, emit at least one user-visible line: what changed and how "
            "verified. Silent tool turns hide failures and feed later hallucinations."
        ),
        "priority": 55,
        "section": "strategy",
    },
]


def bullet_id(pattern: str, rule: str) -> str:
    h = hashlib.sha256(f"{pattern}|{rule[:120]}".encode()).hexdigest()[:10]
    return f"b_{pattern[:40]}_{h}"


def safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0
    try:
        number = int(value)
    except (OverflowError, ValueError):
        return 0
    return max(0, number)


def safe_finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def load_scores() -> dict[str, dict[str, Any]]:
    data, _error = try_read_json_object(SCORES_FILE)
    raw_scores = data.get("scores")
    if not isinstance(raw_scores, dict):
        return {}
    return {
        pattern: row
        for pattern, row in raw_scores.items()
        if isinstance(pattern, str) and pattern and isinstance(row, dict)
    }


def load_lessons(use_llm: bool = False) -> list[dict[str, Any]]:
    """Load lessons and run Reflector on each (weak → upgraded description)."""
    out: list[dict[str, Any]] = []
    if not LESSONS_DIR.exists():
        return out
    for p in sorted(LESSONS_DIR.glob("lesson_autogen_*.md")):
        pattern = p.name.removeprefix("lesson_autogen_").removesuffix(".md")
        try:
            text = p.read_text(errors="replace")
        except OSError as exc:
            print(f"[ace_playbook] unreadable lesson {p.name}: {exc}")
            continue
        occ = 0
        avg = 0.0
        m = re.search(r"occurrence_count:\s*(\d+)", text)
        if m:
            occ = safe_nonnegative_int(m.group(1))
        m = re.search(r"avg_rating:\s*([\d.]+)", text)
        if m:
            avg = safe_finite_float(m.group(1))

        ref = reflect_from_lesson_file(text, pattern, use_llm=use_llm)
        # Absolute last line: never keep a stub description
        if is_weak_rule(ref.description):
            ref = reflect_lesson(pattern, rule="", evidence=ref.evidence_used, use_llm=use_llm)

        out.append({
            "pattern": pattern,
            "rule": ref.description,
            "raw_rule": "",  # filled below for diagnostics
            "path": str(p),
            "occurrence_count": occ,
            "avg_rating": avg,
            "quality": ref.quality,
            "reflect_source": ref.source,
            "section": ref.section if ref.section in ("strategy", "pitfall", "formula") else "strategy",
            "weak_input": ref.weak_input,
        })
        # stash raw first-line for stats
        parts = text.split("---", 2)
        body = (parts[2] if len(parts) >= 3 else text).lstrip("\n")
        for line in body.splitlines():
            t = line.strip()
            if t and not t.startswith("**") and not t.startswith("#"):
                out[-1]["raw_rule"] = t[:200]
                break
    return out


def tokenize(s: str) -> set[str]:
    stop = {"this", "that", "with", "from", "have", "will", "your", "what",
            "when", "where", "which", "about", "would", "could", "should",
            "the", "and", "for", "are", "was", "been", "into", "more", "before",
            "after", "never", "always", "instead"}
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in stop}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_sludge(description: str) -> bool:
    """True for template boilerplate that must never enter active injection."""
    d = (description or "").strip()
    if not d or len(d) < 24:
        return True
    return any(r.search(d) for r in SLUDGE_RES)


def dedupe_bullets(bullets: list[dict], threshold: float = 0.65) -> list[dict]:
    """Lil'Log / ACE: periodic dedupe. Prefer higher quality then signal.

    Threshold tightened 0.72 → 0.65 (2026-07-16): near-dupe lesson sea
    (stateless_*, tone_*, rtfm_*) was surviving as separate bullets.
    """
    kept: list[dict] = []
    for b in sorted(
        bullets,
        key=lambda x: -(
            x.get("quality", 0) * 10
            + x["helpful"]
            + x["harmful"]
            + x.get("priority", 0)
            + (50 if x.get("seed") else 0)
            + (20 if x.get("verdict") in ("regressed", "flat") else 0)
        ),
    ):
        toks = tokenize(b["description"])
        dup = False
        for k in kept:
            if b["pattern"] == k["pattern"] and not b.get("seed"):
                # same pattern: keep higher quality / priority only
                dup = True
                if b.get("quality", 0) > k.get("quality", 0) and not k.get("seed"):
                    k.update({kk: b[kk] for kk in b if kk != "aliases"})
                k.setdefault("aliases", []).append(b["pattern"])
                break
            # Near-dupe names (prefix family) with medium text overlap → merge
            name_near = (
                b["pattern"].split("_")[0] == k["pattern"].split("_")[0]
                and b["pattern"] != k["pattern"]
                and len(b["pattern"].split("_")[0]) >= 4
            )
            j = jaccard(toks, tokenize(k["description"]))
            thr = threshold - 0.10 if name_near else threshold
            if j >= thr:
                k["helpful"] += b["helpful"]
                k["harmful"] += b["harmful"]
                k.setdefault("aliases", []).append(b["pattern"])
                # keep better description if survivor is weaker
                if b.get("quality", 0) > k.get("quality", 0) and not k.get("seed"):
                    k["description"] = b["description"]
                    k["quality"] = b["quality"]
                    k["id"] = bullet_id(k["pattern"], k["description"])
                dup = True
                break
        if not dup:
            kept.append(b)
    return kept


def _injection_section(verdict: str, ace_section: str, quality: int, min_quality: int,
                       description: str = "") -> str:
    """Map verdict + quality → playbook section for injection.

    strategy/pitfall/formula = active injection candidates
    resolved = suppressed unless task-matched
    deferred = failed quality gate OR pending/stale-pending (no real verdict yet)
    """
    if is_sludge(description):
        return "deferred"
    if quality < min_quality:
        return "deferred"
    # Only real verdicts inject. Pending sea was drowning the playbook.
    if verdict not in INJECTABLE_VERDICTS:
        return "deferred"
    if verdict in ("resolved", "working", "improving"):
        return "resolved"
    # active: keep ACE taxonomy for MD rendering (regressed/flat)
    if ace_section in ("strategy", "pitfall", "formula"):
        return ace_section
    return "strategy"


def build_playbook(
    max_bullets: int, min_quality: int = 2, use_llm: bool = False
) -> dict[str, Any]:
    scores = load_scores()
    lessons = load_lessons(use_llm=use_llm)
    bullets: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "lessons_in": len(lessons),
        "weak_input": 0,
        "reflected": 0,
        "deferred_quality": 0,
        "deferred_pending": 0,
        "deferred_sludge": 0,
        "by_source": {},
    }

    for seed in SEED_BULLETS:
        pat = seed["pattern"]
        sc = scores.get(pat, {})
        # Prefer objective/judge when subjective is still pending
        verdict = sc.get("verdict", "pending")
        if verdict not in INJECTABLE_VERDICTS:
            for alt in (sc.get("obj_verdict"), sc.get("judge_verdict")):
                if alt in INJECTABLE_VERDICTS:
                    verdict = alt
                    break
        help_d, harm_d = VERDICT_COUNTERS.get(verdict, (0, 0))
        desc = seed["description"]
        # Seeds always stay active strategy/pitfall — closed-loop guards.
        bullets.append({
            "id": bullet_id(pat, desc),
            "pattern": pat,
            "description": desc,
            "verdict": verdict if verdict in INJECTABLE_VERDICTS else "pending",
            "helpful": help_d + 5,
            "harmful": max(harm_d, 1),
            "priority": seed["priority"],
            "avg_rating": 0,
            "delta": sc.get("delta"),
            "section": seed.get("section", "strategy"),
            "ace_section": seed.get("section", "strategy"),
            "quality": quality_score(desc),
            "reflect_source": "seed",
            "seed": True,
        })

    seen_patterns = {b["pattern"] for b in bullets}

    for les in lessons:
        pat = les["pattern"]
        sc = scores.get(pat, {})
        # Injectable flag from measure_effectiveness when present; else derive.
        if sc.get("injectable") is True:
            verdict = sc.get("verdict", "pending")
            if verdict not in INJECTABLE_VERDICTS:
                for alt in (sc.get("obj_verdict"), sc.get("judge_verdict")):
                    if alt in INJECTABLE_VERDICTS:
                        verdict = alt
                        break
        else:
            verdict = sc.get("verdict", "pending")
            if sc.get("injectable") is False and verdict not in INJECTABLE_VERDICTS:
                # Explicit non-injectable (pending/stale) — still load for deferred stats
                pass
            elif verdict not in INJECTABLE_VERDICTS:
                for alt in (sc.get("obj_verdict"), sc.get("judge_verdict")):
                    if alt in INJECTABLE_VERDICTS:
                        verdict = alt
                        break

        help_d, harm_d = VERDICT_COUNTERS.get(verdict, (0, 0))
        q = min(4, safe_nonnegative_int(les.get("quality", 0)))
        src = les.get("reflect_source", "passthrough")
        stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
        if les.get("weak_input"):
            stats["weak_input"] += 1
        if src != "passthrough":
            stats["reflected"] += 1

        # Seeds already cover pattern — only merge counters if lesson is better (skip)
        if pat in seen_patterns:
            continue

        prio = {
            "regressed": 50, "flat": 30, "pending": 0, "stale-pending": 0,
            "working": 5, "improving": 3, "resolved": -100, "no-baseline": 0,
        }.get(verdict, 0)
        # quality boosts injection rank
        prio += min(q, 4) * 2

        # Do NOT inflate helpful from occurrence_count on low-quality lessons —
        # that was ranking stubs above real strategy.
        helpful = help_d
        if q >= 3:
            helpful += safe_nonnegative_int(les.get("occurrence_count", 0)) // 3
        elif q >= 2:
            helpful += safe_nonnegative_int(les.get("occurrence_count", 0)) // 6

        ace_sec = les.get("section", "strategy")
        rule = les.get("rule") or ""
        inj_sec = _injection_section(verdict, ace_sec, q, min_quality, description=rule)
        if inj_sec == "deferred":
            stats["deferred_quality"] += 1
            if is_sludge(rule):
                stats["deferred_sludge"] += 1
            elif verdict not in INJECTABLE_VERDICTS:
                stats["deferred_pending"] += 1

        bullets.append({
            "id": bullet_id(pat, rule),
            "pattern": pat,
            "description": rule,
            "verdict": verdict,
            "helpful": helpful,
            "harmful": harm_d,
            "priority": prio,
            "avg_rating": les.get("avg_rating", 0),
            "delta": sc.get("delta"),
            "section": inj_sec,
            "ace_section": ace_sec,
            "quality": q,
            "reflect_source": src,
            "raw_was_weak": bool(les.get("weak_input")),
        })
        seen_patterns.add(pat)

    bullets = dedupe_bullets(bullets)

    # rank: priority then quality then harmful then helpful
    bullets.sort(
        key=lambda b: (
            -b["priority"],
            -b.get("quality", 0),
            -b["harmful"],
            -b["helpful"],
            b["pattern"],
        )
    )

    if max_bullets > 0:
        seeds = [b for b in bullets if b.get("seed")]
        must = [
            b for b in bullets
            if b["verdict"] in ("regressed", "flat") and not b.get("seed")
            and b["section"] != "deferred"
        ]
        active = [
            b for b in bullets
            if not b.get("seed")
            and b not in must
            and b["section"] in ("strategy", "pitfall", "formula")
        ]
        rest = [
            b for b in bullets
            if b not in seeds and b not in must and b not in active
        ]
        bullets = (seeds + must + active + rest)[:max_bullets]

    sections = {
        "strategy": [b["id"] for b in bullets if b["section"] == "strategy"],
        "pitfall": [b["id"] for b in bullets if b["section"] == "pitfall"],
        "formula": [b["id"] for b in bullets if b["section"] == "formula"],
        "resolved": [b["id"] for b in bullets if b["section"] == "resolved"],
        "deferred": [b["id"] for b in bullets if b["section"] == "deferred"],
    }

    weak_out = sum(1 for b in bullets if is_weak_rule(b["description"]))
    stats["weak_output"] = weak_out
    stats["avg_quality"] = round(
        sum(b.get("quality", 0) for b in bullets) / max(1, len(bullets)), 2
    )

    return {
        "version": 2,
        "framework": "ACE (Zhang et al. 2025) via Weng 2026 harness post",
        "reflector": "ace_reflector.py",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bullet_count": len(bullets),
        "min_quality": min_quality,
        "stats": stats,
        "bullets": bullets,
        "sections": sections,
    }


def render_md(playbook: dict) -> str:
    stats = playbook.get("stats") or {}
    lines = [
        "<!-- auto-generated by ace_playbook.py — ACE curator; do not hand-edit -->",
        f"# ACE Playbook ({playbook['bullet_count']} bullets)",
        f"Generated: {playbook['generated_at']}",
        f"Reflector stats: weak_in={stats.get('weak_input', '?')} "
        f"reflected={stats.get('reflected', '?')} "
        f"avg_quality={stats.get('avg_quality', '?')} "
        f"weak_out={stats.get('weak_output', '?')}",
        "",
    ]

    def emit(title: str, section: str, limit_desc: int = 220) -> None:
        rows = [b for b in playbook["bullets"] if b["section"] == section]
        if not rows:
            return
        lines.append(f"## {title}")
        lines.append("")
        for b in rows:
            lines.append(
                f"- **[{b['id']}]** `{b['pattern']}` [{b['verdict']}] "
                f"q{b.get('quality', '?')} {b.get('reflect_source', '')} "
                f"h+{b['helpful']}/h-{b['harmful']}: {b['description'][:limit_desc]}"
            )
        lines.append("")

    emit("Strategies (active)", "strategy")
    emit("Pitfalls (active)", "pitfall")
    emit("Formulas (active)", "formula")
    emit("Resolved (suppressed unless task-matched)", "resolved", 120)
    emit("Deferred (failed quality gate)", "deferred", 120)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ACE playbook curator")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=40, help="max bullets to keep")
    ap.add_argument("--min-quality", type=int, default=2,
                    help="min quality_score for active injection (0-4)")
    ap.add_argument("--llm", action="store_true",
                    help="allow Reflector LLM for residual weak rules")
    args = ap.parse_args(argv)
    if args.max <= 0:
        print("[ace_playbook] --max must be positive")
        return 2
    if not 0 <= args.min_quality <= 4:
        print("[ace_playbook] --min-quality must be between 0 and 4")
        return 2

    pb = build_playbook(args.max, min_quality=args.min_quality, use_llm=args.llm)
    md = render_md(pb)
    sec = pb["sections"]
    print(
        f"[ace_playbook] {pb['bullet_count']} bullets "
        f"(strategy={len(sec['strategy'])} pitfall={len(sec['pitfall'])} "
        f"formula={len(sec['formula'])} resolved={len(sec['resolved'])} "
        f"deferred={len(sec['deferred'])})"
    )
    st = pb.get("stats") or {}
    print(
        f"[ace_playbook] reflector: weak_in={st.get('weak_input')} "
        f"reflected={st.get('reflected')} avg_q={st.get('avg_quality')} "
        f"weak_out={st.get('weak_output')} sources={st.get('by_source')}"
    )
    for b in pb["bullets"][:10]:
        print(
            f"  q{b.get('quality', '?')} {b['verdict']:<10} "
            f"{b['pattern']:<36} {b['description'][:55]}"
        )

    if st.get("weak_output", 0) > 0:
        print(f"[ace_playbook] WARNING: {st['weak_output']} weak stubs still in output")

    if args.dry_run:
        return 0

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    diagnostic = DIAG / f"ace_playbook_{day}.md"
    with exclusive_locks([OUT_JSON, OUT_MD, diagnostic]):
        atomic_write_json(OUT_JSON, pb)
        atomic_write_text(OUT_MD, md)
        atomic_write_text(diagnostic, md)
    print(f"[ace_playbook] Wrote {OUT_JSON}")
    print(f"[ace_playbook] Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
