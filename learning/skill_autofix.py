#!/usr/bin/env python3
"""
Skill auto-fix (L2 gated auto-apply) — the machinery half of the self-improvement loop.

The rest of the loop changes BEHAVIOR (inject lessons + EnforcementGate). This closes the
loop onto the MACHINERY: when a specific skill/command concentrates failures, it edits the
skill file itself — gated, git-backed, measured, and auto-reverted if it doesn't help.

SAFETY BY CONSTRUCTION (every lever bounds the blast radius):
  - SCOPE: only ~/.claude/commands/*.md and ~/.pi/agent/skills/** that are REAL files
    (plugin/symlinked skills skipped — not ours to edit, clobbered on update).
    Never touches the loop's own hooks or ~/.pi/agent/extensions/**.
  - BOUNDED EDIT: never rewrites the skill. Upserts ONE marker-delimited
    "Auto-learned guardrails" section. The LLM writes only that section's bullets; application
    is deterministic Python. Revert = restore the prior snapshot (or strip the section).
  - GIT-BACKED: a dedicated snapshot repo (STATE/skillfix_repo) mirrors every edited file and
    commits before+after. Full diff/audit/history; revert pulls the "before" blob.
  - THRESHOLD GATE: a skill qualifies only with >= MIN_LOW low-rated sessions AND
    fail-rate >= MIN_RATE (consistent failure, not a one-off). One active edit per skill.
  - MEASURE-AFTER + AUTO-REVERT: after MIN_AFTER post-edit sessions, verdict_for() (same
    function the lesson loop uses) judges the skill's fail-rate. flat/regressed → auto-revert.
    working/improving/resolved → confirm. Reverted skills enter cooldown (skipped until a new
    dominant pattern appears) so the loop can't thrash.
  - GRACEFUL: LLM proposal needs ADC; on --no-llm or any LLM failure the DETERMINISTIC half
    (measure-after, auto-revert, candidate flagging) still runs. New edits retry next session.

Usage:
  python skill_autofix.py --apply        # evaluate active edits + propose/apply new ones (LLM)
  python skill_autofix.py --apply --no-llm  # deterministic only: revert regressions, flag candidates
  python skill_autofix.py --dry-run      # report what WOULD happen, touch nothing
  python skill_autofix.py --status       # print the ledger
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Shared primitives — same cross-import discipline the rest of the loop uses (zero drift).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_improve import (  # noqa: E402
    load_all_ratings,
    classify_entry,
    call_llm,
    RATINGS_FILE,
)
from measure_effectiveness import verdict_for  # noqa: E402

# ── Paths ────────────────────────────────────────────────────────────────────────
COMMANDS_DIR = Path.home() / ".claude/commands"
PI_SKILLS_DIR = Path.home() / ".pi/agent/skills"
STATE_DIR    = Path.home() / ".claude/MEMORY/STATE"
LEDGER_FILE  = STATE_DIR / "skill_autofix_ledger.json"
SNAP_REPO    = STATE_DIR / "skillfix_repo"
DIAG_DIR     = Path.home() / ".claude/MEMORY/LEARNING/DIAGNOSTICS"

# ── Gates ──────────────────────────────────────────────────────────────────────────
LOW       = 4     # rating <= LOW is a failure session (mirrors measure_effectiveness.LOW)
# Autonomy knobs (2026-07-08): slightly lower gates so skill_autofix can fire once
# skill attribution starts landing in ratings.jsonl. Still requires concentration
# (rate ≥ 40%) and post-edit measure+auto-revert — not one-off noise.
MIN_LOW   = 3     # was 5; a skill needs >= this many low-rated sessions to qualify
MIN_RATE  = 0.4   # was 0.5; AND >= this fraction of the skill's sessions must be low-rated
MIN_AFTER = 5     # post-edit sessions required before judging an applied edit
# Refuse to grow general-session past this many auto-learned bullets (dump-bin guard).
# Real skills (pr-workflow, bq, dataform, …) have no hard cap beyond the section itself.
GENERAL_SESSION_MAX_BULLETS = 5
GENERAL_SESSION_SKILL = "general-session"

# ── Bounded-edit markers ────────────────────────────────────────────────────────────
START = "<!-- AUTO-LEARNED-GUARDRAILS:start -->"
END   = "<!-- AUTO-LEARNED-GUARDRAILS:end -->"


# ── Git snapshot repo (dedicated — never entangles ~/.claude, which gitignores commands/) ──
def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(SNAP_REPO), *args],
        capture_output=True, text=True,
    ).stdout.strip()


def ensure_repo() -> None:
    SNAP_REPO.mkdir(parents=True, exist_ok=True)
    if not (SNAP_REPO / ".git").exists():
        _git("init", "-q")
        # This is a private, never-pushed revert/audit store. Neutralize INHERITED global
        # git hooks (the protected-branch blocker etc. that target real project repos) —
        # they would otherwise block our local snapshot commits. Scoped to this repo only,
        # and we commit on a non-protected branch as belt-and-suspenders.
        nohooks = SNAP_REPO / ".nohooks"
        nohooks.mkdir(exist_ok=True)
        _git("config", "core.hooksPath", str(nohooks))
        _git("config", "user.email", "loop@local")
        _git("config", "user.name", "skill-autofix")
        _git("checkout", "-q", "-b", "autofix")


def _snap_name(skill: str, surface: str) -> str:
    """Unique snapshot blob name so claude/pi skills never collide."""
    safe = skill.replace("/", "_")
    return f"{surface}__{safe}.md"


def snapshot(skill: str, live: Path, msg: str, surface: str = "claude") -> str:
    """Mirror the live skill file into the repo and commit. Returns the commit hash."""
    name = _snap_name(skill, surface)
    (SNAP_REPO / name).write_text(live.read_text())
    _git("add", "-f", name)
    _git("commit", "-q", "-m", msg, "--allow-empty")
    return _git("rev-parse", "HEAD")


def content_at(skill: str, commit: str, surface: str = "claude") -> str:
    """The skill file's exact content at a past commit — the revert source.
    Must NOT strip (unlike _git): trailing newline etc. are part of the file."""
    name = _snap_name(skill, surface)
    # Backward compat: older ledger commits used bare {skill}.md
    for candidate in (name, f"{skill}.md"):
        out = subprocess.run(
            ["git", "-C", str(SNAP_REPO), "show", f"{commit}:{candidate}"],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            return out.stdout
    return ""


# ── Bounded section upsert / strip (deterministic; LLM never writes the file) ─────────
def upsert_section(text: str, block: str) -> str:
    section = f"{START}\n{block}\n{END}"
    if START in text and END in text:
        return text[:text.index(START)] + section + text[text.index(END) + len(END):]
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "\n" + section + "\n"


def validate_skill_content(text: str) -> bool:
    """Validate skill markdown content for structural correctness (Point 3: Mutation Validator)"""
    # 1. Check for exactly one start and one end marker
    start_count = text.count(START)
    end_count = text.count(END)
    if start_count != 1 or end_count != 1:
        print(f"[validation-error] Invalid marker count. Start markers: {start_count}, End markers: {end_count}")
        return False
    
    # 2. Check that start comes before end
    start_idx = text.find(START)
    end_idx = text.find(END)
    if start_idx >= end_idx:
        print("[validation-error] Start marker must precede end marker.")
        return False
    
    # 3. Check for balanced backticks/code fences
    fence_count = text.count("```")
    if fence_count % 2 != 0:
        print(f"[validation-error] Unbalanced backticks/code fences (count: {fence_count})")
        return False
        
    return True


# ── Skill ↔ file resolution (Claude commands + pi skills) ────────────────────────────
# Surfaces (editable_surfaces.json):
#   claude: ~/.claude/commands/{skill}.md  (real files only, no plugins/symlinks)
#   pi:     ~/.pi/agent/skills/{skill}/SKILL.md  OR  ~/.pi/agent/skills/{skill}.md
def skill_file(skill: str) -> Path | None:
    """Resolve skill → live path. Prefer Claude command, then pi skill dir/file."""
    resolved = skill_file_with_surface(skill)
    return resolved[0] if resolved else None


def skill_file_with_surface(skill: str) -> tuple[Path, str] | None:
    if not skill:
        return None
    # Claude commands
    p = COMMANDS_DIR / f"{skill}.md"
    try:
        if p.exists() and not p.is_symlink() and p.resolve().parent == COMMANDS_DIR.resolve():
            return p, "claude"
    except OSError:
        pass
    # Pi skills: directory package
    pi_dir = PI_SKILLS_DIR / skill / "SKILL.md"
    try:
        if (
            pi_dir.exists()
            and not pi_dir.is_symlink()
            and PI_SKILLS_DIR.resolve() in pi_dir.resolve().parents
        ):
            return pi_dir, "pi"
    except OSError:
        pass
    # Pi skills: flat .md
    pi_flat = PI_SKILLS_DIR / f"{skill}.md"
    try:
        if (
            pi_flat.exists()
            and not pi_flat.is_symlink()
            and pi_flat.resolve().parent == PI_SKILLS_DIR.resolve()
        ):
            return pi_flat, "pi"
    except OSError:
        pass
    return None


# ── Stats ────────────────────────────────────────────────────────────────────────────
def skill_sessions(entries: list, skill: str, since: str | None = None) -> list:
    """Sessions attributed to skill via primary skill OR skill_candidates multi-label."""
    out = []
    for e in entries:
        if since is not None and e.timestamp <= since:
            continue
        primary = (e.skill or "").lower()
        cands = set()
        # RatingEntry may only have .skill; jsonl may have skill_candidates on raw dict
        raw_cands = getattr(e, "skill_candidates", None)
        if isinstance(raw_cands, list):
            cands = {str(c).lower() for c in raw_cands}
        if primary == skill or skill in cands:
            out.append(e)
    return out


def count_guardrail_bullets(text: str) -> int:
    """Count bullet lines inside the auto-learned guardrails section."""
    if START not in text or END not in text:
        return 0
    try:
        body = text.split(START, 1)[1].split(END, 1)[0]
    except IndexError:
        return 0
    return sum(1 for ln in body.splitlines() if ln.strip().startswith(("-", "*", "•")))


def fail_rate(sessions: list) -> tuple[float, int]:
    if not sessions:
        return 0.0, 0
    low = sum(1 for e in sessions if e.rating <= LOW)
    return low / len(sessions), low


def dominant_pattern(sessions: list) -> str:
    low = [e for e in sessions if e.rating <= LOW]
    c = Counter(p for e in low for p in classify_entry(e) if p != "other")
    return c.most_common(1)[0][0] if c else "general_quality"


# ── Ledger ────────────────────────────────────────────────────────────────────────────
def load_ledger() -> dict:
    if LEDGER_FILE.exists():
        try:
            return json.loads(LEDGER_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"edits": [], "log": []}


def save_ledger(ledger: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_FILE.write_text(json.dumps(ledger, indent=2))


def active_edit_for(ledger: dict, skill: str) -> dict | None:
    for ed in ledger["edits"]:
        if ed["skill"] == skill and ed["status"] == "active":
            return ed
    return None


def reverted_patterns(ledger: dict, skill: str) -> set[str]:
    """Patterns we already tried and reverted for this skill — cooldown to prevent thrash."""
    return {ed["pattern"] for ed in ledger["edits"]
            if ed["skill"] == skill and ed["status"] == "reverted"}


# ── LLM: generate the bounded guardrail block (only thing the model writes) ───────────
def _passing_behaviors(entries: list, skill: str) -> str:
    """Self-Harness (Zhang et al. 2026 / Weng 2026): preserve what already works."""
    good = [e for e in entries if e.skill == skill and e.rating >= 7]
    if not good:
        return "(no high-rated sessions attributed to this skill yet)"
    lines = []
    for e in sorted(good, key=lambda x: -x.rating)[:4]:
        s = (e.sentiment_summary or e.response_preview or "").strip()[:160]
        if s:
            lines.append(f"- [{e.rating}/10] {s}")
    return "\n".join(lines) or "(no summaries on high-rated sessions)"


def _prior_failed_edits(ledger: dict, skill: str) -> str:
    """Self-Harness: include previously attempted edits so proposer diversifies."""
    failed = [ed for ed in ledger.get("edits", [])
              if ed.get("skill") == skill and ed.get("status") == "reverted"]
    if not failed:
        return "(none)"
    return "\n".join(
        f"- pattern={ed.get('pattern')} verdict={ed.get('verdict')} "
        f"base={ed.get('baseline_fail_rate')} post={ed.get('post_fail_rate')}"
        for ed in failed[-5:]
    )


def generate_guardrail(skill: str, pattern: str, examples: list, today: str,
                       all_entries: list | None = None,
                       ledger: dict | None = None) -> str | None:
    sample = "\n".join(f"- [{e.rating}/10] {e.sentiment_summary}"
                       for e in sorted(examples, key=lambda x: x.rating)[:6]
                       if e.sentiment_summary)
    preserve = _passing_behaviors(all_entries or [], skill)
    prior = _prior_failed_edits(ledger or {"edits": []}, skill)
    # Bounded proposal context per Self-Harness / Lil'Log:
    # (1) editable surface  (2) failure patterns  (3) passing behaviors  (4) prior edits
    prompt = (
        f"You are improving a coding-agent skill invoked as /{skill} "
        f"(Claude commands and/or pi agent skills share this loop). "
        f"EDITABLE SURFACE: only the 'Auto-learned guardrails' marker section "
        f"(EVOLVE-BLOCK equivalent). Do not rewrite the rest of the skill.\n\n"
        f"Failure pattern to address: '{pattern}'.\n\n"
        f"Low-rated session summaries (weakness mining):\n{sample or '(no summaries)'}\n\n"
        f"Passing behaviors to PRESERVE (do not break these):\n{preserve}\n\n"
        f"Previously attempted edits that were REVERTED (do not repeat):\n{prior}\n\n"
        "Write 1-3 imperative guardrail bullets to PREVENT this specific failure. "
        "Be concrete and actionable. Prefer narrow changes for recurrent addressable "
        "errors — not task-specific one-offs. Output ONLY the markdown bullets — "
        "no preamble, no heading, no code fences."
    )
    out = call_llm(prompt, max_tokens=300)
    if not out or not out.strip():
        return None
    bullets = "\n".join(l for l in out.strip().splitlines()
                        if l.strip().startswith(("-", "*"))) or out.strip()
    return (f"## Auto-learned guardrails (self-improvement loop)\n"
            f"<!-- pattern:{pattern} updated:{today} — auto-generated; edit freely, "
            f"the loop only rewrites between these markers -->\n{bullets}")


# ── Evaluate active edits: measure-after → confirm or auto-revert ─────────────────────
def evaluate_active(ledger: dict, entries: list, today: str,
                    changes: list[str], dry_run: bool) -> None:
    for ed in ledger["edits"]:
        if ed["status"] != "active":
            continue
        post = skill_sessions(entries, ed["skill"], since=ed["applied"])
        rate, _ = fail_rate(post)
        ed["post_n"] = len(post)
        if len(post) < MIN_AFTER:
            continue
        v = verdict_for(ed["baseline_fail_rate"], rate, len(post), MIN_AFTER)
        ed["post_fail_rate"] = round(rate, 3)
        ed["verdict"] = v
        surface = ed.get("surface") or "claude"
        if v in ("flat", "regressed"):
            resolved = skill_file_with_surface(ed["skill"])
            live = resolved[0] if resolved else None
            surface = (resolved[1] if resolved else surface)
            if live and not dry_run:
                live.write_text(content_at(ed["skill"], ed["commit_before"], surface))
                snapshot(ed["skill"], live,
                         f"revert /{ed['skill']} — {v} post={rate:.2f} base={ed['baseline_fail_rate']:.2f}",
                         surface=surface)
            ed["status"] = "reverted"
            ed["reverted"] = today
            changes.append(f"REVERTED /{ed['skill']} ({surface}) — {v} (post {rate:.2f} ≥ base {ed['baseline_fail_rate']:.2f})")
        elif v in ("working", "improving", "resolved"):
            ed["status"] = "confirmed"
            ed["confirmed"] = today
            changes.append(f"confirmed /{ed['skill']} ({surface}) — {v} (post {rate:.2f} < base {ed['baseline_fail_rate']:.2f})")


# ── Propose + apply new edits for qualifying skills ───────────────────────────────────
def propose_new(ledger: dict, entries: list, today: str, changes: list[str],
                use_llm: bool, dry_run: bool) -> list[str]:
    candidates: list[str] = []
    # Skills that appear in attributed ratings, ranked by low-rated volume.
    # general-session is the dump-bin fallback — never grow it past MAX bullets.
    skills = Counter(e.skill for e in entries if e.skill and e.rating <= LOW)
    for skill, _ in skills.most_common():
        if active_edit_for(ledger, skill):
            continue                                   # one active edit per skill
        resolved = skill_file_with_surface(skill)
        if not resolved:
            continue                                   # plugin/symlinked or missing → skip
        live, surface = resolved
        # Gate: refuse to grow general-session past N auto-learned bullets.
        if skill == GENERAL_SESSION_SKILL:
            try:
                n_bullets = count_guardrail_bullets(live.read_text(errors="replace"))
            except OSError:
                n_bullets = 0
            if n_bullets >= GENERAL_SESSION_MAX_BULLETS:
                changes.append(
                    f"skip /{skill} — dump-bin cap reached "
                    f"({n_bullets}>={GENERAL_SESSION_MAX_BULLETS} bullets); "
                    f"fix skill attribution instead"
                )
                continue
        sessions = skill_sessions(entries, skill)
        rate, low_n = fail_rate(sessions)
        if low_n < MIN_LOW or rate < MIN_RATE:
            continue                                   # threshold gate
        pattern = dominant_pattern(sessions)
        if pattern in reverted_patterns(ledger, skill):
            changes.append(f"skip /{skill} — pattern '{pattern}' already tried+reverted (cooldown)")
            continue
        candidates.append(f"/{skill} [{surface}] (low={low_n}, rate={rate:.2f}, pattern={pattern})")
        if not use_llm or dry_run:
            continue                                   # deterministic run: flag only
        block = generate_guardrail(
            skill, pattern,
            [e for e in sessions if e.rating <= LOW], today,
            all_entries=entries, ledger=ledger,
        )
        if not block:
            changes.append(f"deferred /{skill} — LLM unavailable; retry next session")
            continue
        commit_before = snapshot(skill, live, f"before autofix /{skill} ({pattern})", surface=surface)
        new_content = upsert_section(live.read_text(), block)
        if validate_skill_content(new_content):
            live.write_text(new_content)
            commit_after = snapshot(skill, live, f"autofix /{skill} ({pattern})", surface=surface)
            ledger["edits"].append({
                "skill": skill, "pattern": pattern, "surface": surface,
                "baseline_fail_rate": round(rate, 3), "baseline_n": len(sessions),
                "commit_before": commit_before, "commit_after": commit_after,
                "applied": today, "status": "active", "post_n": 0,
            })
            changes.append(f"APPLIED /{skill} [{surface}] — guardrail for '{pattern}' (baseline {rate:.2f}, n={low_n})")
        else:
            changes.append(f"deferred /{skill} — generated content failed format validation")
    return candidates


# ── Report ────────────────────────────────────────────────────────────────────────────
def build_report(ledger: dict, changes: list[str], candidates: list[str], today: str) -> str:
    active    = [e for e in ledger["edits"] if e["status"] == "active"]
    confirmed = [e for e in ledger["edits"] if e["status"] == "confirmed"]
    reverted  = [e for e in ledger["edits"] if e["status"] == "reverted"]
    lines = [
        f"# Skill Auto-Fix — {today}", "",
        f"Active edits: {len(active)} | Confirmed: {len(confirmed)} | Reverted: {len(reverted)}", "",
        "Gated auto-apply onto ~/.claude/commands/*.md and ~/.pi/agent/skills/**: "
        "threshold-gate failing skills, append a bounded guardrail section, measure the skill's "
        "fail-rate after, auto-revert if it doesn't help.", "",
    ]
    if changes:
        lines += ["## Changes this run", ""] + [f"- {c}" for c in changes] + [""]
    if candidates:
        lines += ["## Qualifying skills (gate passed)", ""] + [f"- {c}" for c in candidates] + [""]
    if active:
        lines += ["## Active edits (awaiting measure-after)", ""]
        for e in active:
            lines.append(f"- /{e['skill']} — '{e['pattern']}', baseline {e['baseline_fail_rate']:.2f}, "
                         f"post_n {e.get('post_n', 0)}/{MIN_AFTER}")
        lines.append("")
    if not changes and not candidates:
        lines += ["No qualifying skills — accumulating attributed ratings.", ""]
    return "\n".join(lines) + "\n"


def suite_gate_allows_apply() -> tuple[bool, str]:
    """Hard gate: never apply NEW skill mutations when held-out gates are red.

    Lil'Log / Self-Harness: accept harness edits only if D_in and D_out do not regress.
    Reverts (evaluate_active) still run — they *reduce* risk. New proposes are blocked.

    Checks:
      1. held_out_suite.py --gate  (deterministic fixtures; always run)
      2. agent_rollouts last result (if present): block when pass_rate < 0.75
         or baseline gate_pass is false. Missing/stale file does not block (boot).
    """
    suite = Path(__file__).resolve().parent / "held_out_suite.py"
    if not suite.exists():
        return True, "held_out_suite.py missing — fail-open for evaluate-only paths"
    proc = subprocess.run(
        [sys.executable, str(suite), "--gate"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
        return False, f"held_out_suite gate FAIL (rc={proc.returncode}): {tail}"

    # Agent rollouts: use last run (produced by session-end / self_harness).
    # Do not re-invoke LLM here — session-end owns that cost.
    last = STATE_DIR / "agent_rollouts_last.json"
    if last.exists():
        try:
            data = json.loads(last.read_text())
            summary = data.get("summary") or {}
            gate = data.get("gate") or {}
            rate = float(summary.get("pass_rate") or 0.0)
            skipped = bool(summary.get("skipped_all"))
            if not skipped:
                if rate < 0.75:
                    return False, (
                        f"agent_rollouts gate FAIL pass_rate={rate:.1%} < 75% "
                        f"(see {last})"
                    )
                if gate.get("has_baseline") and gate.get("gate_pass") is False:
                    return False, f"agent_rollouts baseline regression (see {last})"
                return True, (
                    f"held_out_suite PASS; agent_rollouts PASS "
                    f"(pass_rate={rate:.1%} n={summary.get('n')})"
                )
            return True, "held_out_suite PASS; agent_rollouts skipped (no LLM)"
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
            # Corrupt last file — do not block applies
            return True, f"held_out_suite PASS; agent_rollouts last unreadable ({e})"

    return True, "held_out_suite PASS; agent_rollouts no prior run (not blocking)"


# ── Main ──────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Skill auto-fix (L2 gated auto-apply)")
    ap.add_argument("--apply", action="store_true", help="evaluate + apply (writes files/ledger)")
    ap.add_argument("--dry-run", action="store_true", help="report only, touch nothing")
    ap.add_argument("--no-llm", action="store_true", help="deterministic only: revert + flag, no new edits")
    ap.add_argument("--status", action="store_true", help="print the ledger and exit")
    ap.add_argument("--force", action="store_true",
                    help="bypass held_out_suite gate for NEW applies (danger; reverts still run)")
    args = ap.parse_args()

    ledger = load_ledger()
    if args.status:
        print(json.dumps(ledger, indent=2))
        return 0

    entries = load_all_ratings(RATINGS_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    dry = args.dry_run or not args.apply
    use_llm = not args.no_llm

    if not dry:
        ensure_repo()

    changes: list[str] = []
    # Always evaluate/revert first — reverts are safe even when suite is red.
    evaluate_active(ledger, entries, today, changes, dry)

    # Hard-block NEW applies when fixture suite gate fails (unless --force).
    allow_new, gate_msg = (True, "dry-run/no-apply") if dry else (
        (True, "forced") if args.force else suite_gate_allows_apply()
    )
    if not allow_new:
        changes.append(f"BLOCKED new skill applies — {gate_msg}")
        print(f"[skill_autofix] {gate_msg}")
        print("[skill_autofix] NEW applies blocked; reverts/measure-after still applied above.")
        candidates: list[str] = []
        # Still surface who would have qualified (deterministic half)
        candidates = propose_new(ledger, entries, today, changes, use_llm=False, dry_run=True)
    else:
        if not dry:
            print(f"[skill_autofix] {gate_msg}")
        candidates = propose_new(ledger, entries, today, changes, use_llm, dry)

    report = build_report(ledger, changes, candidates, today)
    print(report)

    if dry:
        print("[dry-run] no files or ledger written")
        return 0

    if changes:
        ledger.setdefault("log", []).append({"date": today, "changes": changes})
    save_ledger(ledger)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    (DIAG_DIR / f"skill_autofix_{today}.md").write_text(report)
    print(f"Wrote: {LEDGER_FILE}")
    print(f"Wrote: {DIAG_DIR / f'skill_autofix_{today}.md'}")
    # Exit 2 = suite blocked applies (caller can distinguish from hard crash)
    return 0 if allow_new else 2


if __name__ == "__main__":
    raise SystemExit(main())
