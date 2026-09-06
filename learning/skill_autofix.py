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
import math
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from harness_paths import COMMANDS, DIAGNOSTICS, PI_SKILLS, STATE
from state_io import atomic_write_json, atomic_write_text, exclusive_lock, try_read_json_object

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
COMMANDS_DIR = COMMANDS
PI_SKILLS_DIR = PI_SKILLS
STATE_DIR = STATE
LEDGER_FILE  = STATE_DIR / "skill_autofix_ledger.json"
SNAP_REPO    = STATE_DIR / "skillfix_repo"
DIAG_DIR = DIAGNOSTICS

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
MAX_VALIDATION_ARGS = 64
ALLOWED_VALIDATION_COMMANDS = frozenset(
    {"bun", "cargo", "go", "mypy", "npm", "pnpm", "pytest", "python", "python3", "ruff", "shellcheck", "yarn"}
)
SHELL_CONTROL_TOKENS = frozenset({"&&", "||", ";", "|", "<", ">", ">>", "2>", "2>>", "&"})
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")

# ── Bounded-edit markers ────────────────────────────────────────────────────────────
START = "<!-- AUTO-LEARNED-GUARDRAILS:start -->"
END   = "<!-- AUTO-LEARNED-GUARDRAILS:end -->"


# ── Git snapshot repo (dedicated — never entangles ~/.claude, which gitignores commands/) ──
def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(SNAP_REPO), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip()[-500:]
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit {result.returncode}: {detail}"
        )
    return result.stdout.strip()


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


def _valid_skill_name(skill: object) -> bool:
    return isinstance(skill, str) and bool(SKILL_NAME_RE.fullmatch(skill))


def _snap_name(skill: str, surface: str) -> str:
    """Unique snapshot blob name so claude/pi skills never collide."""
    if not _valid_skill_name(skill):
        raise ValueError(f"invalid skill name: {skill!r}")
    safe_surface = surface if surface in {"claude", "pi"} else "claude"
    return f"{safe_surface}__{skill}.md"


def snapshot(skill: str, live: Path, msg: str, surface: str = "claude") -> str:
    """Mirror the live skill file into the repo and commit. Returns the commit hash."""
    name = _snap_name(skill, surface)
    atomic_write_text(SNAP_REPO / name, live.read_text())
    _git("add", "-f", name)
    _git("commit", "-q", "-m", msg, "--allow-empty")
    return _git("rev-parse", "HEAD")


