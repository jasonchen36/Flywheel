#!/usr/bin/env python3
"""Create a concise changelog of mutations across Flywheel's editable surfaces.

The command is read-only apart from its own snapshot and changelog files. It
tracks the installed learning tree, state, lessons, and command skills so the
SessionEnd loop's work is visible and reversible.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from harness_paths import COMMANDS, LEARNING, LESSONS_DIR, STATE

SNAPSHOT = STATE / "harness_changelog_last.json"
CHANGELOG = STATE / "harness_changelog.md"
MAX_FILES = 60
WATCH_ROOTS = {
    "learning": LEARNING,
    "state": STATE,
    "lessons": LESSONS_DIR,
    "commands": COMMANDS,
}
IGNORED_NAMES = {SNAPSHOT.name, CHANGELOG.name}
IGNORED_DIRS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__", "venv"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot() -> dict[str, tuple[float, int]]:
    """Return ``{surface/path: (mtime, size)}`` for watched mutable files."""
    result: dict[str, tuple[float, int]] = {}
    for surface, root in WATCH_ROOTS.items():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or any(part in IGNORED_DIRS for part in path.parts):
                continue
            if path.parent == STATE and path.name in IGNORED_NAMES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = f"{surface}/{path.relative_to(root).as_posix()}"
            result[key] = (stat.st_mtime, stat.st_size)
    return result


def classify(path: str) -> str:
    """Return a human-readable mutation class for a snapshot key."""
    if path.startswith("lessons/"):
        return "lessons"
    if path.startswith("commands/"):
        return "skills"
    if path.startswith("state/"):
        return "state"
    if "/DIAGNOSTICS/" in path or path.startswith("learning/DIAGNOSTICS/"):
        return "diagnostics"
    if path.endswith(".jsonl"):
        return "ledgers"
    if "/held_out_suite/fixtures/" in path or path.startswith("learning/held_out_suite/fixtures/"):
        return "fixtures"
    if path.endswith(".py"):
        return "scripts"
    return "other"


def load_previous() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    try:
        data = json.loads(SNAPSHOT.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_snapshot(timestamp: str, files: dict[str, tuple[float, int]]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps({"taken_at": timestamp, "files": files}, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = parser.parse_args()

    timestamp = now_iso()
    current = snapshot()
    previous = load_previous()
    if previous is None:
        if args.dry_run:
            print(f"[dry-run] baseline snapshot would be written ({len(current)} files)")
            return 0
        write_snapshot(timestamp, current)
        CHANGELOG.write_text(
            f"# Harness changelog\n\nFirst baseline snapshot taken {timestamp}. "
            "Nothing to report yet; run again after a SessionEnd.\n"
        )
        print(f"Baseline snapshot written ({len(current)} files). Changelog: {CHANGELOG}")
        return 0

    previous_files = previous.get("files", {})
    if not isinstance(previous_files, dict):
        previous_files = {}
    added: list[str] = []
    changed: list[tuple[str, int, int]] = []
    removed: list[str] = []

    for path, (mtime, size) in current.items():
        old = previous_files.get(path)
        if not isinstance(old, (list, tuple)) or len(old) != 2:
            added.append(path)
        elif abs(float(old[0]) - mtime) > 1.0 or int(old[1]) != size:
            changed.append((path, int(old[1]), size))
    removed.extend(path for path in previous_files if path not in current)

    if args.dry_run:
        print(f"[dry-run] added={len(added)}, changed={len(changed)}, removed={len(removed)}")
        for path in sorted(added)[:15]:
            print(f"  + {path}")
        for path, _, _ in sorted(changed)[:15]:
            print(f"  ~ {path}")
        return 0

    lines = ["# Harness changelog", "", f"Generated {timestamp}", ""]
    if not (changed or added or removed):
        lines.append("No harness mutations since last snapshot.")
    if added:
        lines.extend([f"## Added ({len(added)})", ""])
        lines.extend(f"- {path} [{classify(path)}]" for path in sorted(added)[:MAX_FILES])
        lines.append("")
    if changed:
        lines.extend([f"## Changed ({min(len(changed), MAX_FILES)} shown of {len(changed)})", ""])
        lines.extend(
            f"- {path} [{classify(path)}] ({old} -> {new} bytes)"
            for path, old, new in sorted(changed)[:MAX_FILES]
        )
        lines.append("")
    if removed:
        lines.extend([f"## Removed ({len(removed)})", ""])
        lines.extend(f"- {path}" for path in sorted(removed)[:MAX_FILES])
        lines.append("")
    lines.extend(
        [
            "## How to undo",
            "",
            "- Skill auto-fixes: `python3 skill_autofix.py --revert <edit_id>`",
            "- Review approvals: `python3 review_queue.py --reject <pattern> --source base --reason \"noise\"`",
            "- Full restore: inspect the git snapshots described in `docs/AUTONOMY.md`.",
            "",
        ]
    )
    STATE.mkdir(parents=True, exist_ok=True)
    CHANGELOG.write_text("\n".join(lines))
    write_snapshot(timestamp, current)
    print(
        f"Changelog written: {CHANGELOG} "
        f"(added={len(added)}, changed={len(changed)}, removed={len(removed)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
