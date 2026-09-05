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
| `make test` | Run the complete branch-aware suite and enforce the repository coverage floor. |
| `make coverage-foundations` | Enforce 100% statement and branch coverage for state I/O, review storage, and configuration models. |
| `make lint` | Run Ruff, mypy, and Python compilation checks. |
| `make hooks` | Bundle every TypeScript hook and pi extension. |
| `make security` | Scan Python code plus Python and JavaScript dependencies. |
| `make shellcheck` | Validate installer, orchestration, Graphiti, and hook shell scripts. |
| `make check` | Run every required local and CI check. |

## Change expectations

Behavior changes must include focused regression tests. Tests must use temporary directories and an explicit `HARNESS_HOME`; they must not read or mutate a contributor’s real `~/.claude`, `~/.pi`, or Graphiti state. Network and model calls should be mocked or disabled in unit tests.

All installed runtime paths must derive from `learning/harness_paths.py` or the equivalent `HARNESS_HOME` resolution in TypeScript. Do not introduce new hard-coded `~/.claude` state paths. Whole-file mutable state, generated lessons and reports, and single or batched JSONL writes should use `learning/state_io.py`; do not add direct mutable-state writes. Shared review mutations must use `learning/review_store.py`; never implement a separate load-modify-rewrite cycle for `pending_human_review.jsonl`. Enforcement configuration changes must pass through `learning/harness_config.py` and keep Claude/pi normalization keys synchronized. New foundation branches require complete branch coverage, including surface-permission and ratings-hygiene paths. Keep generated validation commands bounded: untrusted or model-authored text must never be passed through a shell.

The installer must enforce the documented Python floor, honor explicit custom destinations, and remain idempotent with collision-free backups. Existing user-owned state and configuration should not be overwritten unless the behavior is explicitly documented, backed up, and tested. Approval side effects must use the `pending → processing → approved|action_failed` contract; do not mark a review approved before its mutation succeeds. SessionEnd must remain non-blocking and single-run per installation; stage dependencies should be explicit and sequential unless shared-state independence is proven. Failures and durations must remain observable through status rows and atomic JSON summaries.

## Pull requests

A pull request should explain the failure mode or user outcome being improved, identify the affected safety boundary, and include the verification performed. Run `make check` before requesting review. If a check is intentionally excluded, explain why and describe the replacement evidence.