def content_at(skill: str, commit: str, surface: str = "claude") -> str | None:
    """Return the exact prior snapshot, or ``None`` when it cannot be trusted."""
    if not _valid_skill_name(skill) or not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        return None
    name = _snap_name(skill, surface)
    # Backward compat: older ledger commits used bare {skill}.md.
    for candidate in (name, f"{skill}.md"):
        out = subprocess.run(
            ["git", "-C", str(SNAP_REPO), "show", f"{commit}:{candidate}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout
    return None


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


def parse_validation_contract(block: str) -> str | None:
    """A generated guardrail may opt into a deterministic validation contract:
    a line of the form `-- @validation: <shell command>` inside the block. The
    command must exit 0 against the edited skill file before the edit is kept
    (Flux lesson: playbooks declare validation, not just intent). Returns the
    command, or None when the proposal carries no contract (behavior unchanged).
    """
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("-- @validation:"):
            cmd = stripped.split(":", 1)[1].strip()
            return cmd or None
    return None


def _validation_argv(cmd: str) -> tuple[list[str] | None, str]:
    """Parse and constrain an LLM-authored validation command.

    Validation contracts are untrusted text. They may invoke a small set of
    local verification tools, but they may not use a shell, environment
    assignments, redirection, pipelines, command substitution, or arbitrary
    executables.
    """
    if not cmd.strip():
        return None, "validation command is empty"
    if any(token in cmd for token in ("`", "$(", "${", "\n", "\r", "\\\n")):
        return None, "validation command contains shell expansion or a newline"
    if any(character in cmd for character in ";&|<>"):
        return None, "validation command contains a shell control operator"
    try:
        argv = shlex.split(cmd, posix=True)
    except ValueError as exc:
        return None, f"invalid validation command: {exc}"
    if len(argv) > MAX_VALIDATION_ARGS:
        return None, f"validation command exceeds {MAX_VALIDATION_ARGS} arguments"
    executable = Path(argv[0]).name
    if argv[0] != executable or executable not in ALLOWED_VALIDATION_COMMANDS:
        return None, f"validation executable is not allowed: {argv[0]}"
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        if len(argv) < 2 or argv[1] not in {"test", "run"}:
            return None, f"{executable} validation must use 'test' or 'run'"
        if argv[1] == "run" and (
            len(argv) < 3 or argv[2] not in {"check", "lint", "test", "typecheck"}
        ):
            return None, f"{executable} run is limited to check, lint, test, or typecheck"
    if executable in {"python", "python3"} and (
        len(argv) < 3 or argv[1] != "-m" or argv[2] not in {"compileall", "pytest"}
    ):
        return None, "Python validation is limited to '-m compileall' or '-m pytest'"
    if executable == "cargo" and (len(argv) < 2 or argv[1] not in {"check", "test"}):
        return None, "cargo validation must use 'check' or 'test'"
    if executable == "go" and (len(argv) < 2 or argv[1] != "test"):
        return None, "go validation must use 'test'"
    return argv, ""


def run_validation(cmd: str, cwd: Path, timeout: int = 90) -> tuple[bool, str]:
    """Run a bounded validation command without invoking a shell."""
    argv, error = _validation_argv(cmd)
    if argv is None:
        return False, error
    executable = shutil.which(argv[0])
    if executable is None:
        return False, f"validation executable not found: {argv[0]}"
    argv[0] = executable
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()[-500:]
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)[:500]


# ── Skill ↔ file resolution (Claude commands + pi skills) ────────────────────────────
# Surfaces (editable_surfaces.json):
#   claude: ~/.claude/commands/{skill}.md  (real files only, no plugins/symlinks)
#   pi:     ~/.pi/agent/skills/{skill}/SKILL.md  OR  ~/.pi/agent/skills/{skill}.md
def skill_file(skill: str) -> Path | None:
    """Resolve skill → live path. Prefer Claude command, then pi skill dir/file."""
    resolved = skill_file_with_surface(skill)
    return resolved[0] if resolved else None


def _safe_skill_path(path: Path, root: Path, *, nested: bool) -> bool:
    try:
        if not path.is_file() or path.is_symlink():
            return False
        resolved = path.resolve()
        resolved_root = root.resolve()
        return resolved_root in resolved.parents if nested else resolved.parent == resolved_root
    except OSError:
        return False


def skill_file_with_surface(skill: str) -> tuple[Path, str] | None:
    if not _valid_skill_name(skill):
        return None
    command = COMMANDS_DIR / f"{skill}.md"
    if _safe_skill_path(command, COMMANDS_DIR, nested=False):
        return command, "claude"
    package = PI_SKILLS_DIR / skill / "SKILL.md"
    if _safe_skill_path(package, PI_SKILLS_DIR, nested=True):
        return package, "pi"
    flat = PI_SKILLS_DIR / f"{skill}.md"
    if _safe_skill_path(flat, PI_SKILLS_DIR, nested=False):
        return flat, "pi"
    return None


# ── Stats ────────────────────────────────────────────────────────────────────────────
def skill_sessions(entries: list[Any], skill: str, since: str | None = None) -> list[Any]:
    """Return valid sessions attributed through primary or candidate skill labels."""
    normalized_skill = skill.lower()
    out: list[Any] = []
    for entry in entries:
        timestamp = getattr(entry, "timestamp", "")
        if since is not None and isinstance(timestamp, str) and timestamp <= since:
            continue
        primary_value = getattr(entry, "skill", "")
        primary = primary_value.lower() if isinstance(primary_value, str) else ""
        raw_cands = getattr(entry, "skill_candidates", None)
        candidates = {
            candidate.lower()
            for candidate in raw_cands
            if isinstance(candidate, str) and candidate
        } if isinstance(raw_cands, list) else set()
        if primary == normalized_skill or normalized_skill in candidates:
            out.append(entry)
    return out


def count_guardrail_bullets(text: str) -> int:
    """Count bullet lines inside the auto-learned guardrails section."""
    if START not in text or END not in text:
        return 0
    body = text.split(START, 1)[1].split(END, 1)[0]
    return sum(1 for ln in body.splitlines() if ln.strip().startswith(("-", "*", "•")))


def fail_rate(sessions: list[Any]) -> tuple[float, int]:
    ratings = [
        float(value)
        for entry in sessions
        if isinstance((value := getattr(entry, "rating", None)), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    if not ratings:
        return 0.0, 0
    low = sum(1 for rating in ratings if rating <= LOW)
    return low / len(ratings), low


def dominant_pattern(sessions: list[Any]) -> str:
    low = [
        entry
        for entry in sessions
        if isinstance(getattr(entry, "rating", None), (int, float))
        and not isinstance(getattr(entry, "rating", None), bool)
        and float(entry.rating) <= LOW
    ]
    counts = Counter(
        pattern
        for entry in low
        for pattern in classify_entry(entry)
        if isinstance(pattern, str) and pattern not in {"", "other"}
    )
    return counts.most_common(1)[0][0] if counts else "general_quality"


# ── Ledger ────────────────────────────────────────────────────────────────────────────
def _valid_edit_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and _valid_skill_name(value.get("skill"))
        and isinstance(value.get("pattern"), str)
        and bool(value["pattern"])
        and isinstance(value.get("status"), str)
        and bool(value["status"])
    )


def normalize_ledger(value: object) -> dict[str, list[dict[str, Any]]]:
    data = value if isinstance(value, dict) else {}
    raw_edits = data.get("edits")
    raw_invalid = data.get("invalid_edits")
    log = data.get("log")
    edits = raw_edits if isinstance(raw_edits, list) else []
    invalid = [record for record in edits if not _valid_edit_record(record)]
    if isinstance(raw_invalid, list):
        invalid.extend(record for record in raw_invalid if isinstance(record, dict))
    return {
        "edits": [record for record in edits if _valid_edit_record(record)],
        "invalid_edits": [record for record in invalid if isinstance(record, dict)],
        "log": [record for record in log if isinstance(record, dict)]
        if isinstance(log, list)
        else [],
    }


def load_ledger() -> dict[str, list[dict[str, Any]]]:
    data, _error = try_read_json_object(LEDGER_FILE)
    return normalize_ledger(data)


def save_ledger(ledger: dict[str, Any], *, locked: bool = False) -> None:
    normalized = normalize_ledger(ledger)
    if locked:
        atomic_write_json(LEDGER_FILE, normalized)
        return
    with exclusive_lock(LEDGER_FILE):
        atomic_write_json(LEDGER_FILE, normalized)


def active_edit_for(ledger: dict[str, Any], skill: str) -> dict[str, Any] | None:
    for edit in ledger.get("edits", []):
        if _valid_edit_record(edit) and edit.get("skill") == skill and edit.get("status") == "active":
            return edit
    return None


def reverted_patterns(ledger: dict[str, Any], skill: str) -> set[str]:
    """Patterns already tried and reverted for this skill."""
    return {
        str(edit["pattern"])
        for edit in ledger.get("edits", [])
        if _valid_edit_record(edit)
        and edit.get("skill") == skill
        and edit.get("status") == "reverted"
    }


# ── LLM: generate the bounded guardrail block (only thing the model writes) ───────────
def _passing_behaviors(entries: list, skill: str) -> str:
    """Summarize well-formed passing behavior without trusting persisted row shapes."""
    good = [
        entry
        for entry in entries
        if getattr(entry, "skill", None) == skill
        and isinstance((rating := getattr(entry, "rating", None)), (int, float))
        and not isinstance(rating, bool)
        and math.isfinite(float(rating))
        and float(rating) >= 7
    ]
    if not good:
        return "(no high-rated sessions attributed to this skill yet)"
    lines = []
    for entry in sorted(good, key=lambda item: -float(item.rating))[:4]:
        summary = getattr(entry, "sentiment_summary", "")
        preview = getattr(entry, "response_preview", "")
        summary = summary if isinstance(summary, str) else ""
        preview = preview if isinstance(preview, str) else ""
        text = (summary or preview).strip()[:160]
        if text:
            lines.append(f"- [{entry.rating}/10] {text}")
    return "\n".join(lines) or "(no summaries on high-rated sessions)"


def _prior_failed_edits(ledger: dict, skill: str) -> str:
    """Include only well-formed reverted edits so malformed history cannot crash proposals."""
    raw_edits = ledger.get("edits")
    failed = [
        edit
        for edit in raw_edits
        if isinstance(edit, dict)
        and edit.get("skill") == skill
        and edit.get("status") == "reverted"
    ] if isinstance(raw_edits, list) else []
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
def _normalized_rate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        rate = float(value)
    except ValueError:
        return None
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        return None
    return rate


def _record_rollback_failure(
    edit: dict[str, Any], today: str, reason: str, changes: list[str]
) -> None:
    edit["status"] = "rollback-failed"
    edit["rollback_failed"] = today
    edit["rollback_error"] = reason[:500]
    changes.append(f"ROLLBACK FAILED /{edit['skill']} — {reason[:200]}")


def evaluate_active(
    ledger: dict[str, Any],
    entries: list[Any],
    today: str,
    changes: list[str],
    dry_run: bool,
) -> None:
    for edit in ledger.get("edits", []):
        if not _valid_edit_record(edit) or edit.get("status") != "active":
            continue
        applied = edit.get("applied")
        baseline = _normalized_rate(edit.get("baseline_fail_rate"))
        if not isinstance(applied, str) or not applied or baseline is None:
            edit["status"] = "invalid"
            edit["invalid_at"] = today
            edit["invalid_reason"] = "active edit requires applied date and baseline_fail_rate in [0,1]"
            changes.append(f"INVALID /{edit['skill']} — malformed active edit state")
            continue

        post = skill_sessions(entries, str(edit["skill"]), since=applied)
        rate, _ = fail_rate(post)
        edit["post_n"] = len(post)
        if len(post) < MIN_AFTER:
            continue
        verdict = verdict_for(baseline, rate, len(post), MIN_AFTER)
        edit["post_fail_rate"] = round(rate, 3)
        edit["verdict"] = verdict
        surface = edit.get("surface") if edit.get("surface") in {"claude", "pi"} else "claude"

        if verdict in {"flat", "regressed"}:
            resolved = skill_file_with_surface(str(edit["skill"]))
            if resolved is None:
                _record_rollback_failure(edit, today, "live skill file is unavailable", changes)
                continue
            live, surface = resolved
            commit_before = edit.get("commit_before")
            prior = content_at(str(edit["skill"]), commit_before, surface)
            if prior is None:
                _record_rollback_failure(edit, today, "trusted pre-edit snapshot is unavailable", changes)
                continue
            if dry_run:
                changes.append(
                    f"WOULD REVERT /{edit['skill']} ({surface}) — {verdict} "
                    f"(post {rate:.2f} ≥ base {baseline:.2f})"
                )
                continue
            try:
                with exclusive_lock(live):
                    atomic_write_text(live, prior)
                    try:
                        rollback_commit = snapshot(
                            str(edit["skill"]),
                            live,
                            f"revert /{edit['skill']} — {verdict} post={rate:.2f} base={baseline:.2f}",
                            surface=surface,
                        )
                    except (OSError, RuntimeError) as exc:
                        edit["status"] = "reverted-audit-failed"
                        edit["reverted"] = today
                        edit["rollback_audit_error"] = str(exc)[:500]
                        changes.append(
                            f"REVERTED /{edit['skill']} ({surface}) — snapshot audit failed: {exc}"
                        )
                        continue
            except (OSError, TimeoutError) as exc:
                _record_rollback_failure(edit, today, f"restore failed: {exc}", changes)
                continue
            edit["status"] = "reverted"
            edit["reverted"] = today
            edit["rollback_commit"] = rollback_commit
            edit.pop("rollback_error", None)
            changes.append(
                f"REVERTED /{edit['skill']} ({surface}) — {verdict} "
                f"(post {rate:.2f} ≥ base {baseline:.2f})"
            )
        elif verdict in {"working", "improving", "resolved"}:
            edit["status"] = "confirmed"
            edit["confirmed"] = today
            changes.append(
                f"confirmed /{edit['skill']} ({surface}) — {verdict} "
                f"(post {rate:.2f} < base {baseline:.2f})"
            )


# ── Propose + apply new edits for qualifying skills ───────────────────────────────────
def _apply_guardrail(
    *,
    skill: str,
    pattern: str,
    surface: str,
    live: Path,
    block: str,
    today: str,
    baseline_rate: float,
    baseline_n: int,
) -> tuple[dict[str, Any] | None, str]:
    validation_cmd = parse_validation_contract(block)
    record: dict[str, Any] = {
        "skill": skill,
        "pattern": pattern,
        "surface": surface,
        "baseline_fail_rate": round(baseline_rate, 3),
        "baseline_n": baseline_n,
        "expected_outcome": (
            f"fix '{pattern}' on /{skill} "
            f"(baseline fail rate {baseline_rate:.2f}, n={baseline_n})"
        ),
        "validation": None,
        "applied": today,
        "post_n": 0,
    }
    try:
        with exclusive_lock(live):
            original = live.read_text()
            try:
                commit_before = snapshot(
                    skill, live, f"before autofix /{skill} ({pattern})", surface=surface
                )
            except (OSError, RuntimeError) as exc:
                return None, f"deferred /{skill} — pre-edit snapshot failed: {exc}"
            record["commit_before"] = commit_before

            new_content = upsert_section(original, block)
            if not validate_skill_content(new_content):
                return None, f"deferred /{skill} — generated content failed format validation"

            atomic_write_text(live, new_content)
            try:
                commit_applied = snapshot(
                    skill, live, f"autofix /{skill} ({pattern})", surface=surface
                )
            except (OSError, RuntimeError) as exc:
                try:
                    atomic_write_text(live, original)
                except OSError as restore_exc:
                    record.update(
                        {
                            "status": "rollback-failed",
                            "rollback_error": str(restore_exc)[:500],
                            "apply_error": str(exc)[:500],
                        }
                    )
                    return record, (
                        f"ROLLBACK FAILED /{skill} [{surface}] after snapshot failure: "
                        f"{restore_exc}"
                    )
                record.update({"status": "apply-audit-failed", "apply_error": str(exc)[:500]})
                return record, f"deferred /{skill} — applied snapshot failed; live file restored: {exc}"

            record["commit_applied"] = commit_applied
            record["commit_after"] = commit_applied
            if not validation_cmd:
                record["status"] = "active"
                return record, (
                    f"APPLIED /{skill} [{surface}] — guardrail for '{pattern}' "
                    f"(baseline {baseline_rate:.2f}, n={baseline_n})"
                )

            ok, validation_note = run_validation(validation_cmd, live.parent)
            record["validation"] = (
                f"pass: {validation_note[:200]}" if ok else f"fail: {validation_note[:200]}"
            )
            if ok:
                record["status"] = "active"
                return record, (
                    f"APPLIED /{skill} [{surface}] — guardrail for '{pattern}' "
                    f"(baseline {baseline_rate:.2f}, n={baseline_n})"
                )

            try:
                atomic_write_text(live, original)
            except OSError as exc:
                record.update({"status": "rollback-failed", "rollback_error": str(exc)[:500]})
                return record, f"ROLLBACK FAILED /{skill} [{surface}] — validation failed: {exc}"
            try:
                rollback_commit = snapshot(
                    skill,
                    live,
                    f"revert /{skill} ({pattern}) validation-failed",
                    surface=surface,
                )
            except (OSError, RuntimeError) as exc:
                record.update(
                    {
                        "status": "validation-failed-audit-failed",
                        "rollback_audit_error": str(exc)[:500],
                    }
                )
                return record, (
                    f"REVERTED /{skill} [{surface}] — validation failed; "
                    f"rollback snapshot failed: {exc}"
                )
            record.update(
                {
                    "status": "validation-failed",
                    "commit_after": rollback_commit,
                    "rollback_commit": rollback_commit,
                }
            )
            return record, (
                f"REVERTED /{skill} [{surface}] — validation contract failed: "
                f"{validation_note[:200]}"
            )
    except (OSError, TimeoutError) as exc:
        return None, f"deferred /{skill} — skill mutation unavailable: {exc}"


def propose_new(
    ledger: dict[str, Any],
    entries: list[Any],
    today: str,
    changes: list[str],
    use_llm: bool,
    dry_run: bool,
) -> list[str]:
    candidates: list[str] = []
    # Skills that appear in attributed ratings, ranked by low-rated volume.
    # general-session is the dump-bin fallback — never grow it past MAX bullets.
    skills = Counter(
        skill.lower()
        for entry in entries
        if isinstance((skill := getattr(entry, "skill", None)), str)
        and _valid_skill_name(skill)
        and isinstance((rating := getattr(entry, "rating", None)), (int, float))
        and not isinstance(rating, bool)
        and math.isfinite(float(rating))
        and float(rating) <= LOW
    )
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
        record, outcome = _apply_guardrail(
            skill=skill,
            pattern=pattern,
            surface=surface,
            live=live,
            block=block,
            today=today,
            baseline_rate=rate,
            baseline_n=len(sessions),
        )
        if record is not None:
            ledger.setdefault("edits", []).append(record)
        changes.append(outcome)
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
    """Fail closed for new mutations while preserving evaluate/revert behavior."""
    suite = Path(__file__).resolve().parent / "held_out_suite.py"
    if not suite.is_file():
        return False, "held_out_suite.py missing — new mutations blocked"
    try:
        proc = subprocess.run(
            [sys.executable, str(suite), "--gate"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"held_out_suite gate unavailable: {exc}"
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
        return False, f"held_out_suite gate FAIL (rc={proc.returncode}): {tail}"

    # Agent rollouts are a supplemental live-provider gate. Missing state is allowed
    # for bootstrap, but an existing malformed result must never authorize mutation.
    last = STATE_DIR / "agent_rollouts_last.json"
    if not last.exists():
        return True, "held_out_suite PASS; agent_rollouts no prior run (not blocking)"
    data, error = try_read_json_object(last)
    if error:
        return False, f"agent_rollouts gate state invalid: {error}"
    summary = data.get("summary")
    gate = data.get("gate")
    if not isinstance(summary, dict) or not isinstance(gate, dict):
        return False, "agent_rollouts gate state invalid: expected summary and gate objects"
    if summary.get("skipped_all") is True:
        return True, "held_out_suite PASS; agent_rollouts skipped (no LLM)"
    rate = _normalized_rate(summary.get("pass_rate"))
    if rate is None:
        return False, "agent_rollouts gate state invalid: pass_rate must be in [0,1]"
    if rate < 0.75:
        return False, f"agent_rollouts gate FAIL pass_rate={rate:.1%} < 75% (see {last})"
    if gate.get("has_baseline") is True and gate.get("gate_pass") is not True:
        return False, f"agent_rollouts baseline regression or invalid gate result (see {last})"
    return True, (
        f"held_out_suite PASS; agent_rollouts PASS "
        f"(pass_rate={rate:.1%} n={summary.get('n')})"
    )


# ── Main ──────────────────────────────────────────────────────────────────────────────
def _run_cycle(args: argparse.Namespace) -> int:
    ledger = load_ledger()
    entries = load_all_ratings(RATINGS_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    dry = args.dry_run or not args.apply
    use_llm = not args.no_llm

    if not dry:
        ensure_repo()

    changes: list[str] = []
    # Always evaluate/revert first — reverts reduce risk even when new applies are blocked.
    evaluate_active(ledger, entries, today, changes, dry)

    allow_new, gate_msg = (True, "dry-run/no-apply") if dry else (
        (True, "forced") if args.force else suite_gate_allows_apply()
    )
    if not allow_new:
        changes.append(f"BLOCKED new skill applies — {gate_msg}")
        print(f"[skill_autofix] {gate_msg}")
        print("[skill_autofix] NEW applies blocked; reverts/measure-after still applied above.")
        candidates = propose_new(
            ledger, entries, today, changes, use_llm=False, dry_run=True
        )
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
    save_ledger(ledger, locked=True)
    atomic_write_text(DIAG_DIR / f"skill_autofix_{today}.md", report)
    print(f"Wrote: {LEDGER_FILE}")
    print(f"Wrote: {DIAG_DIR / f'skill_autofix_{today}.md'}")
    return 0 if allow_new else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Skill auto-fix (L2 gated auto-apply)")
    ap.add_argument("--apply", action="store_true", help="evaluate + apply (writes files/ledger)")
    ap.add_argument("--dry-run", action="store_true", help="report only, touch nothing")
    ap.add_argument("--no-llm", action="store_true", help="deterministic only: revert + flag, no new edits")
    ap.add_argument("--status", action="store_true", help="print the ledger and exit")
    ap.add_argument("--force", action="store_true",
                    help="bypass held_out_suite gate for NEW applies (danger; reverts still run)")
    args = ap.parse_args(argv)

    if args.status:
        print(json.dumps(load_ledger(), indent=2))
        return 0
    dry = args.dry_run or not args.apply
    try:
        if dry:
            return _run_cycle(args)
        with exclusive_lock(LEDGER_FILE):
            return _run_cycle(args)
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"[skill_autofix] mutation cycle failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
