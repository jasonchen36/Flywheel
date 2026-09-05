#!/usr/bin/env python3
"""
self_harness.py — Self-Harness (Zhang et al. 2026) stage coordinator.

Lil'Log (Weng 2026) frames Self-Harness as:

  1. Weakness mining   — cluster failures into verifier-grounded patterns
  2. Harness proposal  — bounded edits on an explicit editable surface
  3. Proposal validation — held-in + held-out; accept only if no regression

This script does NOT replace the existing loop scripts. It:
  - enforces editable_surfaces.json (permission lives OUTSIDE mutation loop)
  - runs the three Self-Harness stages as a report + gate
  - archives negative results (failed/reverted edits, abandoned variants)
  - applies diversity rejection against near-duplicate active lessons
  - rebuilds the ACE playbook after validation

Usage:
  python3 self_harness.py                 # full mine→propose→validate report
  python3 self_harness.py --stage mine
  python3 self_harness.py --stage validate
  python3 self_harness.py --apply         # also rebuild ace_playbook + run held_out_suite
  python3 self_harness.py --gate          # exit 1 if held_out_suite gate fails
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from state_io import append_jsonl, atomic_write_text, load_jsonl_objects, try_read_json_object

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from self_improve import load_all_ratings, classify_entry, RATINGS_FILE  # noqa: E402

SURFACES = ROOT / "editable_surfaces.json"
SCORES = HARNESS_HOME / "MEMORY/STATE/effectiveness_scores.json"
LEDGER = HARNESS_HOME / "MEMORY/STATE/skill_autofix_ledger.json"
NEG = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS/negative_results.jsonl"
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
ARCHIVE = HARNESS_HOME / "MEMORY/STATE/harness_candidates.jsonl"
LESSONS_DIR = HARNESS_HOME / "MEMORY/lessons"
LOW = 4


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_surfaces() -> dict:
    data, error = try_read_json_object(SURFACES)
    if error:
        return {"allow": [], "deny": []}
    allow = data.get("allow")
    deny = data.get("deny")
    return {
        "allow": [entry for entry in allow if isinstance(entry, dict)]
        if isinstance(allow, list) else [],
        "deny": deny if isinstance(deny, list) else [],
    }


def load_scores() -> dict[str, dict]:
    data, _error = try_read_json_object(SCORES)
    scores = data.get("scores")
    if not isinstance(scores, dict):
        return {}
    return {
        str(pattern): value
        for pattern, value in scores.items()
        if isinstance(value, dict)
    }


def load_child_result(path: Path) -> tuple[dict, dict]:
    data, _error = try_read_json_object(path)
    summary = data.get("summary")
    gate = data.get("gate")
    return (
        summary if isinstance(summary, dict) else {},
        gate if isinstance(gate, dict) else {},
    )


# ── Stage 1: Weakness mining ──────────────────────────────────────────────────

def stage_mine() -> dict:
    """Cluster low-rated sessions into verifier-grounded failure patterns.

    Self-Harness: terminal cause + agent mechanism, not just surface error string.
    """
    entries = load_all_ratings(RATINGS_FILE)
    low = [e for e in entries if e.rating <= LOW]
    pattern_hits: Counter = Counter()
    skill_hits: Counter = Counter()
    rich: list[dict] = []

    for e in low:
        pats = [p for p in classify_entry(e) if p != "other"] or ["other"]
        for p in pats:
            pattern_hits[p] += 1
        if e.skill:
            skill_hits[e.skill] += 1
        rich.append({
            "timestamp": e.timestamp,
            "rating": e.rating,
            "patterns": pats,
            "skill": e.skill or None,
            "summary": (e.sentiment_summary or "")[:200],
            # Self-Harness "rich failure record" fields (best-effort from rating)
            "terminal_cause": pats[0],
            "agent_mechanism": e.skill or "unattributed",
        })

    top = pattern_hits.most_common(15)
    return {
        "stage": "mine",
        "low_n": len(low),
        "total_n": len(entries),
        "top_patterns": top,
        "top_skills": skill_hits.most_common(10),
        "addressable": [p for p, n in top if n >= 2 and p != "other"],
        "sample_rich": rich[-5:],
    }


# ── Stage 2: Bounded proposal inventory ───────────────────────────────────────

def stage_propose(mine: dict) -> dict:
    """List what the harness *could* mutate, constrained by editable_surfaces."""
    surfaces = load_surfaces()
    allow_ids = [a.get("id") for a in surfaces.get("allow", [])]
    scores = load_scores()

    proposals = []
    for pat in mine.get("addressable", []):
        v = (scores.get(pat) or {}).get("verdict", "pending")
        # Prefer recurrent, addressable, non-task-specific (Self-Harness)
        if v in ("regressed", "flat", "pending"):
            proposals.append({
                "pattern": pat,
                "verdict": v,
                "surface": "lesson_autogen",
                "action": "evolve_or_reinforce",
                "in_allowlist": "lesson_autogen" in allow_ids,
            })

    # skill autofix candidates from attributed skills
    for skill, n in mine.get("top_skills", []):
        proposals.append({
            "pattern": f"skill:{skill}",
            "verdict": "skill_fail_concentration",
            "surface": "skill_guardrails",
            "action": f"skill_autofix /{skill} (n_low={n})",
            "in_allowlist": "skill_guardrails" in allow_ids,
        })

    # filter deny — never propose outside allowlist
    proposals = [p for p in proposals if p["in_allowlist"]]
    return {
        "stage": "propose",
        "allow_ids": allow_ids,
        "deny_count": len(surfaces.get("deny", [])),
        "proposals": proposals[:25],
        "proposal_n": len(proposals),
    }


# ── Stage 3: Validation (held-in + held-out + diversity) ─────────────────────

def run_held_out_suite(gate: bool = False) -> dict:
    """Metric-driven D_in/D_out fixture suite (Self-Harness accept gate)."""
    cmd = [sys.executable, str(ROOT / "held_out_suite.py")]
    if gate:
        cmd.append("--gate")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    summary, gate_state = load_child_result(
        HARNESS_HOME / "MEMORY/STATE/held_out_suite_last.json"
    )
    return {
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-500:],
        "summary": summary,
        "gate": gate_state,
        "suite_accept": summary.get("accept"),
        "gate_pass": gate_state.get("gate_pass", proc.returncode == 0),
    }


def run_agent_rollouts(gate: bool = False, no_llm: bool = False) -> dict:
    """Agent-executed PR-review rollouts (semantic Self-Harness half)."""
    cmd = [sys.executable, str(ROOT / "agent_rollouts.py")]
    if gate:
        cmd.append("--gate")
    if no_llm:
        cmd.append("--no-llm")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    summary, gate_state = load_child_result(
        HARNESS_HOME / "MEMORY/STATE/agent_rollouts_last.json"
    )
    return {
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-500:],
        "summary": summary,
        "gate": gate_state,
        "suite_accept": summary.get("accept"),
        "gate_pass": gate_state.get("gate_pass", proc.returncode == 0),
        "pass_rate": summary.get("pass_rate"),
    }


def stage_validate(run_suite: bool = True, run_rollouts: bool = True) -> dict:
    """Summarize held-in/held-out health + log negative results from reverts."""
    scores = load_scores()

    by_verdict = Counter(v.get("verdict", "?") for v in scores.values())
    regressed = [p for p, v in scores.items() if v.get("verdict") == "regressed"]
    resolved = [p for p, v in scores.items() if v.get("verdict") == "resolved"]

    # skill autofix reverts → negative results archive (Lil'Log: preserve failures)
    reverts_logged = 0
    ledger, _ledger_error = try_read_json_object(LEDGER)
    edits_value = ledger.get("edits")
    edits = [edit for edit in edits_value if isinstance(edit, dict)] \
        if isinstance(edits_value, list) else []
    archived_reverts = {
        (
            record.get("skill"),
            record.get("pattern"),
            record.get("commit_after"),
            record.get("applied"),
        )
        for record in load_jsonl_objects(NEG).records
        if record.get("kind") == "skill_autofix_revert"
    }
    for edit in edits:
        if edit.get("status") != "reverted":
            continue
        archive_key = (
            edit.get("skill"),
            edit.get("pattern"),
            edit.get("commit_after"),
            edit.get("applied"),
        )
        if archive_key in archived_reverts:
            continue
        append_jsonl(NEG, {
            "ts": now_iso(),
            "kind": "skill_autofix_revert",
            "skill": edit.get("skill"),
            "pattern": edit.get("pattern"),
            "verdict": edit.get("verdict"),
            "baseline_fail_rate": edit.get("baseline_fail_rate"),
            "post_fail_rate": edit.get("post_fail_rate"),
            "commit_after": edit.get("commit_after"),
            "applied": edit.get("applied"),
            "note": "preserved as negative result — do not retry same pattern until new signal",
        })
        archived_reverts.add(archive_key)
        reverts_logged += 1

    # diversity: near-duplicate active lesson rules (Lil'Log: diversity collapse risk)
    rules: list[tuple[str, str]] = []
    if LESSONS_DIR.exists():
        for p in sorted(LESSONS_DIR.glob("lesson_autogen_*.md")):
            text = p.read_text(errors="replace")
            parts = text.split("---", 2)
            body = (parts[2] if len(parts) >= 3 else text).lstrip("\n")
            rule = next((ln.strip() for ln in body.splitlines()
                         if ln.strip() and not ln.strip().startswith("**")
                         and not ln.strip().startswith("#")), "")
            pat = p.name.removeprefix("lesson_autogen_").removesuffix(".md")
            rules.append((pat, rule))

    def toks(s: str) -> set[str]:
        return set(re.findall(r"[a-z]{4,}", (s or "").lower()))

    near_dupes = []
    for i, (p1, r1) in enumerate(rules):
        t1 = toks(r1)
        if not t1:
            continue
        for p2, r2 in rules[i + 1:]:
            t2 = toks(r2)
            if not t2:
                continue
            j = len(t1 & t2) / len(t1 | t2)
            if j >= 0.75:
                near_dupes.append({"a": p1, "b": p2, "jaccard": round(j, 3)})

    suite = run_held_out_suite(gate=False) if run_suite else {}
    # Agent rollouts: run with LLM when available; --no-llm falls back gracefully.
    # Skip when session-end already ran agent_rollouts.py (pass run_rollouts=False).
    if run_rollouts:
        rollouts = run_agent_rollouts(gate=False, no_llm=False)
    else:
        last_path = HARNESS_HOME / "MEMORY/STATE/agent_rollouts_last.json"
        rollout_summary, rollout_gate = load_child_result(last_path)
        rollouts = {
            "summary": rollout_summary,
            "gate": rollout_gate,
            "exit_code": 0,
            "pass_rate": rollout_summary.get("pass_rate"),
            "suite_accept": rollout_summary.get("accept"),
            "gate_pass": rollout_gate.get("gate_pass", True),
        }

    return {
        "stage": "validate",
        "held_in_verdicts": dict(by_verdict),
        "regressed": regressed,
        "resolved_n": len(resolved),
        "skill_reverts_logged": reverts_logged,
        "near_duplicate_lessons": near_dupes[:20],
        "held_out_suite": {
            "d_in_rate": (suite.get("summary") or {}).get("d_in", {}).get("pass_rate"),
            "d_out_rate": (suite.get("summary") or {}).get("d_out", {}).get("pass_rate"),
            "suite_accept": suite.get("suite_accept"),
            "gate_pass": suite.get("gate_pass"),
            "exit_code": suite.get("exit_code"),
        },
        "agent_rollouts": {
            "pass_rate": rollouts.get("pass_rate"),
            "suite_accept": rollouts.get("suite_accept"),
            "gate_pass": rollouts.get("gate_pass"),
            "exit_code": rollouts.get("exit_code"),
            "d_in": (rollouts.get("summary") or {}).get("d_in"),
            "d_out": (rollouts.get("summary") or {}).get("d_out"),
            "skipped_all": (rollouts.get("summary") or {}).get("skipped_all", False),
        },
        "accept_policy": (
            "Accept harness edit only if: "
            "(1) surface in editable_surfaces.json allowlist, "
            "(2) held-in not regressed (measure_effectiveness), "
            "(3) live held-out not regressed (held_out_regression), "
            "(4) fixture suite D_in+D_out pass (held_out_suite.py --gate), "
            "(5) agent_rollouts pass_rate ≥ 75% and no D_out regression, "
            "(6) not a near-dupe of an existing active lesson (jaccard≥0.75). "
            "skill_autofix NEW applies hard-blocked when (4) or (5) fail."
        ),
    }


# ── Orchestrate ───────────────────────────────────────────────────────────────

def run_ace_playbook() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "ace_playbook.py")],
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Self-Harness stage coordinator (Weng/Lil'Log)")
    ap.add_argument("--stage", choices=["mine", "propose", "validate", "all"], default="all")
    ap.add_argument("--apply", action="store_true", help="rebuild ACE playbook after validate")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 if held_out_suite fails or regressed vs baseline")
    ap.add_argument("--skip-rollouts", action="store_true",
                    help="do not re-run agent_rollouts (use last result; session-end sets this)")
    args = ap.parse_args(argv)

    report: dict = {
        "ts": now_iso(),
        "framework": "Self-Harness + ACE via Weng 2026 https://lilianweng.github.io/posts/2026-07-04-harness/",
        "stages": {},
    }

    mine = propose = validate = None
    if args.stage in ("mine", "all"):
        mine = stage_mine()
        report["stages"]["mine"] = mine
        print(f"[self_harness:mine] low={mine['low_n']}/{mine['total_n']} "
              f"addressable={mine['addressable'][:8]}")

    if args.stage in ("propose", "all"):
        if mine is None:
            mine = stage_mine()
        propose = stage_propose(mine)
        report["stages"]["propose"] = propose
        print(f"[self_harness:propose] {propose['proposal_n']} proposals "
              f"on surfaces {propose['allow_ids']}")
        for p in propose["proposals"][:8]:
            print(f"  • {p['surface']}: {p['action']} [{p['verdict']}]")

    if args.stage in ("validate", "all"):
        validate = stage_validate(run_suite=True, run_rollouts=not args.skip_rollouts)
        report["stages"]["validate"] = validate
        hs = validate.get("held_out_suite") or {}
        ar = validate.get("agent_rollouts") or {}
        print(f"[self_harness:validate] verdicts={validate['held_in_verdicts']} "
              f"regressed={validate['regressed']} "
              f"near_dupes={len(validate['near_duplicate_lessons'])}")
        print(f"[self_harness:validate] suite D_in={hs.get('d_in_rate')} "
              f"D_out={hs.get('d_out_rate')} accept={hs.get('suite_accept')} "
              f"gate_pass={hs.get('gate_pass')}")
        print(f"[self_harness:validate] rollouts pass_rate={ar.get('pass_rate')} "
              f"accept={ar.get('suite_accept')} gate_pass={ar.get('gate_pass')} "
              f"d_in={ar.get('d_in')} d_out={ar.get('d_out')}")
        print(f"  policy: {validate['accept_policy'][:160]}...")

    day = datetime.now().strftime("%Y-%m-%d")
    out = DIAG / f"self_harness_{day}.json"
    atomic_write_text(out, json.dumps(report, indent=2, default=str) + "\n")
    append_jsonl(ARCHIVE, {
        "ts": now_iso(),
        "kind": "self_harness_cycle",
        "mine_top": (mine or {}).get("top_patterns", [])[:5],
        "proposal_n": (propose or {}).get("proposal_n"),
        "regressed": (validate or {}).get("regressed"),
        "suite_accept": ((validate or {}).get("held_out_suite") or {}).get("suite_accept"),
    })
    print(f"[self_harness] report → {out}")

    if args.apply:
        print("[self_harness] rebuilding ACE playbook...")
        run_ace_playbook()

    if args.gate:
        suite = (
            (validate or {}).get("held_out_suite")
            if validate is not None
            else run_held_out_suite(gate=True)
        ) or {}
        if (
            suite.get("exit_code", 1) != 0
            or suite.get("suite_accept") is not True
            or suite.get("gate_pass") is False
        ):
            print("[self_harness] GATE FAIL — held_out_suite regression or fixture mismatch")
            return 1
        if args.skip_rollouts:
            # Use last agent_rollouts result (session-end already ran them)
            last_path = HARNESS_HOME / "MEMORY/STATE/agent_rollouts_last.json"
            if last_path.exists():
                summary, gate_state = load_child_result(last_path)
                if not summary:
                    print("[self_harness] GATE FAIL — existing rollouts result is unreadable")
                    return 1
                try:
                    rate = float(summary.get("pass_rate") or 0.0)
                except (TypeError, ValueError) as exc:
                    print(f"[self_harness] GATE FAIL — invalid rollout pass rate: {exc}")
                    return 1
                if summary.get("skipped_all"):
                    print("[self_harness] GATE PASS (fixtures; rollouts skipped/no-llm)")
                    return 0
                if rate < 0.75:
                    print(f"[self_harness] GATE FAIL — last agent_rollouts "
                          f"pass_rate={rate:.1%} < 75%")
                    return 1
                if gate_state.get("has_baseline") and gate_state.get("gate_pass") is False:
                    print("[self_harness] GATE FAIL — last agent_rollouts baseline regression")
                    return 1
                print(f"[self_harness] GATE PASS (fixtures + last rollouts "
                      f"pass_rate={rate:.1%})")
                return 0
            print("[self_harness] GATE PASS (fixtures only; no rollouts last)")
            return 0
        rolls = (
            (validate or {}).get("agent_rollouts")
            if validate is not None
            else run_agent_rollouts(gate=True, no_llm=False)
        ) or {}
        if rolls.get("skipped_all"):
            print("[self_harness] GATE PASS (fixtures; rollouts skipped/unavailable)")
            return 0
        try:
            rollout_rate = float(rolls.get("pass_rate") or 0.0)
        except (TypeError, ValueError):
            rollout_rate = 0.0
        if (
            rolls.get("exit_code", 1) != 0
            or rolls.get("suite_accept") is not True
            or rollout_rate < 0.75
            or rolls.get("gate_pass") is False
        ):
            print("[self_harness] GATE FAIL — agent_rollouts regression or below floor")
            print(rolls.get("stdout_tail", "")[-800:])
            return 1
        print("[self_harness] GATE PASS (fixtures + agent_rollouts)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
