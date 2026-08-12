#!/usr/bin/env python3
"""harness_changelog.py — make the self-improvement loop's work visible.

DoorDash Flux lesson: "Make the work visible. Moving work into public threads
changed the adoption pattern... Engineers could see what others delegated,
watch progress, review output, and build trust together."

For a personal harness, visibility means: at any moment you can see WHAT the
loop mutated, WHEN, and WHY, plus how to undo it. This script is read-only: it
snapshots per-file mtimes under the LEARNING tree and diffs against the last
snapshot, then emits a dated changelog into STATE/harness_changelog.md.

Run:  python3 harness_changelog.py            (write changelog + new snapshot)
      python3 harness_changelog.py --dry-run  (show what changed, no writes)

Additive only: does not edit denied paths (hooks/, settings.json,
review_queue.py, CLAUDE.md, editable_surfaces.json). It only READS those.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "STATE")
SNAPSHOT = os.path.join(STATE, "harness_changelog_last.json")
CHANGELOG = os.path.join(STATE, "harness_changelog.md")
MAX_FILES = 60  # cap changelog entries to keep the digest readable

# File classes the loop mutates, with human labels for the digest.
CLASSES = [
    ("lessons", "lesson_autogen_*.md"),
    ("ledgers", "*.jsonl"),
    ("state", "STATE/*.json"),
    ("playbook", "STATE/ace_playbook.*"),
    ("fixtures", "held_out_suite/fixtures/**"),
    ("diagnostics", "DIAGNOSTICS/**"),
    ("scripts", "*.py"),
    ("skills", "commands/*.md"),
]


def snapshot():
    """Return {relpath: (mtime_epoch, size)} for tracked files under BASE."""
    snap = {}
    for root, dirs, files in os.walk(BASE):
        # Skip heavyweight/volatile dirs that are not harness mutations.
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "venv", ".venv")]
        for name in files:
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, BASE)
            snap[rel] = (st.st_mtime, st.st_size)
    return snap


def classify(path):
    if path.startswith("lesson_autogen"):
        return "lessons"
    if path.endswith(".jsonl"):
        return "ledgers"
    if path.startswith("STATE/"):
        return "state"
    if path.startswith("held_out_suite/fixtures"):
        return "fixtures"
    if path.startswith("DIAGNOSTICS/"):
        return "diagnostics"
    if path.endswith(".py"):
        return "scripts"
    if path.startswith("commands/"):
        return "skills"
    return "other"


def load_prev():
    if not os.path.exists(SNAPSHOT):
        return None
    try:
        with open(SNAPSHOT) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = ap.parse_args()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = snapshot()
    prev = load_prev()
    if prev is None:
        # First run: baseline only.
        if not args.dry_run:
            os.makedirs(STATE, exist_ok=True)
            with open(SNAPSHOT, "w") as f:
                json.dump({"taken_at": now, "files": cur}, f)
            with open(CHANGELOG, "w") as f:
                f.write(f"# Harness changelog\n\nFirst baseline snapshot taken {now}. "
                        f"Nothing to report yet; run again after a SessionEnd.\n")
            print(f"Baseline snapshot written ({len(cur)} files). Changelog: {CHANGELOG}")
        else:
            print(f"[dry-run] baseline snapshot would be written ({len(cur)} files)")
        return

    changed, added, removed = [], [], []
    prev_files = prev.get("files", {})
    for path, (mtime, size) in cur.items():
        p = prev_files.get(path)
        if p is None:
            added.append(path)
        elif abs(p[0] - mtime) > 1.0 or p[1] != size:
            changed.append((path, p[1], size))
    for path in prev_files:
        if path not in cur:
            removed.append(path)

    entries = sorted(changed)[:MAX_FILES]

    if not args.dry_run:
        os.makedirs(STATE, exist_ok=True)
        with open(CHANGELOG, "w") as f:
            f.write(f"# Harness changelog\n\nGenerated {now}\n\n")
            if not (changed or added or removed):
                f.write("No harness mutations since last snapshot.\n")
            if added:
                f.write(f"## Added ({len(added)})\n")
                for p in sorted(added)[:MAX_FILES]:
                    f.write(f"- {p}\n")
            if entries:
                f.write(f"## Changed ({len(entries)} shown of {len(changed)})\n")
                for p, old, new in entries:
                    f.write(f"- {p}  [{classify(p)}]  ({old} -> {new} bytes)\n")
            if removed:
                f.write(f"## Removed ({len(removed)})\n")
                for p in sorted(removed)[:MAX_FILES]:
                    f.write(f"- {p}\n")
            f.write("\n## How to undo\n")
            f.write("- skill_autofix applies: `python3 skill_autofix.py --revert <edit_id>`\n")
            f.write("- review_queue approvals: `python3 review_queue.py --reject <pattern> --source base --reason \"noise\"`\n")
            f.write("- Full restore: git snapshot in `STATE/` (see AUTONOMY.md 'git snapshot + auto-revert')\n")
        with open(SNAPSHOT, "w") as f:
            json.dump({"taken_at": now, "files": cur}, f)
        print(f"Changelog written: {CHANGELOG} "
              f"(added={len(added)}, changed={len(changed)}, removed={len(removed)})")
    else:
        print(f"[dry-run] added={len(added)}, changed={len(changed)}, removed={len(removed)}")
        for p in sorted(added)[:15]:
            print(f"  + {p}")
        for p, _, _ in sorted(changed)[:15]:
            print(f"  ~ {p}")


if __name__ == "__main__":
    main()
