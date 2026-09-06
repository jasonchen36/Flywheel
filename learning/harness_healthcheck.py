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
from typing import Any, Callable

from harness_config import load_enforcement_config
from harness_paths import DIAGNOSTICS, HARNESS_HOME, LEARNING, LESSONS_DIR, STATE, SIGNALS
from state_io import load_jsonl_objects
from state_io import try_read_json_object as load_json_object

MEM = LESSONS_DIR

sys.path.insert(0, str(LEARNING))
try:
    from self_improve import RATINGS_FILE, load_all_ratings
    rating_loader: Callable[[Path], list[Any]] = load_all_ratings
except Exception:
    RATINGS_FILE = SIGNALS / "ratings.jsonl"

    def rating_loader(_path: Path) -> list[Any]:
        return []


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report: dict = {"ts": now_iso(), "checks": {}, "ok": True, "warnings": [], "errors": []}

    def state_object(name: str) -> dict:
        data, error = load_json_object(STATE / name)
        if error:
            report["errors"].append(error)
            report["ok"] = False
        return data

    # Effectiveness
    scores = state_object("effectiveness_scores.json")
    sc_value = scores.get("scores") or {}
    sc: dict[str, dict[str, Any]] = {}
    if isinstance(sc_value, dict):
        invalid_score_rows = []
        for pattern, value in sc_value.items():
            if isinstance(pattern, str) and isinstance(value, dict):
                sc[pattern] = value
            else:
                invalid_score_rows.append(str(pattern))
        if invalid_score_rows:
            report["errors"].append(
                f"effectiveness score rows must be JSON objects: {invalid_score_rows}"
            )
            report["ok"] = False
    elif sc_value:
        report["errors"].append("effectiveness scores must be a JSON object")
        report["ok"] = False
    escalate_value = scores.get("escalate") or []
    escalate = escalate_value if isinstance(escalate_value, list) else []
    if escalate_value and not isinstance(escalate_value, list):
        report["errors"].append("effectiveness escalate must be a JSON list")
        report["ok"] = False
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
    if Path(RATINGS_FILE).exists():
        ents = rating_loader(Path(RATINGS_FILE))
        n_ratings = len(ents)
        n_skill = sum(1 for e in ents if e.skill)
        n_skill_real = sum(
            1 for e in ents if e.skill and e.skill != "general-session"
        )
        n_agent = sum(1 for e in ents if getattr(e, "agent", ""))
        n_multi = sum(
            1 for e in ents
            if isinstance(getattr(e, "skill_candidates", None), list)
            and len(e.skill_candidates or []) > 1
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

    # Signal freshness — when ratings stop flowing, every downstream verdict
    # (measure_effectiveness, burn-in, ACE) runs on stale data while the loop
    # still reports green. 2026-08-12: PAI_HAIKU_BACKGROUND_DISABLED=1 killed
    # sentiment capture for 27d and healthcheck said OK — never again.
    newest_rating = None
    rating_rows = load_jsonl_objects(Path(RATINGS_FILE))
    for row in rating_rows.records:
        timestamp = row.get("timestamp")
        if timestamp and (newest_rating is None or str(timestamp) > newest_rating):
            newest_rating = str(timestamp)
    rating_age_days = None
    if newest_rating:
        try:
            dt = datetime.fromisoformat(newest_rating.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            rating_age_days = (datetime.now(timezone.utc) - dt).days
        except ValueError:
            pass
    report["checks"]["signal_freshness"] = {
        "newest_rating": newest_rating,
        "rating_age_days": rating_age_days,
        "invalid_lines": list(rating_rows.invalid_lines),
    }
    if rating_rows.invalid_lines:
        report["warnings"].append(
            f"ratings contain malformed JSON object rows: {list(rating_rows.invalid_lines)}"
        )
    if n_ratings == 0:
        report["checks"]["signal_freshness"]["status"] = "no_ratings_yet"
    elif rating_age_days is None:
        report["errors"].append(
            "no parseable rating timestamps — capture sensor is dark"
        )
        report["ok"] = False
    elif rating_age_days > 5:
        report["errors"].append(
            f"RATINGS FLATLINE: newest rating is {rating_age_days}d old — "
            "verdicts, escalation and burn-in are spinning on stale data; "
            "check RatingCapture hook + PAI_*_DISABLED flags"
        )
        report["ok"] = False
    elif rating_age_days > 2:
        report["warnings"].append(
            f"newest rating is {rating_age_days}d old — capture may be degraded"
        )

    # Outcome judge queues and quarantine
    judge_pending = load_jsonl_objects(SIGNALS / "pending_judge.jsonl")
    judge_results = load_jsonl_objects(SIGNALS / "judge_results.jsonl")
    judge_invalid = load_jsonl_objects(SIGNALS / "invalid_judge.jsonl")
    report["checks"]["outcome_judge"] = {
        "pending": len(judge_pending.records),
        "results": len(judge_results.records),
        "quarantined": len(judge_invalid.records),
        "pending_invalid_lines": list(judge_pending.invalid_lines),
        "result_invalid_lines": list(judge_results.invalid_lines),
        "quarantine_invalid_lines": list(judge_invalid.invalid_lines),
    }
    malformed_judge_state = (
        judge_pending.invalid_lines
        or judge_results.invalid_lines
        or judge_invalid.invalid_lines
    )
    if malformed_judge_state:
        report["errors"].append(
            "outcome judge state has malformed JSON object rows: "
            f"pending={list(judge_pending.invalid_lines)} "
            f"results={list(judge_results.invalid_lines)} "
            f"quarantine={list(judge_invalid.invalid_lines)}"
        )
        report["ok"] = False
    if judge_invalid.records:
        report["warnings"].append(
            f"{len(judge_invalid.records)} quarantined outcome-judge turn(s) require inspection"
        )
    if len(judge_pending.records) > 50:
        report["warnings"].append(
            f"{len(judge_pending.records)} outcome-judge turns still pending — provider may be unavailable"
        )

    # Graph
    pending = STATE / "graphiti_pending_episodes.jsonl"
    archive = STATE / "graphiti_flushed_archive.jsonl"
    preflight = STATE / "graph_preflight.md"
    pending_rows = load_jsonl_objects(pending)
    archive_rows = load_jsonl_objects(archive)
    pend_n = len(pending_rows.records)
    arch_n = len(archive_rows.records)
    report["checks"]["graph"] = {
        "pending_episodes": pend_n,
        "flushed_archive": arch_n,
        "preflight_exists": preflight.exists(),
        "preflight_bytes": preflight.stat().st_size if preflight.exists() else 0,
        "pending_invalid_lines": list(pending_rows.invalid_lines),
        "archive_invalid_lines": list(archive_rows.invalid_lines),
    }
    if pending_rows.invalid_lines or archive_rows.invalid_lines:
        report["errors"].append(
            "graph state has malformed JSON object rows: "
            f"pending={list(pending_rows.invalid_lines)} "
            f"archive={list(archive_rows.invalid_lines)}"
        )
        report["ok"] = False
    if pend_n > 10:
        report["warnings"].append(
            f"{pend_n} graphiti episodes still pending — flush may be failing"
        )
    if not preflight.exists():
        report["errors"].append("graph_preflight.md missing")
        report["ok"] = False

    # Review queue state machine
    review_result = load_jsonl_objects(SIGNALS / "pending_human_review.jsonl")
    review_statuses = Counter(str(record.get("status") or "missing")
                              for record in review_result.records)
    known_review_statuses = {
        "pending", "processing", "action_failed", "approved", "rejected", "auto-escalated"
    }
    unknown_review_statuses = sorted(set(review_statuses) - known_review_statuses)
    failed_review_patterns = [
        str(record.get("pattern") or "?")
        for record in review_result.records
        if record.get("status") == "action_failed"
    ]
    processing_review_patterns = [
        str(record.get("pattern") or "?")
        for record in review_result.records
        if record.get("status") == "processing"
    ]
    report["checks"]["review_queue"] = {
        "counts": dict(sorted(review_statuses.items())),
        "invalid_lines": list(review_result.invalid_lines),
        "failed_patterns": failed_review_patterns,
        "processing_patterns": processing_review_patterns,
        "unknown_statuses": unknown_review_statuses,
    }
    if review_result.invalid_lines:
        report["errors"].append(
            f"review queue has malformed JSON object rows: {list(review_result.invalid_lines)}"
        )
        report["ok"] = False
    if unknown_review_statuses:
        report["errors"].append(
            f"review queue has unknown statuses: {unknown_review_statuses}"
        )
        report["ok"] = False
    if failed_review_patterns:
        report["warnings"].append(
            f"review actions failed and require --retry-failed: {failed_review_patterns}"
        )
    if processing_review_patterns:
        report["warnings"].append(
            f"review actions are still processing: {processing_review_patterns}"
        )

    # Enforcement config
    enforcement_result = load_enforcement_config(STATE / "enforcement_config.json")
    enc = enforcement_result.config
    ov = enc.overrides
    report["checks"]["enforcement"] = {
        "valid": enforcement_result.ok,
        "errors": list(enforcement_result.errors),
        "enabled": enc.enabled,
        "graphiti_bypassed": ov.get("graphiti_bypassed"),
        "unverified_completion": ov.get("unverified_completion"),
        "unverified_claims": ov.get("unverified_claims"),
        "claim_evidence": ov.get("claim_evidence"),
        "silent_completion": ov.get("silent_completion"),
    }
    if not enforcement_result.ok:
        report["errors"].extend(
            f"invalid enforcement_config.json: {error}"
            for error in enforcement_result.errors
        )
        report["ok"] = False
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
    except Exception as exc:
        report["warnings"].append(f"detector probe skipped: {exc}")

    # anti_hallucination brief present
    ah = STATE / "anti_hallucination.md"
    report["checks"]["anti_hallucination_brief"] = {
        "exists": ah.exists(),
        "bytes": ah.stat().st_size if ah.exists() else 0,
    }
    if not ah.exists():
        report["warnings"].append("anti_hallucination.md missing")

    # skill_autofix
    led = state_object("skill_autofix_ledger.json")
    edits_value = led.get("edits", [])
    edits = [edit for edit in edits_value if isinstance(edit, dict)] if isinstance(edits_value, list) else []
    if edits_value and (not isinstance(edits_value, list) or len(edits) != len(edits_value)):
        report["errors"].append("skill_autofix edits must be a JSON list of objects")
        report["ok"] = False
    invalid_edits_value = led.get("invalid_edits", [])
    invalid_edits = (
        [record for record in invalid_edits_value if isinstance(record, dict)]
        if isinstance(invalid_edits_value, list)
        else []
    )
    failure_states = {
        "rollback-failed",
        "reverted-audit-failed",
        "apply-audit-failed",
        "validation-failed-audit-failed",
        "invalid",
    }
    failed_edits = [
        f"/{edit.get('skill')}:{edit.get('pattern')} ({edit.get('status')})"
        for edit in edits
        if edit.get("status") in failure_states
    ]
    report["checks"]["skill_autofix"] = {
        "active_edits": sum(1 for edit in edits if edit.get("status") == "active"),
        "total_edits": len(edits),
        "failed_edits": failed_edits,
        "quarantined_edits": len(invalid_edits),
    }
    critical_autofix = [
        item
        for item in failed_edits
        if "(rollback-failed)" in item or "(invalid)" in item
    ]
    if critical_autofix:
        report["errors"].append(
            f"skill_autofix has {len(critical_autofix)} unresolved critical edits: {critical_autofix}"
        )
        report["ok"] = False
    audit_failures = [item for item in failed_edits if item not in critical_autofix]
    if audit_failures:
        report["warnings"].append(
            f"skill_autofix has {len(audit_failures)} audit-failed edits: {audit_failures}"
        )
    if invalid_edits:
        report["warnings"].append(
            f"skill_autofix quarantined {len(invalid_edits)} malformed ledger records"
        )
    # Burn-in stall: active edits that can never complete measurement because
    # no post-apply traffic exists for the skill (or the sensor is dark).
    today = datetime.now(timezone.utc).date()
    stalled = []
    for edit in edits:
        if edit.get("status") != "active":
            continue
        try:
            applied = datetime.strptime(str(edit.get("applied", ""))[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - applied).days
        if age > 14 and not edit.get("post_n"):
            stalled.append(f"/{edit.get('skill')}:{edit.get('pattern')} ({age}d)")
    report["checks"]["skill_autofix"]["burnin_stalled"] = stalled
    if stalled:
        report["warnings"].append(
            f"burn-in stalled {len(stalled)} edits (age>14d, post_n=0): {stalled} "
            "— run skill_burnin.py --resolve-stall --apply"
        )

    # Gates last run
    suite = state_object("held_out_suite_last.json")
    rolls = state_object("agent_rollouts_last.json")
    suite_summary_value = suite.get("summary")
    suite_summary = suite_summary_value if isinstance(suite_summary_value, dict) else suite
    suite_gate_value = suite.get("gate")
    suite_gate = suite_gate_value if isinstance(suite_gate_value, dict) else {}
    rolls_gate_value = rolls.get("gate")
    rolls_gate = rolls_gate_value if isinstance(rolls_gate_value, dict) else {}
    rolls_summary_value = rolls.get("summary")
    rolls_summary = rolls_summary_value if isinstance(rolls_summary_value, dict) else {}
    rollout_skipped = rolls_summary.get("skipped_all") is True
    report["checks"]["gates"] = {
        "held_out_suite_gate_pass": suite_gate.get("gate_pass"),
        "held_out_accept": suite_summary.get("accept"),
        "agent_rollouts_gate_pass": rolls_gate.get("gate_pass"),
        "agent_rollouts_pass_rate": rolls_summary.get("pass_rate"),
        "agent_rollouts_skipped_all": rollout_skipped,
        "agent_rollouts_baseline_error": rolls_gate.get("error"),
    }
    if (STATE / "held_out_suite_last.json").exists() and (
        suite_gate.get("gate_pass") is False or suite_summary.get("accept") is False
    ):
        report["errors"].append("held-out suite last run did not pass")
        report["ok"] = False
    if (STATE / "agent_rollouts_last.json").exists() and not rollout_skipped and (
        rolls_gate.get("gate_pass") is False or rolls_summary.get("accept") is False
    ):
        report["errors"].append("agent rollouts last run did not pass")
        report["ok"] = False
    if rolls_gate.get("error"):
        report["errors"].append(f"agent rollout baseline invalid: {rolls_gate['error']}")
        report["ok"] = False

    # ACE playbook and Self-Harness observability
    ace_path = STATE / "ace_playbook.json"
    ace = state_object("ace_playbook.json") if ace_path.exists() else {}
    raw_bullets = ace.get("bullets")
    bullets = [row for row in raw_bullets if isinstance(row, dict)] \
        if isinstance(raw_bullets, list) else []
    invalid_bullets = len(raw_bullets) - len(bullets) if isinstance(raw_bullets, list) else 0
    if ace_path.exists() and not isinstance(raw_bullets, list):
        report["errors"].append("ACE playbook bullets must be a JSON list")
        report["ok"] = False
    if invalid_bullets:
        report["errors"].append(f"ACE playbook contains {invalid_bullets} malformed bullets")
        report["ok"] = False
    active_sections = {"strategy", "pitfall", "formula"}
    active_bullets = sum(1 for row in bullets if row.get("section") in active_sections)
    weak_output = (ace.get("stats") or {}).get("weak_output") if isinstance(ace.get("stats"), dict) else None
    report["checks"]["ace_playbook"] = {
        "present": ace_path.exists(),
        "generated_at": ace.get("generated_at"),
        "bullet_count": len(bullets),
        "active_bullets": active_bullets,
        "invalid_bullets": invalid_bullets,
        "weak_output": weak_output,
    }
    if isinstance(weak_output, int) and weak_output > 0:
        report["warnings"].append(f"ACE playbook reports {weak_output} weak output bullets")

    harness_latest = DIAGNOSTICS / "self_harness_latest.json"
    harness_data, harness_error = load_json_object(harness_latest)
    if harness_error and harness_latest.exists():
        report["errors"].append(harness_error)
        report["ok"] = False
    stages_value = harness_data.get("stages")
    harness_stages = stages_value if isinstance(stages_value, dict) else {}
    validate_value = harness_stages.get("validate")
    harness_validate = validate_value if isinstance(validate_value, dict) else {}
    unreadable_value = harness_validate.get("unreadable_lessons")
    unreadable_lessons = unreadable_value if isinstance(unreadable_value, list) else []
    outcome_value = harness_data.get("outcome")
    harness_outcome = outcome_value if isinstance(outcome_value, dict) else {}
    outcome_status = harness_outcome.get("status")
    report["checks"]["self_harness"] = {
        "present": harness_latest.exists(),
        "timestamp": harness_data.get("ts"),
        "stages": sorted(str(name) for name in harness_stages),
        "unreadable_lessons": unreadable_lessons,
        "outcome_status": outcome_status,
        "gate_error": harness_outcome.get("gate_error"),
    }
    if unreadable_lessons:
        report["warnings"].append(
            f"self-harness could not read {len(unreadable_lessons)} lessons: {unreadable_lessons[:5]}"
        )
    if outcome_status in {"rejected", "apply_failed"}:
        report["errors"].append(
            f"self-harness last cycle {outcome_status}: {harness_outcome.get('gate_error') or 'inspect logs'}"
        )
        report["ok"] = False
    elif outcome_status not in {None, "reported", "accepted", "applied"}:
        report["errors"].append(f"self-harness outcome status is invalid: {outcome_status}")
        report["ok"] = False

    # Critical paths
    HOOKS = HARNESS_HOME / "hooks"
    critical = {
        "sync_graph_memory": LEARNING / "sync_graph_memory.py",
        "flush_graphiti_pending": LEARNING / "flush_graphiti_pending.py",
        "session_graphiti_autoseed": LEARNING / "session_graphiti_autoseed.py",
        "self_harness": LEARNING / "self_harness.py",
        "ace_reflector": LEARNING / "ace_reflector.py",
        "ace_playbook": LEARNING / "ace_playbook.py",
        "agent_rollouts": LEARNING / "agent_rollouts.py",
        "consolidate_memory": LEARNING / "consolidate_memory.py",
        "session_end": HOOKS / "harness-session-end.sh",
        "enforcement_gate": HOOKS / "EnforcementGate.hook.ts",
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
        print(f"Signal freshness: {report['checks']['signal_freshness']}")
        print(f"Outcome judge: {report['checks']['outcome_judge']}")
        print(f"Graph: {report['checks']['graph']}")
        print(f"Enforcement: {report['checks']['enforcement']}")
        print(f"Review queue: {report['checks']['review_queue']}")
        print(f"skill_autofix: {report['checks']['skill_autofix']}")
        print(f"Gates: {report['checks']['gates']}")
        print(f"ACE playbook: {report['checks']['ace_playbook']}")
        print(f"Self-Harness: {report['checks']['self_harness']}")
        print(f"Files missing: {missing or 'none'}")
        if report["warnings"]:
            print("\nWarnings:")
            for w in report["warnings"]:
                print(f"  - {w}")
        if report["errors"]:
            print("\nErrors:")
            for error in report["errors"]:
                print(f"  - {error}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
