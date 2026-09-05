#!/usr/bin/env python3
"""
sync_graph_memory.py — keep Graphiti + Bungraph current with harness state.

Problems this fixes (2026-07-09 audit):
  1. measure_effectiveness wrote bungraph triplets via fire-and-forget bunx without
     BUNGRAPH_DB_PATH → wrong DB / silent no-op; stale HAS_VERDICT edges.
  2. No automatic write of harness architecture / regressed lessons into Graphiti.
  3. Agents under-use graph tools because graphs lacked fresh harness facts.

Design: stay FAST for SessionEnd (<5s). Graphiti gets a durable pending queue file;
bungraph triplets/episodes are fire-and-forget with correct BUNGRAPH_DB_PATH.
Full LLM episode extraction happens via MCP when agents call add_memory / add_episode.

Usage:
  pyenv exec python3 sync_graph_memory.py
  pyenv exec python3 sync_graph_memory.py --dry-run
  pyenv exec python3 sync_graph_memory.py --wait   # block on bungraph CLI (slow)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from harness_paths import BUNGRAPH_DB, DIAGNOSTICS, STATE
from state_io import append_jsonl, atomic_write_json, atomic_write_text, try_read_json_object

SCORES = STATE / "effectiveness_scores.json"
ACE = STATE / "ace_playbook.json"
PENDING_GRAPHITI = STATE / "graphiti_pending_episodes.jsonl"
GRAPH_PREFLIGHT = STATE / "graph_preflight.md"
DIAG = DIAGNOSTICS


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_scores() -> dict:
    data, _error = try_read_json_object(SCORES)
    scores = data.get("scores")
    escalate = data.get("escalate")
    return {
        **data,
        "scores": {
            str(pattern): value
            for pattern, value in scores.items()
            if isinstance(value, dict)
        } if isinstance(scores, dict) else {},
        "escalate": [str(pattern) for pattern in escalate if isinstance(pattern, str)]
        if isinstance(escalate, list) else [],
    }


def load_ace_top(n: int = 5) -> list[dict]:
    data, _error = try_read_json_object(ACE)
    bullets = data.get("bullets")
    if not isinstance(bullets, list):
        return []
    return [bullet for bullet in bullets if isinstance(bullet, dict)][:n]


def build_status_episode(scores: dict, escalate: list, ace: list[dict]) -> str:
    sc = scores.get("scores") or {}
    lines = [
        f"PAI harness graph sync as of {today()}.",
        f"Measured patterns: {len(sc)}. Escalate: {escalate or []}.",
        "Active non-pending verdicts:",
    ]
    for p, s in sorted(
        sc.items(),
        key=lambda kv: {
            "regressed": 0,
            "flat": 1,
            "improving": 2,
            "working": 3,
            "resolved": 4,
        }.get(kv[1].get("verdict", ""), 9),
    ):
        v = s.get("verdict")
        if v in (None, "pending", "no-baseline", "undated"):
            continue
        lines.append(
            f"- {p}: subj={v} Δ={s.get('delta')} obj={s.get('obj_verdict')} "
            f"after_n={s.get('after_n')}"
        )
    if ace:
        lines.append("Top ACE bullets:")
        for b in ace:
            lines.append(
                f"- [{b.get('pattern')}/{b.get('verdict')}] "
                f"{(b.get('description') or '')[:160]}"
            )
    lines.append(
        "Retrieval rule: agents must search graphiti-memory (group main) or bungraph "
        "before broad manual research. Completion claims need STRONG paper traces "
        "(fenced CLI/test output, pass counts, exit codes, or live URL) — bare paths fail."
    )
    return "\n".join(lines)


def queue_graphiti_episode(name: str, body: str, dry: bool) -> None:
    rec = {
        "ts": now_iso(),
        "name": name,
        "episode_body": body,
        "source": "text",
        "source_description": f"sync_graph_memory {today()}",
        "group_id": "main",
        "status": "pending",
    }
    if dry:
        print(f"[dry-run] queue graphiti episode {name!r} ({len(body)} chars)")
        return
    append_jsonl(PENDING_GRAPHITI, rec)


def spawn_bungraph(args: list[str], dry: bool, wait: bool) -> bool:
    if dry:
        print(f"[dry-run] bungraph {' '.join(args[:4])}...")
        return False
    env = {**os.environ, "BUNGRAPH_DB_PATH": str(BUNGRAPH_DB)}
    try:
        if wait:
            result = subprocess.run(
                ["bunx", "bungraph", *args],
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode != 0:
                print(f"[bungraph] command failed ({result.returncode}): {(result.stderr or '')[-300:]}")
                return False
        else:
            subprocess.Popen(
                ["bunx", "bungraph", *args],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True
    except Exception as exc:
        print(f"[bungraph] spawn failed: {exc}")
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--wait",
        action="store_true",
        help="block on bungraph CLI (slow; default fire-and-forget)",
    )
    args = ap.parse_args(argv)
    dry = args.dry_run
    wait = args.wait

    data = load_scores()
    sc_map = data.get("scores") or {}
    escalate = list(data.get("escalate") or [])
    # Dual-signal safety: ensure subj-regressed enforceable patterns are synced
    for p, s in sc_map.items():
        if (
            s.get("verdict") == "regressed"
            and (s.get("after_n") or 0) >= 5
            and p not in escalate
        ):
            escalate.append(p)

    ace = load_ace_top(5)
    d = today()

    # Cap: escalate + other regressed/flat, max 8 triplets
    priority: list[str] = list(escalate)
    for pattern, s in sc_map.items():
        v = s.get("verdict")
        if v in ("regressed", "flat") and (s.get("after_n") or 0) >= 5:
            if pattern not in priority:
                priority.append(pattern)
    priority = priority[:8]

    n_trip = 0
    for pattern in priority:
        s = sc_map.get(pattern) or {}
        v = s.get("verdict")
        if v in (None, "pending", "no-baseline", "undated"):
            continue
        delta = s.get("delta", 0.0) or 0.0
        fact = (
            f"Behavioral pattern '{pattern}' has verdict '{v}' "
            f"(delta: {delta:+.4f}) as of {d} based on objective/subjective "
            f"session evaluations. obj={s.get('obj_verdict')} after_n={s.get('after_n')}."
        )
        spawned = spawn_bungraph(
            [
                "triplet",
                f"lesson_{pattern}",
                "HAS_VERDICT",
                str(v),
                "--fact",
                fact,
            ],
            dry,
            wait,
        )
        if spawned:
            n_trip += 1

    body = build_status_episode(data, escalate, ace)
    name = f"harness-status-{d}"
    # Graphiti pending queue (always, durable, fast)
    queue_graphiti_episode(name, body, dry)
    for pattern in escalate[:5]:
        s = sc_map.get(pattern) or {}
        reg_body = (
            f"ESCALATED pattern '{pattern}' as of {d}: subj={s.get('verdict')} "
            f"Δ={s.get('delta')} obj={s.get('obj_verdict')}. "
            f"Apply lesson_autogen_{pattern}.md + EnforcementGate. "
            f"Completion needs STRONG paper traces (not bare paths)."
        )
        queue_graphiti_episode(f"escalated-{pattern}-{d}", reg_body, dry)

    # One bungraph episode (fire-and-forget unless --wait)
    episode_spawned = spawn_bungraph(
        ["add", body, "--name", name, "--source", "text"],
        dry,
        wait,
    )
    n_ep = int(episode_spawned)

    # Refresh SessionStart preflight (LoadContext + pai-learning-harness)
    preflight_lines = [
        "# Graph memory preflight (auto-loaded SessionStart)",
        "",
        "MANDATORY before broad research or proposing architecture/data changes:",
        "",
        "1. Query **graphiti-memory** (`search_memory_facts` / `search_nodes`) "
        "OR **bungraph** (`search` / `search_facts`) first.",
        "2. Use retrieved facts; if empty, say so — then proceed with tools.",
        "3. Write durable findings back (`add_memory` / `add_episode` / `add_triplet`) "
        "when state changes.",
        "4. Skip only for pure local edits with no discovery "
        "(single-file fix already in context).",
        "",
        "Enforcement:",
        "- `graphiti_bypassed` = **block** if ≥2 research tools without graphiti/bungraph **read**",
        "- `graphiti_writeback_skipped` = **warn** if research+durable claims without **write**",
        "- SessionEnd runs `session_graphiti_autoseed.py` as write-back safety net",
        "",
        f"**Synced:** {d}",
        f"**Escalate:** {escalate or []}",
        "",
        "### Harness snapshot",
        body[:2500],
        "",
        f"*Refreshed by sync_graph_memory.py at {now_iso()}.*",
        "",
    ]
    if not dry:
        atomic_write_text(GRAPH_PREFLIGHT, "\n".join(preflight_lines))

    report = {
        "ts": now_iso(),
        "triplets_spawned": n_trip,
        "episodes_spawned": n_ep,
        "escalate": escalate,
        "priority": priority,
        "n_scored": len(sc_map),
        "pending_graphiti": str(PENDING_GRAPHITI),
        "graph_preflight": str(GRAPH_PREFLIGHT),
        "bungraph_db": str(BUNGRAPH_DB),
        "wait": wait,
    }
    if not dry:
        atomic_write_json(DIAG / f"sync_graph_memory_{d}.json", report)
    print(json.dumps(report, indent=2))

    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
