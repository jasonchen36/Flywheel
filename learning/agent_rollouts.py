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
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import HARNESS_HOME
from state_io import (
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
    try_read_json_object,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evals import score_text  # noqa: E402
from self_improve import call_llm  # noqa: E402

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "held_out_suite" / "fixtures" / "agent_rollouts.json"
ACE = HARNESS_HOME / "MEMORY/STATE/ace_playbook.json"
STATE = HARNESS_HOME / "MEMORY/STATE"
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
LAST = STATE / "agent_rollouts_last.json"
BASELINE = STATE / "agent_rollouts_baseline.json"
HISTORY = HARNESS_HOME / "MEMORY/LEARNING/SIGNALS" / "agent_rollouts_history.jsonl"
TRANSCRIPTS = DIAG / "agent_rollout_transcripts"
SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
VALID_SPLITS = frozenset({"in", "out"})


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_rate(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return default
    try:
        rate = float(value)
    except (OverflowError, ValueError):
        return default
    return rate if math.isfinite(rate) and 0.0 <= rate <= 1.0 else default


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return value


def normalize_scenario(value: object) -> tuple[dict | None, str | None]:
    if not isinstance(value, dict):
        return None, "scenario must be a JSON object"
    scenario_id = value.get("id")
    if not isinstance(scenario_id, str) or not SCENARIO_ID_RE.fullmatch(scenario_id):
        return None, "scenario id must be a safe lowercase identifier"
    split = value.get("split")
    if split not in VALID_SPLITS:
        return None, f"scenario {scenario_id}: split must be 'in' or 'out'"
    normalized: dict = {"id": scenario_id, "split": split}
    for field, default in (
        ("domain", ""),
        ("pattern", ""),
        ("system_role", "You are a careful coding agent."),
        ("user", ""),
    ):
        raw = value.get(field, default)
        if not isinstance(raw, str):
            return None, f"scenario {scenario_id}: {field} must be a string"
        normalized[field] = raw
    for field in ("constraints", "must_not_match", "must_match_any"):
        rows = _string_list(value.get(field, []))
        if rows is None:
            return None, f"scenario {scenario_id}: {field} must be a string list"
        normalized[field] = rows
    raw_expect = value.get("eval_expect", {})
    if not isinstance(raw_expect, dict):
        return None, f"scenario {scenario_id}: eval_expect must be an object"
    expected: dict[str, dict[str, bool | None]] = {}
    for eval_id, expectation in raw_expect.items():
        if not isinstance(eval_id, str) or not isinstance(expectation, dict):
            return None, f"scenario {scenario_id}: invalid eval expectation"
        row: dict[str, bool | None] = {}
        if "applied" in expectation:
            if not isinstance(expectation["applied"], bool):
                return None, f"scenario {scenario_id}: applied must be boolean"
            row["applied"] = expectation["applied"]
        if "passed" in expectation:
            passed = expectation["passed"]
            if passed is not None and not isinstance(passed, bool):
                return None, f"scenario {scenario_id}: passed must be boolean or null"
            row["passed"] = passed
        expected[eval_id] = row
    normalized["eval_expect"] = expected
    return normalized, None


def load_scenarios(path: Path = SCENARIOS) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], [f"scenario fixture unreadable: {path} does not exist"]
    data, error = try_read_json_object(path)
    if error:
        return [], [f"scenario fixture unreadable: {error}"]
    raw = data.get("scenarios")
    if not isinstance(raw, list):
        return [], ["scenario fixture field 'scenarios' must be a list"]
    scenarios: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        scenario, issue = normalize_scenario(value)
        if issue:
            errors.append(f"row {index}: {issue}")
            continue
        assert scenario is not None
        scenario_id = scenario["id"]
        if scenario_id in seen:
            errors.append(f"row {index}: duplicate scenario id {scenario_id}")
            continue
        seen.add(scenario_id)
        scenarios.append(scenario)
    if not scenarios:
        errors.append("scenario fixture contains no valid scenarios")
    return scenarios, errors


def load_playbook_bullets(max_n: int = 8) -> str:
    if max_n <= 0:
        return "(empty playbook)"
    if not ACE.exists():
        return "(no ACE playbook yet)"
    data, error = try_read_json_object(ACE)
    if error:
        return "(ace_playbook unreadable)"
    raw_bullets = data.get("bullets")
    if not isinstance(raw_bullets, list):
        return "(ace_playbook unreadable)"
    lines: list[str] = []
    for value in raw_bullets:
        if not isinstance(value, dict):
            continue
        section = value.get("section")
        if section in ("resolved", "deferred"):
            continue
        pattern = value.get("pattern")
        description = value.get("description")
        if not isinstance(pattern, str) or not pattern or not isinstance(description, str):
            continue
        quality = value.get("quality")
        qtag = f" q{quality}" if isinstance(quality, int) and not isinstance(quality, bool) else ""
        lines.append(f"- [{pattern}]{qtag} {description[:180]}")
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
    try:
        response = call_llm(prompt, max_tokens=700) or ""
    except Exception as exc:
        return {
            "id": scenario["id"],
            "split": scenario.get("split"),
            "ok": None,
            "skipped": True,
            "errors": [f"LLM unavailable: {type(exc).__name__}: {exc}"],
            "response": "",
        }
    if not response.strip():
        return {
            "id": scenario["id"],
            "split": scenario.get("split"),
            "ok": None,
            "skipped": True,
            "errors": ["LLM unavailable or empty response"],
            "response": "",
        }
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
    for _split, d in by_split.items():
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
    current_rate = safe_rate(current.get("pass_rate"))
    current_in = safe_rate((current.get("d_in") or {}).get("pass_rate"))
    current_out = safe_rate((current.get("d_out") or {}).get("pass_rate"))
    baseline_rate = safe_rate(baseline.get("pass_rate"))
    baseline_in = safe_rate((baseline.get("d_in") or {}).get("pass_rate"))
    baseline_out = safe_rate((baseline.get("d_out") or {}).get("pass_rate"))
    return {
        "has_baseline": True,
        "pass_rate_delta": round(current_rate - baseline_rate, 4),
        "d_in_delta": round(current_in - baseline_in, 4),
        "d_out_delta": round(current_out - baseline_out, 4),
        "d_out_regressed": current_out < baseline_out,
        "d_in_regressed": current_in < baseline_in,
        "baseline_ts": baseline.get("ts") if isinstance(baseline.get("ts"), str) else None,
        "gate_pass": current_rate >= baseline_rate and current_out >= baseline_out,
    }


def load_baseline(path: Path = BASELINE) -> tuple[dict | None, str | None]:
    data, error = try_read_json_object(path)
    if error:
        return None, error
    required = ("pass_rate", "d_in", "d_out")
    if any(field not in data for field in required):
        return None, "baseline is missing required rate fields"
    if not isinstance(data.get("d_in"), dict) or not isinstance(data.get("d_out"), dict):
        return None, "baseline split summaries must be objects"
    for value in (
        data.get("pass_rate"),
        data["d_in"].get("pass_rate"),
        data["d_out"].get("pass_rate"),
    ):
        if safe_rate(value, -1.0) < 0:
            return None, "baseline contains an invalid pass rate"
    return data, None


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
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent-executed PR-review rollouts")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--force-baseline", action="store_true",
                    help="allow updating a non-clean baseline after explicit review")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="no state writes")
    ap.add_argument("--min-pass-rate", type=float, default=0.75,
                    help="absolute floor for accept when no baseline (default 0.75)")
    args = ap.parse_args(argv)
    if args.limit < 0:
        print("[agent_rollouts] --limit must be non-negative")
        return 2
    if not 0.0 <= args.min_pass_rate <= 1.0 or not math.isfinite(args.min_pass_rate):
        print("[agent_rollouts] --min-pass-rate must be between 0 and 1")
        return 2
    if args.force_baseline and not args.update_baseline:
        print("[agent_rollouts] --force-baseline requires --update-baseline")
        return 2

    scenarios, scenario_errors = load_scenarios(SCENARIOS)
    if scenario_errors:
        for error in scenario_errors:
            print(f"[agent_rollouts] invalid fixture: {error}")
        return 2
    if args.limit > 0:
        scenarios = scenarios[: args.limit]

    playbook = load_playbook_bullets()
    results = []
    for sc in scenarios:
        rec = run_scenario(sc, playbook, use_llm=not args.no_llm)
        results.append(rec)
        if not args.dry_run and rec.get("response"):
            atomic_write_text(TRANSCRIPTS / f"{rec['id']}.txt", rec["response"])
        flag = "SKIP" if rec.get("skipped") else ("PASS" if rec.get("ok") else "FAIL")
        print(f"  [{flag}] {rec['id']}"
              + (f" — {'; '.join(rec.get('errors') or [])}" if rec.get("errors") else ""))

    summary = aggregate(results)
    gate = None
    if BASELINE.exists():
        baseline, baseline_error = load_baseline(BASELINE)
        if baseline_error:
            gate = {"has_baseline": True, "error": baseline_error, "gate_pass": False}
        else:
            assert baseline is not None
            gate = compare_baseline(summary, baseline)

    print(f"[agent_rollouts] {summary['passed']}/{summary['n']} "
          f"({summary['pass_rate']:.1%}) accept={summary['accept']} "
          f"d_in={summary.get('d_in')} d_out={summary.get('d_out')}")
    if gate and gate.get("has_baseline"):
        print(f"[agent_rollouts] vs baseline gate_pass={gate.get('gate_pass')} "
              f"d_out_delta={gate.get('d_out_delta')}")

    if not args.dry_run:
        STATE.mkdir(parents=True, exist_ok=True)
        atomic_write_json(LAST, {"summary": summary, "gate": gate, "results": [
            {k: v for k, v in r.items() if k != "response"} for r in results
        ]})
        report = write_report(summary, results, gate)
        print(f"[agent_rollouts] report → {report}")
        if not summary.get("skipped_all"):
            append_jsonl(HISTORY, {
                "ts": summary["ts"],
                "pass_rate": summary["pass_rate"],
                "d_in": summary.get("d_in"),
                "d_out": summary.get("d_out"),
                "accept": summary["accept"],
                "gate_pass": (gate or {}).get("gate_pass"),
            })
            should_update = args.update_baseline and (summary["accept"] or args.force_baseline)
            should_initialize = not BASELINE.exists() and summary["accept"]
            if should_update or should_initialize:
                atomic_write_json(BASELINE, summary)
                qualifier = " FORCE" if args.force_baseline and not summary["accept"] else ""
                print(f"[agent_rollouts] baseline{qualifier} → {BASELINE}")
            elif args.update_baseline and not summary["accept"]:
                print("[agent_rollouts] baseline NOT updated: suite is not clean; use --force-baseline after review")

    if summary.get("skipped_all"):
        print("[agent_rollouts] all skipped (--no-llm or LLM unavailable)")
        # Soft: no LLM is infrastructure unavailability, not harness regression.
        return 0

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


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
