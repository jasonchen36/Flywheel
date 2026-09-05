#!/usr/bin/env python3
"""Deterministic, fail-closed permission check for proposed harness mutations.

Usage:
  python3 surface_gate.py path1 path2 ...
  python3 surface_gate.py --file proposal.json
  echo "path1 path2" | python3 surface_gate.py

Exit codes are 0 when every declared path is allowed, 1 when any path is denied,
and 2 when the policy or proposal input is missing or malformed.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from harness_paths import HARNESS_HOME, PI_SKILLS

SURFACES = Path(__file__).resolve().parent / "editable_surfaces.json"


class PolicyError(ValueError):
    """Raised when a permission policy cannot be used safely."""


def _validate_rule(entry: object, *, allow: bool) -> dict[str, Any]:
    if isinstance(entry, str):
        if not entry:
            raise PolicyError("surface glob must not be empty")
        return {"glob": entry}
    if not isinstance(entry, dict):
        raise PolicyError("surface rules must be strings or JSON objects")
    pattern = entry.get("glob") if "glob" in entry else entry.get("path")
    if not isinstance(pattern, str) or not pattern:
        raise PolicyError("surface rule glob or path must be a non-empty string")
    normalized: dict[str, Any] = {"glob": pattern}
    if allow and "who" in entry:
        who = entry["who"]
        if not isinstance(who, list) or not all(isinstance(actor, str) for actor in who):
            raise PolicyError("surface rule who must be a list of actor names")
        normalized["who"] = who
    return normalized


def load_rules(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load and validate an editable-surfaces policy or raise ``PolicyError``."""
    path = path or SURFACES
    if not path.exists():
        raise PolicyError(f"editable_surfaces.json not found at {path}")
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read editable surfaces policy: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError("editable surfaces policy must be a JSON object")
    allow = value.get("allow", [])
    deny = value.get("deny", [])
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise PolicyError("editable surfaces allow and deny values must be lists")
    return {
        "allow": [_validate_rule(entry, allow=True) for entry in allow],
        "deny": [_validate_rule(entry, allow=False) for entry in deny],
    }


def _expanded_pattern(pattern: str) -> str:
    expanded = str(Path(pattern).expanduser().resolve())
    default_harness = str((Path.home() / ".claude").resolve())
    default_pi_skills = str((Path.home() / ".pi" / "agent" / "skills").resolve())
    if expanded == default_harness or expanded.startswith(default_harness + "/"):
        expanded = str(HARNESS_HOME.resolve()) + expanded[len(default_harness):]
    elif expanded == default_pi_skills or expanded.startswith(default_pi_skills + "/"):
        expanded = str(PI_SKILLS.resolve()) + expanded[len(default_pi_skills):]
    return expanded


def is_allowed(path: str, rules: dict[str, list[dict[str, Any]]], actor: str | None = None) -> bool:
    """Return whether *path* matches an allowed surface and no denied surface."""
    absolute = str(Path(path).expanduser().resolve())

    for rule in rules.get("deny", []):
        if fnmatch.fnmatch(absolute, _expanded_pattern(rule["glob"])):
            return False
    for rule in rules.get("allow", []):
        if not fnmatch.fnmatch(absolute, _expanded_pattern(rule["glob"])):
            continue
        permitted_actors = rule.get("who")
        if actor and permitted_actors and actor not in permitted_actors:
            continue
        return True
    return False


def _proposal_paths(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read proposal file: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("paths", []), list):
        raise PolicyError("proposal file must be an object with a paths list")
    paths = value.get("paths", [])
    if not all(isinstance(item, str) and item for item in paths):
        raise PolicyError("proposal paths must be non-empty strings")
    return paths


def main(argv: list[str] | None = None, *, stdin: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--file", type=Path, help="JSON file with a paths list")
    parser.add_argument("--actor", help="script name used to enforce a surface's who list")
    args = parser.parse_args(argv)
    input_stream = stdin if stdin is not None else sys.stdin

    paths = list(args.paths)
    try:
        if args.file:
            paths.extend(_proposal_paths(args.file))
        if not paths and not input_stream.isatty():
            paths.extend(input_stream.read().split())
        if not paths:
            print("[surface_gate] no paths to check", file=sys.stderr)
            return 0
        rules = load_rules()
    except PolicyError as exc:
        print(f"[surface_gate] {exc}", file=sys.stderr)
        return 2

    verdicts = [(path, is_allowed(path, rules, actor=args.actor)) for path in paths]
    denied = [path for path, allowed in verdicts if not allowed]
    for path, allowed in verdicts:
        print(f"{'ALLOW' if allowed else 'DENY '}  {path}")
    if denied:
        print(
            f"[surface_gate] {len(denied)} denied path(s); proposal must not touch them.",
            file=sys.stderr,
        )
        return 1
    print(f"[surface_gate] all {len(paths)} path(s) allowed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
