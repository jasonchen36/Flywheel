#!/usr/bin/env python3
"""surface_gate.py — deterministic pre-apply surface check for harness mutations.

DoorDash Flux lesson: "Each playbook declares the tools it requires, and Flux
grants only the scoped permissions needed for that task." The deterministic
equivalent for this harness: a proposal declares the paths it will mutate, and
this gate checks each against editable_surfaces.json BEFORE anything is applied
-- permission lives outside the LLM's judgment.

This script is READ-ONLY on editable_surfaces.json and never mutates anything.

Usage:
  python3 surface_gate.py path1 path2 ...      # check declared target paths
  python3 surface_gate.py --file proposal.json # paths from a proposal file
  echo "path1 path2" | python3 surface_gate.py # paths on stdin (whitespace/newline separated)

Exit codes:
  0  all paths allowed (or none given)
  1  at least one path denied/unknown (denied paths printed to stderr)
"""

import argparse
import json
import os
import sys

SURFACES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "editable_surfaces.json")


def load_rules():
    if not os.path.exists(SURFACES):
        print(f"[surface_gate] editable_surfaces.json not found at {SURFACES}", file=sys.stderr)
        sys.exit(2)
    with open(SURFACES) as f:
        return json.load(f)


def is_allowed(path, rules, actor=None):
    """Path is allowed if it matches an allow-glob (fnmatch, ~ expanded) and no
    deny-glob. Deny wins over allow; unknown paths are DENIED (fail closed).
    If an actor (script name) is given, it must be listed in the surface's
    'who' for the match to count.
    """
    import fnmatch

    def expand(p):
        return os.path.abspath(os.path.expanduser(p))

    abs_path = expand(path)
    deny = rules.get("deny", []) or []
    for d in deny:
        if isinstance(d, dict):
            d = d.get("glob", "")
        if d and fnmatch.fnmatch(abs_path, expand(d)):
            return False
    for a in rules.get("allow", []) or []:
        if isinstance(a, str):
            a = {"glob": a}
        glob = a.get("glob", "")
        if not glob:
            continue
        if fnmatch.fnmatch(abs_path, expand(glob)):
            if actor and a.get("who") and actor not in a.get("who", []):
                continue  # surface allows the path, but not this actor
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--file", help="JSON file with a 'paths' list")
    ap.add_argument("--actor", help="script name (e.g. skill_autofix.py) to enforce 'who'")
    args = ap.parse_args()

    paths = list(args.paths)
    if args.file:
        with open(args.file) as f:
            paths.extend(json.load(f).get("paths", []))
    if not paths and not sys.stdin.isatty():
        paths.extend(sys.stdin.read().split())

    if not paths:
        print("[surface_gate] no paths to check", file=sys.stderr)
        sys.exit(0)

    rules = load_rules()
    verdicts = [(p, is_allowed(p, rules, actor=args.actor)) for p in paths]
    denied = [p for p, ok in verdicts if not ok]
    for p, ok in verdicts:
        print(f"{'ALLOW' if ok else 'DENY '}  {p}")
    if denied:
        print(f"[surface_gate] {len(denied)} denied path(s); proposal must not touch them.",
              file=sys.stderr)
        sys.exit(1)
    print(f"[surface_gate] all {len(paths)} path(s) allowed.")


if __name__ == "__main__":
    main()
