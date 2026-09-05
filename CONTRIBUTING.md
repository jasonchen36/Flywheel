# Contributing to Flywheel

Flywheel is a safety-sensitive automation harness. Changes should preserve deterministic enforcement, bounded mutation surfaces, reversible state transitions, and the ability to run from a custom `HARNESS_HOME`.

## Development setup

Create an isolated Python environment if desired, then install the runtime and development dependencies. Bun is required for hook integration tests, and ShellCheck is required by the complete quality gate.

```bash
make install-dev
make check
```

| Command | Purpose |
|---|---|
| `make test` | Run the complete Python and hook regression suite. |
| `make lint` | Run high-signal Ruff checks and compile every Python module. |
| `make security` | Scan Python code and declared dependencies for known security issues. |
| `make shellcheck` | Validate installer, orchestration, Graphiti, and hook shell scripts. |
| `make check` | Run every required local and CI check. |

## Change expectations

Behavior changes must include focused regression tests. Tests must use temporary directories and an explicit `HARNESS_HOME`; they must not read or mutate a contributor’s real `~/.claude`, `~/.pi`, or Graphiti state. Network and model calls should be mocked or disabled in unit tests.

All installed runtime paths must derive from `learning/harness_paths.py` or the equivalent `HARNESS_HOME` resolution in TypeScript. Do not introduce new hard-coded `~/.claude` state paths. Keep generated validation commands bounded: untrusted or model-authored text must never be passed through a shell.

The installer must remain idempotent. Existing user-owned state and configuration should not be overwritten unless the behavior is explicitly documented, backed up, and tested. SessionEnd stages may be non-blocking, but failures must remain observable through status or diagnostic output.

## Pull requests

A pull request should explain the failure mode or user outcome being improved, identify the affected safety boundary, and include the verification performed. Run `make check` before requesting review. If a check is intentionally excluded, explain why and describe the replacement evidence.
