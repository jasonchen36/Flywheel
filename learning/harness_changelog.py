#!/usr/bin/env python3
"""Create a concise changelog of mutations across Flywheel's editable surfaces.

The command is read-only apart from its own snapshot and changelog files. It
tracks the installed learning tree, state, lessons, and command skills so the
SessionEnd loop's work is visible and reversible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

from harness_paths import COMMANDS, LEARNING, LESSONS_DIR, STATE
from state_io import atomic_write_json, atomic_write_text

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


def ignored_path(path) -> bool:
    return (
        path.is_symlink()
        or any(part in IGNORED_DIRS or part.endswith(".lock.d") for part in path.parts)
        or (path.name.startswith(".") and path.name.endswith(".tmp"))
    )


def file_digest(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot() -> dict[str, dict[str, object]]:
    """Return content fingerprints for watched mutable files."""
    result: dict[str, dict[str, object]] = {}
    for surface, root in WATCH_ROOTS.items():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if ignored_path(path) or not path.is_file():
                continue
            if path.parent == STATE and path.name in IGNORED_NAMES:
                continue
            try:
                stat = path.stat()
                sha256 = file_digest(path)
            except OSError:
                continue
            key = f"{surface}/{path.relative_to(root).as_posix()}"
            result[key] = {
                "sha256": sha256,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
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


def write_snapshot(timestamp: str, files: dict[str, dict[str, object]]) -> None:
    atomic_write_json(SNAPSHOT, {"taken_at": timestamp, "files": files})


def safe_int(value: object, default: int = 0) -> int:
    if not isinstance(value, (str, int, float)):
        return default
    try:
        return int(value)
    except (OverflowError, ValueError):
        return default


def fingerprint_changed(old: object, current: dict[str, object]) -> bool:
    """Compare current hashes while accepting the pre-hash ``[mtime, size]`` schema."""
    if isinstance(old, dict):
        old_digest = old.get("sha256")
        old_size = old.get("size")
        return old_digest != current.get("sha256") or old_size != current.get("size")
    if isinstance(old, (list, tuple)) and len(old) == 2:
        try:
            old_mtime = float(old[0])
            old_size = int(old[1])
        except (TypeError, ValueError):
            return True
        current_mtime = safe_int(current.get("mtime_ns")) / 1_000_000_000
        current_size = safe_int(current.get("size"), -1)
        return abs(old_mtime - current_mtime) > 1.0 or old_size != current_size
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = parser.parse_args(argv)

    timestamp = now_iso()
    current = snapshot()
    previous = load_previous()
    if previous is None:
        if args.dry_run:
            print(f"[dry-run] baseline snapshot would be written ({len(current)} files)")
            return 0
        write_snapshot(timestamp, current)
        atomic_write_text(
            CHANGELOG,
            f"# Harness changelog\n\nFirst baseline snapshot taken {timestamp}. "
            "Nothing to report yet; run again after a SessionEnd.\n",
        )
        print(f"Baseline snapshot written ({len(current)} files). Changelog: {CHANGELOG}")
        return 0

    previous_files_raw = previous.get("files", {})
    previous_files = (
        {path: value for path, value in previous_files_raw.items() if isinstance(path, str)}
        if isinstance(previous_files_raw, dict)
        else {}
    )
    added: list[str] = []
    changed: list[tuple[str, int, int]] = []
    removed: list[str] = []

    for path, fingerprint in current.items():
        old = previous_files.get(path)
        if old is None:
            added.append(path)
        elif fingerprint_changed(old, fingerprint):
            old_size = (
                old.get("size", 0)
                if isinstance(old, dict)
                else old[1] if isinstance(old, (list, tuple)) and len(old) == 2 else 0
            )
            try:
                normalized_old_size = int(old_size)
            except (TypeError, ValueError):
                normalized_old_size = 0
            changed.append((
                path,
                normalized_old_size,
                safe_int(fingerprint.get("size")),
            ))
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
        lines.extend([f"## Added ({min(len(added), MAX_FILES)} shown of {len(added)})", ""])
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
        lines.extend([f"## Removed ({min(len(removed), MAX_FILES)} shown of {len(removed)})", ""])
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
    atomic_write_text(CHANGELOG, "\n".join(lines))
    write_snapshot(timestamp, current)
    print(
        f"Changelog written: {CHANGELOG} "
        f"(added={len(added)}, changed={len(changed)}, removed={len(removed)})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
