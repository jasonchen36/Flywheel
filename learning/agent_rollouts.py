#!/usr/bin/env python3
"""
agent_rollouts.py — agent-executed mini PR-review rollouts for Self-Harness.

Unlike held_out_suite.py (static fixture responses), this:
  1. Loads current ACE playbook bullets (harness context injection)
  2. For each scenario, prompts the model as the coding agent
  3. Scores the transcript with evals.score_text + scenario rubrics
  4. Splits D_in (weakness) vs D_out (preserve-correct)
  5. Gates on pass rates + baseline regression

Lil'Log / Self-Harness: evaluate harness under live model behavior, not only
regex fixtures. Fixtures remain the cheap deterministic half; this is the
semantic half.

Usage:
  python3 agent_rollouts.py                 # run all scenarios
  python3 agent_rollouts.py --no-llm        # skip LLM (exit 0, report skipped)
  python3 agent_rollouts.py --gate          # exit 1 on fail / regression
  python3 agent_rollouts.py --update-baseline
  python3 agent_rollouts.py --limit 3       # smoke
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evals import score_text  # noqa: E402
from self_improve import call_llm  # noqa: E402

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "held_out_suite" / "fixtures" / "agent_rollouts.json"
ACE = Path.home() / ".claude/MEMORY/STATE/ace_playbook.json"
STATE = Path.home() / ".claude/MEMORY/STATE"
DIAG = Path.home() / ".claude/MEMORY/LEARNING/DIAGNOSTICS"
LAST = STATE / "agent_rollouts_last.json"
BASELINE = STATE / "agent_rollouts_baseline.json"
HISTORY = Path.home() / ".claude/MEMORY/LEARNING/SIGNALS" / "agent_rollouts_history.jsonl"
TRANSCRIPTS = DIAG / "agent_rollout_transcripts"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_playbook_bullets(max_n: int = 8) -> str:
    if not ACE.exists():
        return "(no ACE playbook yet)"
    try:
        pb = json.loads(ACE.read_text())
    except json.JSONDecodeError:
        return "(ace_playbook unreadable)"
    lines = []
    for b in pb.get("bullets", []):
        # v2 sections: strategy|pitfall|formula active; resolved|deferred skipped
        if b.get("section") in ("resolved", "deferred"):
            continue
        q = b.get("quality")
        qtag = f" q{q}" if q is not None else ""
        lines.append(f"- [{b.get('pattern')}]{qtag} {b.get('description', '')[:180]}")
        if len(lines) >= max_n:
            break
    return "\n".join(lines) if lines else "(empty playbook)"


def build_prompt(scenario: dict, playbook: str) -> str:
    constraints = "\n".join(f"- {c}" for c in scenario.get("constraints") or [])
    return (
        f"{scenario.get('system_role', 'You are a careful coding agent.')}\n\n"
        f"## Active harness lessons (ACE playbook — obey these)\n{playbook}\n\n"
        f"## Hard constraints for THIS task\n{constraints}\n\n"
        f"## User request\n{scenario.get('user', '')}\n\n"
        "Respond as the agent would in a real session. Be concise. "
        "Do not invent tool output you did not receive. "
        "If a safety rule applies, follow it even if the user asks to skip it."
    )


def rubric_ok(text: str, scenario: dict) -> tuple[bool, list[str]]:
    errs: list[str] = []
    low = text or ""
    for bad in scenario.get("must_not_match") or []:
        if re.search(re.escape(bad), low, re.I):
            errs.append(f"must_not_match hit: {bad!r}")
    must_any = scenario.get("must_match_any") or []
    if must_any and not any(re.search(re.escape(m), low, re.I) for m in must_any):
        errs.append(f"must_match_any missed all of {must_any}")
    # eval_expect
    scored = score_text(low)
    for eid, exp in (scenario.get("eval_expect") or {}).items():
        got = scored.get(eid)
        if not got:
            errs.append(f"eval {eid} missing")
            continue
        if "applied" in exp and got["applied"] != exp["applied"]:
            errs.append(f"eval {eid} applied expected {exp['applied']} got {got['applied']}")
        if "passed" in exp and exp["passed"] is not None and got["passed"] != exp["passed"]:
            errs.append(f"eval {eid} passed expected {exp['passed']} got {got['passed']}")
    return (len(errs) == 0), errs


def run_scenario(scenario: dict, playbook: str, use_llm: bool) -> dict:
    prompt = build_prompt(scenario, playbook)
    if not use_llm:
        return {
            "id": scenario["id"],
            "split": scenario.get("split"),
            "ok": None,
            "skipped": True,
            "errors": ["--no-llm"],
            "response": "",
        }
    response = call_llm(prompt, max_tokens=700) or ""
    ok, errors = rubric_ok(response, scenario)
    return {
        "id": scenario["id"],
        "split": scenario.get("split", "?"),
        "domain": scenario.get("domain"),
        "pattern": scenario.get("pattern"),
        "ok": ok,
        "skipped": False,
        "errors": errors,
        "response": response,
        "response_len": len(response),
    }


def aggregate(results: list[dict]) -> dict:
    usable = [r for r in results if not r.get("skipped")]
    by_split: dict[str, dict] = {}
    for r in usable:
        sp = r.get("split") or "?"
        by_split.setdefault(sp, {"n": 0, "passed": 0, "failed": 0})
        by_split[sp]["n"] += 1
        if r.get("ok"):
            by_split[sp]["passed"] += 1
        else:
            by_split[sp]["failed"] += 1
    for sp, d in by_split.items():
        d["pass_rate"] = round(d["passed"] / d["n"], 4) if d["n"] else 0.0
    d_in = by_split.get("in", {"n": 0, "passed": 0, "failed": 0, "pass_rate": 0.0})
    d_out = by_split.get("out", {"n": 0, "passed": 0, "failed": 0, "pass_rate": 0.0})
    n = len(usable)
    passed = sum(1 for r in usable if r.get("ok"))
    return {
        "ts": now_iso(),
        "n": n,
        "passed": passed,
        "failed": n - passed,
        "pass_rate": round(passed / n, 4) if n else 0.0,
        "d_in": d_in,
        "d_out": d_out,
        "accept": (n > 0 and (n - passed) == 0),
        "skipped_all": len(usable) == 0,
        "failures": [
            {"id": r["id"], "split": r.get("split"), "errors": r.get("errors")}
            for r in usable if not r.get("ok")
        ],
    }


def compare_baseline(current: dict, baseline: dict) -> dict:
    return {
        "has_baseline": True,
        "pass_rate_delta": round(current["pass_rate"] - baseline.get("pass_rate", 0), 4),
        "d_in_delta": round(
            current.get("d_in", {}).get("pass_rate", 0)
            - baseline.get("d_in", {}).get("pass_rate", 0), 4),
        "d_out_delta": round(
            current.get("d_out", {}).get("pass_rate", 0)
            - baseline.get("d_out", {}).get("pass_rate", 0), 4),
        "d_out_regressed": (
            current.get("d_out", {}).get("pass_rate", 0)
            < baseline.get("d_out", {}).get("pass_rate", 0)
        ),
        "d_in_regressed": (
            current.get("d_in", {}).get("pass_rate", 0)
            < baseline.get("d_in", {}).get("pass_rate", 0)
        ),
        "baseline_ts": baseline.get("ts"),
        "gate_pass": (
            current.get("pass_rate", 0) >= baseline.get("pass_rate", 0)
            and current.get("d_out", {}).get("pass_rate", 0)
            >= baseline.get("d_out", {}).get("pass_rate", 0)
        ),
    }


def write_report(summary: dict, results: list[dict], gate: dict | None) -> Path:
    DIAG.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    path = DIAG / f"agent_rollouts_{day}.md"
    lines = [
        f"# Agent rollouts — {summary['ts']}",
        "",
        f"Overall: {summary['passed']}/{summary['n']} "
        f"({summary['pass_rate']:.1%}) accept={summary['accept']}",
        f"D_in: {summary.get('d_in')}",
        f"D_out: {summary.get('d_out')}",
        "",
    ]
    if summary.get("failures"):
        lines.append("## Failures")
        for f in summary["failures"]:
            lines.append(f"- `{f['id']}` ({f.get('split')}): {'; '.join(f.get('errors') or [])}")
        lines.append("")
    if gate:
        lines += [
            "## vs baseline",
            f"- gate_pass: {gate.get('gate_pass')}",
            f"- pass_rate_delta: {gate.get('pass_rate_delta')}",
            f"- d_out_delta: {gate.get('d_out_delta')}",
            "",
        ]
    lines.append("## Per scenario")
    for r in results:
        status = "SKIP" if r.get("skipped") else ("PASS" if r.get("ok") else "FAIL")
        lines.append(f"- {status} `{r['id']}` errors={r.get('errors')}")
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent-executed PR-review rollouts")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="no state writes")
    ap.add_argument("--min-pass-rate", type=float, default=0.75,
                    help="absolute floor for accept when no baseline (default 0.75)")
    args = ap.parse_args()

    data = json.loads(SCENARIOS.read_text())
    scenarios = data.get("scenarios") or []
    if args.limit > 0:
        scenarios = scenarios[: args.limit]

    playbook = load_playbook_bullets()
    results = []
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    for sc in scenarios:
        rec = run_scenario(sc, playbook, use_llm=not args.no_llm)
        results.append(rec)
        if not args.dry_run and rec.get("response"):
            (TRANSCRIPTS / f"{rec['id']}.txt").write_text(rec["response"])
        flag = "SKIP" if rec.get("skipped") else ("PASS" if rec.get("ok") else "FAIL")
        print(f"  [{flag}] {rec['id']}"
              + (f" — {'; '.join(rec.get('errors') or [])}" if rec.get("errors") else ""))

    summary = aggregate(results)
    gate = None
    if BASELINE.exists():
        try:
            gate = compare_baseline(summary, json.loads(BASELINE.read_text()))
        except (json.JSONDecodeError, OSError, KeyError) as e:
            gate = {"has_baseline": False, "error": str(e), "gate_pass": True}

    print(f"[agent_rollouts] {summary['passed']}/{summary['n']} "
          f"({summary['pass_rate']:.1%}) accept={summary['accept']} "
          f"d_in={summary.get('d_in')} d_out={summary.get('d_out')}")
    if gate and gate.get("has_baseline"):
        print(f"[agent_rollouts] vs baseline gate_pass={gate.get('gate_pass')} "
              f"d_out_delta={gate.get('d_out_delta')}")

    if summary.get("skipped_all"):
        print("[agent_rollouts] all skipped (--no-llm or LLM unavailable)")
        if args.gate:
            # Soft: no LLM ≠ harness regression
            return 0
        return 0

    if not args.dry_run:
        STATE.mkdir(parents=True, exist_ok=True)
        LAST.write_text(json.dumps({"summary": summary, "gate": gate, "results": [
            {k: v for k, v in r.items() if k != "response"} for r in results
        ]}, indent=2))
        report = write_report(summary, results, gate)
        print(f"[agent_rollouts] report → {report}")
        with open(HISTORY, "a") as f:
            f.write(json.dumps({
                "ts": summary["ts"],
                "pass_rate": summary["pass_rate"],
                "d_in": summary.get("d_in"),
                "d_out": summary.get("d_out"),
                "accept": summary["accept"],
                "gate_pass": (gate or {}).get("gate_pass"),
            }) + "\n")
        if args.update_baseline or (not BASELINE.exists() and summary["accept"]):
            BASELINE.write_text(json.dumps(summary, indent=2))
            print(f"[agent_rollouts] baseline → {BASELINE}")
        elif args.update_baseline and not summary["accept"]:
            # Still allow freeze after intentional review
            BASELINE.write_text(json.dumps(summary, indent=2))
            print(f"[agent_rollouts] baseline FORCE-updated (suite not clean)")

    if args.gate:
        if summary["pass_rate"] < args.min_pass_rate:
            print(f"[agent_rollouts] GATE FAIL absolute floor "
                  f"{summary['pass_rate']:.1%} < {args.min_pass_rate:.1%}")
            return 1
        if gate and gate.get("has_baseline") and not gate.get("gate_pass"):
            print("[agent_rollouts] GATE FAIL regression vs baseline")
            return 1
        if not summary["accept"] and summary["pass_rate"] < 1.0:
            # Strict mode only when floor says so; accept soft if above floor
            print("[agent_rollouts] GATE PASS (above floor; some scenario fails logged)")
        print("[agent_rollouts] GATE PASS")
        return 0

    return 0 if summary["accept"] or summary.get("skipped_all") else 1


if __name__ == "__main__":
    raise SystemExit(main())
