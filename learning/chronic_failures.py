#!/usr/bin/env python3
"""chronic_failures.py — recidivism breaker for ladder-top patterns.

The escalation ladder tops out at "queue human review" — and the queue is a
write-only graveyard: the same patterns cycle approved -> auto-escalated ->
pending for months while Stop-time blocks keep firing AFTER the failure.

This script closes the circuit:
  1. Detects CHRONIC patterns: verdict=regressed AND enforcement=block AND
     queue audit count >= CHRONIC_MIN (the loop has tried and failed repeatedly).
  2. Emits an intervention-rotation report: which intervention classes have
     been tried per pattern, and which untried class is next. Reactive block
     at Stop-time has failed for these; the untried class is PROACTIVE priming
     at session/prompt time.
  3. Writes a compact injectable brief (DIAGNOSTICS/chronic_failures_latest.md)
     that SessionStart/UserPromptSubmit surfaces can load.
  4. Appends a snapshot to SIGNALS/chronic_failures.jsonl so consecutive
     ladder-top hits (top_hits) are tracked as a trend, not re-discovered.

Writes only to allowlisted surfaces (DIAGNOSTICS report, SIGNALS append-only).

Usage:
  pyenv exec python3 chronic_failures.py            # report only
  pyenv exec python3 chronic_failures.py --json
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_paths import LEARNING, STATE, SIGNALS  # noqa: E402

DIAG = LEARNING / "DIAGNOSTICS"
REVIEW_FILE = SIGNALS / "pending_human_review.jsonl"
AUDIT_FILE = SIGNALS / "review_audit.jsonl"
SNAPSHOT_FILE = SIGNALS / "chronic_failures.jsonl"

CHRONIC_MIN = 5  # queue/audit entries before a blocked+regressed pattern is chronic

# Intervention classes the loop can rotate through, in escalating order.
# A chronic pattern by definition exhausted 1-4; the report names what is left.
INTERVENTION_CLASSES = [
    "lesson",             # 1. autogen lesson written + approved
    "ace_bullet",         # 2. ACE playbook bullet (quality-boosted)
    "enforcement_block",  # 3. Stop-time block via EnforcementGate
    "skill_guardrail",    # 4. skill_autofix bounded edit
    "session_priming",    # 5. proactive inject at SessionStart/prompt time
    "human_pairing",      # 6. human revises lesson/ACE by hand (ladder top)
]


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    eff = _load_json(STATE / "effectiveness_scores.json")
    scores = eff.get("scores") or {}
    enc = _load_json(STATE / "enforcement_config.json")
    overrides = enc.get("overrides") or {}
    ace = _load_json(STATE / "ace_playbook.json")
    bullets = ace.get("bullets") or []
    ledger = _load_json(STATE / "skill_autofix_ledger.json")
    edits = ledger.get("edits") or []
    audit = _jsonl(AUDIT_FILE)
    pending = _jsonl(REVIEW_FILE)
    snapshots = _jsonl(SNAPSHOT_FILE)

    audit_n = Counter(r.get("pattern") for r in audit if r.get("pattern"))
    pend_n = Counter(
        r.get("pattern") for r in pending if r.get("status") == "pending"
    )
    last_snap = {}
    for s in snapshots:
        if s.get("pattern"):
            last_snap[s["pattern"]] = s

    regressed = sorted(
        k for k, v in scores.items() if (v or {}).get("verdict") == "regressed"
    )
    rows = []
    for pat in regressed:
        blocked = overrides.get(pat) == "block"
        n_audit = audit_n.get(pat, 0)
        chronic = blocked and n_audit >= CHRONIC_MIN
        pat_edits = [e for e in edits if e.get("pattern") == pat]
        pat_bullets = [b for b in bullets if b.get("pattern") == pat]
        tried = set()
        if n_audit:
            tried.add("lesson")
        if pat_bullets:
            tried.add("ace_bullet")
        if blocked:
            tried.add("enforcement_block")
        if pat_edits:
            tried.add("skill_guardrail")
        if pend_n.get(pat):
            tried.add("human_pairing")  # already queued for a human
        untried = [c for c in INTERVENTION_CLASSES if c not in tried]
        prev = last_snap.get(pat) or {}
        top_hits = (prev.get("top_hits") or 0) + 1 if chronic else 0
        rows.append({
            "pattern": pat,
            "chronic": chronic,
            "blocked": blocked,
            "audit_entries": n_audit,
            "pending_entries": pend_n.get(pat, 0),
            "skill_edits": [
                f"/{e.get('skill')}:{e.get('status')}" for e in pat_edits
            ],
            "ace_quality": max(
                (int(b.get("quality") or 0) for b in pat_bullets), default=None
            ),
            "untried": untried,
            "next_intervention": untried[0] if untried else "human_pairing (repeat)",
            "top_hits": top_hits,
        })

    lines = [
        f"# Chronic failures — {today}",
        "",
        "Patterns regressed under block-mode enforcement with >=5 queue cycles.",
        "Stop-time blocks fire AFTER the failure; rotate to the next untried",
        "intervention class instead of re-queueing another lesson.",
        "",
    ]
    chronic_rows = [r for r in rows if r["chronic"]]
    if not chronic_rows:
        lines.append("No chronic patterns.")
    for r in chronic_rows:
        lines.append(
            f"## {r['pattern']} (top_hits={r['top_hits']}, audit={r['audit_entries']})"
        )
        lines.append(f"- skill edits: {r['skill_edits'] or 'none'}")
        lines.append(f"- ACE quality: {r['ace_quality']}")
        lines.append(f"- next intervention: **{r['next_intervention']}**")
        lines.append("")
    if chronic_rows:
        lines += [
            "## Session-priming checklist (inject at prompt time)",
            "",
        ]
        for r in chronic_rows:
            lines.append(f"- BEFORE finishing: verify no {r['pattern']} — state evidence or say unverified")

    report_md = "\n".join(lines) + "\n"
    DIAG.mkdir(parents=True, exist_ok=True)
    (DIAG / f"chronic_failures_{today}.md").write_text(report_md)
    (DIAG / "chronic_failures_latest.md").write_text(report_md)
    with SNAPSHOT_FILE.open("a") as f:
        for r in rows:
            if r["chronic"]:
                f.write(json.dumps({
                    "date": today, "pattern": r["pattern"],
                    "top_hits": r["top_hits"], "audit_entries": r["audit_entries"],
                }) + "\n")

    if args.json:
        print(json.dumps({"date": today, "regressed": rows}, indent=2))
    else:
        print(report_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
