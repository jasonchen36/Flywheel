#!/usr/bin/env bash
# session-end.sh — serialized, non-blocking self-learning harness pipeline.

set -u

HARNESS_HOME="${HARNESS_HOME:-$HOME/.claude}"
LEARNING="$HARNESS_HOME/MEMORY/LEARNING"
LOG_DIR="${HARNESS_LOG_DIR:-$LEARNING/DIAGNOSTICS/session-end}"
STATUS_FILE="$LOG_DIR/latest.tsv"
SKIPPED_FILE="$LOG_DIR/skipped.tsv"
LOCK_FILE="$LOG_DIR/pipeline.lock"
LOCK_DIR="$LOG_DIR/pipeline.lock.d"
LOCK_KIND=""
export HARNESS_HOME

if [ "${PAI_SELF_IMPROVE_DISABLED:-0}" = "1" ]; then
  mkdir -p "$LOG_DIR"
  printf '%s\tdisabled\t0\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE"
  exit 0
fi

if [ ! -d "$LEARNING" ]; then
  printf 'Flywheel learning directory does not exist: %s\n' "$LEARNING" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

if [ -n "${HARNESS_PYTHON:-}" ]; then
  read -r -a PY <<< "$HARNESS_PYTHON"
elif command -v pyenv >/dev/null 2>&1; then
  PY=(pyenv exec python3)
else
  PY=(python3)
fi

record_status() {
  local stage="$1"
  local status="$2"
  local code="$3"
  printf '%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stage" "$status" "$code" >> "$STATUS_FILE"
}

record_skipped_run() {
  printf '%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "already-running" "$BASHPID" >> "$SKIPPED_FILE"
}

acquire_pipeline_lock() {
  if [ "${HARNESS_FORCE_DIRECTORY_LOCK:-0}" != "1" ] && command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
      record_skipped_run
      return 1
    fi
    LOCK_KIND="flock"
    return 0
  fi

  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$BASHPID" > "$LOCK_DIR/pid"
    LOCK_KIND="directory"
    return 0
  fi

  local owner=""
  if [ -f "$LOCK_DIR/pid" ]; then
    owner=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
  fi
  if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      printf '%s\n' "$BASHPID" > "$LOCK_DIR/pid"
      LOCK_KIND="directory"
      return 0
    fi
  fi

  record_skipped_run
  return 1
}

run_stage() {
  local stage="$1"
  shift
  local log="$LOG_DIR/$stage.log"
  if "$@" > "$log" 2>&1; then
    record_status "$stage" ok 0
    return 0
  fi
  local code=$?
  record_status "$stage" failed "$code"
  return "$code"
}

run_surface_gate_selftest() {
  local log="$LOG_DIR/surface_gate_selftest.log"
  "${PY[@]}" surface_gate.py "$HARNESS_HOME/hooks/claude-session-end" > "$log" 2>&1
  local code=$?
  if [ "$code" -eq 1 ]; then
    printf '%s\n' '[surface_gate] enforcement OK — hooks path denied' >> "$log"
    record_status surface_gate_selftest ok 0
  else
    printf '[surface_gate] WARN: expected deny (exit 1), got %s\n' "$code" >> "$log"
    record_status surface_gate_selftest failed "$code"
  fi
}

run_pipeline() {
  if ! acquire_pipeline_lock; then
    return 0
  fi
  trap 'if [ "$LOCK_KIND" = "directory" ]; then rm -rf "$LOCK_DIR"; fi' EXIT

  : > "$STATUS_FILE"
  record_status pipeline started 0
  cd "$LEARNING" || {
    record_status pipeline failed 1
    return 1
  }

  # Deterministic order: producers precede consumers and no two stages mutate
  # shared lessons, ledgers, or state files concurrently.
  run_stage ratings_hygiene "${PY[@]}" ratings_hygiene.py --apply || true
  run_stage meeting_ingest "${PY[@]}" meeting_summary_ingest.py --once --limit 15 || true
  run_stage intent_how_audit "${PY[@]}" intent_how_audit.py || true
  run_stage self_improve "${PY[@]}" self_improve.py --no-llm --classify-other || true
  run_stage evals "${PY[@]}" evals.py || true
  run_stage judge_outcomes "${PY[@]}" judge_outcomes.py || true
  run_stage pattern_promotion "${PY[@]}" pattern_promotion.py || true
  run_stage measure_effectiveness "${PY[@]}" measure_effectiveness.py || true
  run_stage skill_autofix "${PY[@]}" skill_autofix.py --apply || true
  run_stage enforcement_promotion "${PY[@]}" enforcement_promotion.py || true
  run_stage held_out_regression "${PY[@]}" held_out_regression.py --apply || true
  run_stage lesson_dedup "${PY[@]}" lesson_dedup.py --apply || true
  run_stage lesson_evolve "${PY[@]}" lesson_evolve.py || true
  run_stage review_queue "${PY[@]}" review_queue.py --auto-drain \
    --min-age "${SELF_IMPROVE_AUTO_DRAIN_MIN_AGE:-0}" || true
  run_stage held_out_suite "${PY[@]}" held_out_suite.py --gate || true
  run_stage agent_rollouts "${PY[@]}" agent_rollouts.py --gate || true
  run_stage self_harness "${PY[@]}" self_harness.py --apply --skip-rollouts || true
  run_stage consolidate_memory "${PY[@]}" consolidate_memory.py --apply || true
  run_stage session_graphiti_autoseed "${PY[@]}" session_graphiti_autoseed.py || true
  run_stage sync_graph_memory "${PY[@]}" sync_graph_memory.py || true
  run_stage flush_graphiti "${PY[@]}" flush_graphiti_pending.py --limit 50 || true
  run_stage harness_changelog "${PY[@]}" harness_changelog.py || true
  run_surface_gate_selftest
  record_status pipeline completed 0
}

run_pipeline >/dev/null 2>&1 &
exit 0
