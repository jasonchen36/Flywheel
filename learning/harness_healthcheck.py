#!/usr/bin/env python3
"""
harness_healthcheck.py — single dashboard for the self-improvement harness.

Reports (no mutations unless --fix flags used elsewhere):
  - effectiveness verdicts + escalate list
  - first_seen coverage on lessons
  - ratings skill/agent attribution rates
  - graph pending/archive, graph_preflight age
  - skill_autofix ledger
  - last held_out / agent_rollouts gate status
  - critical file presence

Usage:
  pyenv exec python3 harness_healthcheck.py
  pyenv exec python3 harness_healthcheck.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
LEARNING = HOME / ".claude/MEMORY/LEARNING"
STATE = HOME / ".claude/MEMORY/STATE"
MEM = HOME / ".claude/projects/-Users-jason-chen/memory"
SIGNALS = LEARNING / "SIGNALS"

sys.path.insert(0, str(LEARNING))
try:
    from self_improve import load_all_ratings, RATINGS_FILE
except Exception:
    load_all_ratings = None  # type: ignore
    RATINGS_FILE = SIGNALS / "ratings.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report: dict = {"ts": now_iso(), "checks": {}, "ok": True, "warnings": [], "errors": []}

    # Effectiveness
    scores = load_json(STATE / "effectiveness_scores.json") or {}
    sc = scores.get("scores") or {}
    escalate = scores.get("escalate") or []
    stale = scores.get("stale_pending") or [
        p for p, v in sc.items() if (v or {}).get("verdict") == "stale-pending"
    ]
    injectable = scores.get("injectable") or [
        p for p, v in sc.items() if (v or {}).get("injectable")
    ]
    vc = Counter((v or {}).get("verdict") for v in sc.values())
    report["checks"]["effectiveness"] = {
        "measured_at": scores.get("measured_at"),
        "n_patterns": len(sc),
        "verdicts": dict(vc),
        "escalate": escalate,
        "stale_pending_n": len(stale),
        "injectable_n": len(injectable),
        "pending_n": vc.get("pending", 0),
    }
    if vc.get("pending", 0) > 20 and len(injectable) < 5:
        report["warnings"].append(
            f"effectiveness stuck: {vc.get('pending', 0)} pending / "
            f"{len(stale)} stale-pending / only {len(injectable)} injectable real verdicts"
        )
    if "regressed" in vc and not escalate:
        # may be OK if only soft patterns — flag if UC regressed
        uc = sc.get("unverified_completion") or {}
        if uc.get("verdict") == "regressed":
            report["errors"].append(
                "unverified_completion is regressed but escalate[] is empty"
            )
            report["ok"] = False

    # first_seen / baseline_date coverage
    lessons = list(MEM.glob("lesson_autogen_*.md")) if MEM.exists() else []
    missing_fs = [f.name for f in lessons if "first_seen:" not in f.read_text()]
    missing_base = [f.name for f in lessons if "baseline_date:" not in f.read_text()]
    report["checks"]["lessons"] = {
        "n": len(lessons),
        "missing_first_seen": len(missing_fs),
        "missing_baseline_date": len(missing_base),
        "sample_missing": missing_fs[:5],
    }
    if missing_fs:
        report["warnings"].append(f"{len(missing_fs)} lessons missing first_seen")

    # Ratings attribution (primary skill + multi-label candidates)
    n_ratings = n_skill = n_skill_real = n_agent = n_multi = 0
    if load_all_ratings and Path(RATINGS_FILE).exists():
        ents = load_all_ratings(Path(RATINGS_FILE))
        n_ratings = len(ents)
        n_skill = sum(1 for e in ents if e.skill)
        n_skill_real = sum(
            1 for e in ents if e.skill and e.skill != "general-session"
        )
        n_agent = sum(1 for e in ents if getattr(e, "agent", ""))
        n_multi = sum(
            1 for e in ents
            if isinstance(getattr(e, "skill_candidates", None), list)
            and len(getattr(e, "skill_candidates") or []) > 1
        )
    skill_non_general_rate = (
        round(n_skill_real / n_ratings, 3) if n_ratings else 0
    )
    report["checks"]["ratings"] = {
        "n": n_ratings,
        "with_skill": n_skill,
        "with_skill_non_general": n_skill_real,
        "clean_with_skill_non_general_rate": skill_non_general_rate,
        "with_multi_skill_candidates": n_multi,
        "with_agent": n_agent,
        "skill_rate": round(n_skill / n_ratings, 3) if n_ratings else 0,
    }
    if n_ratings and n_skill == 0:
        report["warnings"].append(
            "zero skill attribution on ratings — skill_autofix cannot qualify skills"
        )
    if n_ratings and skill_non_general_rate < 0.30:
        report["warnings"].append(
            f"clean_with_skill_non_general rate {skill_non_general_rate:.1%} < 30% "
            "— everything dumping into general-session; path/Skill attribution broken"
        )

    # Graph
    pending = STATE / "graphiti_pending_episodes.jsonl"
    archive = STATE / "graphiti_flushed_archive.jsonl"
    preflight = STATE / "graph_preflight.md"
    pend_n = (
        len([l for l in pending.read_text().splitlines() if l.strip()])
        if pending.exists()
        else 0
    )
    arch_n = (
        len([l for l in archive.read_text().splitlines() if l.strip()])
        if archive.exists()
        else 0
    )
    report["checks"]["graph"] = {
        "pending_episodes": pend_n,
        "flushed_archive": arch_n,
        "preflight_exists": preflight.exists(),
        "preflight_bytes": preflight.stat().st_size if preflight.exists() else 0,
    }
    if pend_n > 10:
        report["warnings"].append(
            f"{pend_n} graphiti episodes still pending — flush may be failing"
        )
    if not preflight.exists():
        report["errors"].append("graph_preflight.md missing")
        report["ok"] = False

    # Enforcement config
    enc = load_json(STATE / "enforcement_config.json") or {}
    ov = enc.get("overrides") or {}
    report["checks"]["enforcement"] = {
        "enabled": enc.get("enabled", True),
        "graphiti_bypassed": ov.get("graphiti_bypassed"),
        "unverified_completion": ov.get("unverified_completion"),
        "unverified_claims": ov.get("unverified_claims"),
        "claim_evidence": ov.get("claim_evidence"),
        "silent_completion": ov.get("silent_completion"),
    }
    if ov.get("graphiti_bypassed") != "block":
        report["warnings"].append("graphiti_bypassed is not block")
    if ov.get("unverified_completion") != "block":
        report["warnings"].append("unverified_completion is not block")
    if ov.get("unverified_claims") != "block":
        report["warnings"].append(
            "unverified_claims is not block (promoted 2026-07-09 for anti-hallucination)"
        )
    if ov.get("claim_evidence") != "block":
        report["warnings"].append("claim_evidence is not block")
    if ov.get("silent_completion") != "block":
        report["warnings"].append(
            "silent_completion is not block (promoted 2026-07-09b)"
        )

    # Detector probe: bare metric must NOT look like strong completion evidence
    try:
        from evals import has_strong_artifact, score_text  # type: ignore

        bare = "Done. The table has 5000 rows and everything is complete."
        if has_strong_artifact(bare):
            report["errors"].append(
                "detector hole: bare '5000 rows' still counts as strong artifact"
            )
            report["ok"] = False
        scored = score_text(bare)
        if scored.get("completion_without_artifact", {}).get("passed") is not False:
            report["errors"].append(
                "detector hole: bare-metric completion did not fail completion_without_artifact"
            )
            report["ok"] = False
        report["checks"]["detector_probe"] = {
            "bare_metric_strong": has_strong_artifact(bare),
            "bare_metric_completion_pass": scored.get(
                "completion_without_artifact", {}
            ).get("passed"),
        }
    except Exception as e:
        report["warnings"].append(f"detector probe skipped: {e}")

    # anti_hallucination brief present
    ah = STATE / "anti_hallucination.md"
    report["checks"]["anti_hallucination_brief"] = {
        "exists": ah.exists(),
        "bytes": ah.stat().st_size if ah.exists() else 0,
    }
    if not ah.exists():
        report["warnings"].append("anti_hallucination.md missing")

    # skill_autofix
    led = load_json(STATE / "skill_autofix_ledger.json") or {}
    report["checks"]["skill_autofix"] = {
        "active_edits": sum(
            1 for e in led.get("edits", []) if e.get("status") == "active"
        ),
        "total_edits": len(led.get("edits", [])),
    }

    # Gates last run
    suite = load_json(STATE / "held_out_suite_last.json") or {}
    rolls = load_json(STATE / "agent_rollouts_last.json") or {}
    report["checks"]["gates"] = {
        "held_out_suite_gate_pass": (suite.get("gate") or {}).get("gate_pass"),
        "held_out_accept": suite.get("accept"),
        "agent_rollouts_gate_pass": (rolls.get("gate") or {}).get("gate_pass"),
        "agent_rollouts_pass_rate": (rolls.get("summary") or {}).get("pass_rate"),
    }

    # Critical paths
    critical = {
        "sync_graph_memory": LEARNING / "sync_graph_memory.py",
        "flush_graphiti_pending": LEARNING / "flush_graphiti_pending.py",
        "session_graphiti_autoseed": LEARNING / "session_graphiti_autoseed.py",
        "self_harness": LEARNING / "self_harness.py",
        "session_end": HOME / ".claude/hooks/claude-session-end",
        "enforcement_gate": HOME / ".claude/hooks/EnforcementGate.hook.ts",
        "pai_learning": HOME / ".pi/agent/extensions/pai-learning-harness.ts",
        "pai_enforcement": HOME / ".pi/agent/extensions/pai-enforcement-gate.ts",
    }
    missing = [k for k, p in critical.items() if not p.exists()]
    report["checks"]["files"] = {"missing": missing}
    if missing:
        report["errors"].append(f"missing files: {missing}")
        report["ok"] = False

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"# Harness healthcheck — {report['ts']}")
        print(f"OK: {report['ok']}")
        print(f"\nEffectiveness: {report['checks']['effectiveness']}")
        print(f"Lessons: {report['checks']['lessons']}")
        print(f"Ratings: {report['checks']['ratings']}")
        print(f"Graph: {report['checks']['graph']}")
        print(f"Enforcement: {report['checks']['enforcement']}")
        print(f"skill_autofix: {report['checks']['skill_autofix']}")
        print(f"Gates: {report['checks']['gates']}")
        print(f"Files missing: {missing or 'none'}")
        if report["warnings"]:
            print("\nWarnings:")
            for w in report["warnings"]:
                print(f"  - {w}")
        if report["errors"]:
            print("\nErrors:")
            for e in report["errors"]:
                print(f"  - {e}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
