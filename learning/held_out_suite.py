#!/usr/bin/env python3
"""
held_out_suite.py — metric-driven D_in / D_out eval suite for Self-Harness.

Lil'Log / Self-Harness (Zhang et al. 2026 via Weng 2026):
  Candidates accepted only if no regression on held-in (D_in) AND held-out (D_out).

This is the *fixture* half of that gate (deterministic, no LLM):
  - D_in  : weakness-targeted cases — bad must fail target eval; good must pass
  - D_out : preserve-correct-behavior cases — must not start failing after harness edits

Rating-based held_out_regression.py remains the *live traffic* half.
Together they form the full accept policy.

Usage:
  python3 held_out_suite.py                 # run suite, write report + baseline if missing
  python3 held_out_suite.py --update-baseline  # freeze current scores as baseline
  python3 held_out_suite.py --gate          # exit 1 on D_out regression vs baseline
  python3 held_out_suite.py --json          # machine-readable summary on stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from state_io import append_jsonl, atomic_write_json, atomic_write_text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evals import EVALS, score_text  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "held_out_suite" / "fixtures"
STATE = HARNESS_HOME / "MEMORY/STATE"
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
BASELINE_FILE = STATE / "held_out_suite_baseline.json"
LAST_FILE = STATE / "held_out_suite_last.json"
HISTORY = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS" / "held_out_suite_history.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_split(name: str) -> list[dict]:
    path = FIXTURES / f"{name}.json"
    data = json.loads(path.read_text())
    return data.get("cases", [])


def case_ok(case: dict, scored: dict) -> tuple[bool, list[str]]:
    """Compare scored evals against fixture expectations."""
    errors: list[str] = []
    expect = case.get("expect") or {}
    for eval_id, exp in expect.items():
        got = scored.get(eval_id)
        if got is None:
            errors.append(f"{eval_id}: missing from score_text (eval deleted?)")
            continue
        exp_applied = exp.get("applied")
        exp_passed = exp.get("passed")
        if exp_applied is not None and got["applied"] != exp_applied:
            errors.append(
                f"{eval_id}: applied expected {exp_applied} got {got['applied']}"
            )
        if exp_passed is not None and got["passed"] != exp_passed:
            errors.append(
                f"{eval_id}: passed expected {exp_passed} got {got['passed']}"
            )
        # when expect says applied:false, passed should be null — already covered
        if exp_applied is False and exp_passed is None and got["passed"] is not None:
            errors.append(f"{eval_id}: expected not-applied (passed=null) got {got['passed']}")
    return (len(errors) == 0), errors


def run_split(cases: list[dict]) -> dict:
    results = []
    passed = 0
    failed = 0
    by_domain: dict[str, dict] = {}
    by_pattern: dict[str, dict] = {}

    for case in cases:
        scored = score_text(case.get("response") or "")
        ok, errors = case_ok(case, scored)
        rec = {
            "id": case["id"],
            "domain": case.get("domain", "?"),
            "pattern": case.get("pattern", "?"),
            "ok": ok,
            "errors": errors,
        }
        results.append(rec)
        if ok:
            passed += 1
        else:
            failed += 1
        dom = case.get("domain", "?")
        by_domain.setdefault(dom, {"pass": 0, "fail": 0})
        by_domain[dom]["pass" if ok else "fail"] += 1
        pat = case.get("pattern", "?")
        by_pattern.setdefault(pat, {"pass": 0, "fail": 0})
        by_pattern[pat]["pass" if ok else "fail"] += 1

    n = len(cases) or 1
    return {
        "n": len(cases),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / n, 4),
        "by_domain": by_domain,
        "by_pattern": by_pattern,
        "failures": [r for r in results if not r["ok"]],
        "results": results,
    }


def summarize(d_in: dict, d_out: dict) -> dict:
    return {
        "ts": now_iso(),
        "eval_count": len(EVALS),
        "eval_ids": [e.id for e in EVALS],
        "d_in": {k: d_in[k] for k in ("n", "passed", "failed", "pass_rate", "by_domain", "by_pattern", "failures")},
        "d_out": {k: d_out[k] for k in ("n", "passed", "failed", "pass_rate", "by_domain", "by_pattern", "failures")},
        "accept": d_in["failed"] == 0 and d_out["failed"] == 0,
    }


def compare_baseline(current: dict, baseline: dict) -> dict:
    """Self-Harness gate: D_out must not regress; D_in should not get worse either."""
    out = {
        "has_baseline": True,
        "d_in_delta": round(current["d_in"]["pass_rate"] - baseline["d_in"]["pass_rate"], 4),
        "d_out_delta": round(current["d_out"]["pass_rate"] - baseline["d_out"]["pass_rate"], 4),
        "d_out_regressed": current["d_out"]["pass_rate"] < baseline["d_out"]["pass_rate"],
        "d_in_regressed": current["d_in"]["pass_rate"] < baseline["d_in"]["pass_rate"],
        "baseline_ts": baseline.get("ts"),
    }
    out["gate_pass"] = (not out["d_out_regressed"]) and (not out["d_in_regressed"])
    # New fixtures (n increased) are OK if rates hold; absolute fail on current failures
    # is separate (current accept).
    return out


def write_report(summary: dict, gate: dict | None) -> Path:
    DIAG.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    path = DIAG / f"held_out_suite_{day}.md"
    lines = [
        f"# Held-out suite — {summary['ts']}",
        "",
        f"Evals active: {summary['eval_count']}",
        "",
        f"## D_in (weakness-targeted): {summary['d_in']['passed']}/{summary['d_in']['n']} "
        f"pass_rate={summary['d_in']['pass_rate']:.1%}",
        "",
    ]
    if summary["d_in"]["failures"]:
        lines.append("Failures:")
        for f in summary["d_in"]["failures"]:
            lines.append(f"- `{f['id']}`: {'; '.join(f['errors'])}")
        lines.append("")
    lines += [
        f"## D_out (preserve-correct): {summary['d_out']['passed']}/{summary['d_out']['n']} "
        f"pass_rate={summary['d_out']['pass_rate']:.1%}",
        "",
    ]
    if summary["d_out"]["failures"]:
        lines.append("Failures:")
        for f in summary["d_out"]["failures"]:
            lines.append(f"- `{f['id']}`: {'; '.join(f['errors'])}")
        lines.append("")
    lines.append(f"**Suite accept (all fixtures match):** {summary['accept']}")
    if gate:
        lines += [
            "",
            "## vs baseline (Self-Harness gate)",
            f"- baseline_ts: {gate.get('baseline_ts')}",
            f"- d_in_delta: {gate.get('d_in_delta'):+.4f}",
            f"- d_out_delta: {gate.get('d_out_delta'):+.4f}",
            f"- d_out_regressed: {gate.get('d_out_regressed')}",
            f"- d_in_regressed: {gate.get('d_in_regressed')}",
            f"- **gate_pass: {gate.get('gate_pass')}**",
        ]
    lines.append("")
    atomic_write_text(path, "\n".join(lines))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Held-out fixture suite (Self-Harness D_in/D_out)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="write current results as the regression baseline")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 if suite fails OR D_out/D_in regressed vs baseline")
    ap.add_argument("--json", action="store_true", help="print summary JSON")
    ap.add_argument("--dry-run", action="store_true", help="do not write state files")
    args = ap.parse_args()

    d_in = run_split(load_split("d_in"))
    d_out = run_split(load_split("d_out"))
    summary = summarize(d_in, d_out)

    gate = None
    if BASELINE_FILE.exists():
        try:
            baseline = json.loads(BASELINE_FILE.read_text())
            gate = compare_baseline(summary, baseline)
        except (json.JSONDecodeError, OSError, KeyError) as e:
            gate = {"has_baseline": False, "error": str(e), "gate_pass": True}

    print(f"[held_out_suite] D_in  {summary['d_in']['passed']}/{summary['d_in']['n']} "
          f"({summary['d_in']['pass_rate']:.1%})")
    print(f"[held_out_suite] D_out {summary['d_out']['passed']}/{summary['d_out']['n']} "
          f"({summary['d_out']['pass_rate']:.1%})")
    print(f"[held_out_suite] accept={summary['accept']}")
    if gate and gate.get("has_baseline"):
        print(f"[held_out_suite] vs baseline: d_in_delta={gate['d_in_delta']:+.4f} "
              f"d_out_delta={gate['d_out_delta']:+.4f} gate_pass={gate['gate_pass']}")
    for split_name, split in (("D_in", summary["d_in"]), ("D_out", summary["d_out"])):
        for f in split["failures"][:12]:
            print(f"  FAIL {split_name} {f['id']}: {'; '.join(f['errors'])}")

    if args.json:
        print(json.dumps({"summary": summary, "gate": gate}, indent=2))

    if not args.dry_run:
        STATE.mkdir(parents=True, exist_ok=True)
        atomic_write_json(LAST_FILE, {"summary": summary, "gate": gate})
        report = write_report(summary, gate)
        print(f"[held_out_suite] report → {report}")
        append_jsonl(HISTORY, {
            "ts": summary["ts"],
            "d_in_rate": summary["d_in"]["pass_rate"],
            "d_out_rate": summary["d_out"]["pass_rate"],
            "accept": summary["accept"],
            "gate_pass": (gate or {}).get("gate_pass"),
        })
        if args.update_baseline or not BASELINE_FILE.exists():
            # First run auto-baselines if suite is clean; --update-baseline always freezes.
            if args.update_baseline or summary["accept"]:
                atomic_write_json(BASELINE_FILE, summary)
                print(f"[held_out_suite] baseline → {BASELINE_FILE}")
            else:
                print("[held_out_suite] baseline NOT updated (suite not clean; fix fixtures/evals first)")

    if args.gate:
        if not summary["accept"]:
            return 1
        if gate and gate.get("has_baseline") and not gate.get("gate_pass"):
            return 1
    return 0 if summary["accept"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
