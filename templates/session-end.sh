#!/usr/bin/env bash
# session-end.sh — serialized, non-blocking self-learning harness pipeline.

set -u

HARNESS_HOME="${HARNESS_HOME:-$HOME/.claude}"
LEARNING="$HARNESS_HOME/MEMORY/LEARNING"
LOG_DIR="${HARNESS_LOG_DIR:-$LEARNING/DIAGNOSTICS/session-end}"
STATUS_FILE="$LOG_DIR/latest.tsv"
SUMMARY_FILE="$LOG_DIR/latest.json"
SKIPPED_FILE="$LOG_DIR/skipped.tsv"
SKIPPED_SUMMARY_FILE="$LOG_DIR/skipped.json"
LOCK_FILE="$LOG_DIR/pipeline.lock"
LOCK_DIR="$LOG_DIR/pipeline.lock.d"
LOCK_KIND=""
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_STARTED_MS=0
STAGE_TOTAL=0
STAGE_FAILED=0
FAILED_STAGES=()
LEARNING_PRESENT=1
if [ ! -d "$LEARNING" ]; then
  LEARNING_PRESENT=0
fi
export HARNESS_HOME

mkdir -p "$LOG_DIR"

epoch_millis() {
  local value
  value=$(date +%s%3N 2>/dev/null || true)
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$(( $(date +%s) * 1000 ))"
  fi
}

RUN_STARTED_MS=$(epoch_millis)

record_status() {
  local stage="$1"
  local status="$2"
  local code="$3"
  local duration_ms="${4:-0}"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stage" "$status" "$code" "$duration_ms" \
    >> "$STATUS_FILE"
}

write_run_summary() {
  local status="$1"
  local finished_at finished_ms duration_ms failed_json tmp
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  finished_ms=$(epoch_millis)
  duration_ms=$((finished_ms - RUN_STARTED_MS))
  failed_json=""
  if [ "${#FAILED_STAGES[@]}" -gt 0 ]; then
    local quoted=()
    local stage
    for stage in "${FAILED_STAGES[@]}"; do
      quoted+=("\"$stage\"")
    done
    failed_json=$(IFS=,; printf '%s' "${quoted[*]}")
  fi
  tmp="$SUMMARY_FILE.tmp.$BASHPID"
  printf '{\n  "run_id": "%s",\n  "status": "%s",\n  "started_at": "%s",\n  "finished_at": "%s",\n  "duration_ms": %s,\n  "stage_total": %s,\n  "stage_failed": %s,\n  "failed_stages": [%s]\n}\n' \
    "$RUN_ID" "$status" "$RUN_STARTED_AT" "$finished_at" "$duration_ms" \
    "$STAGE_TOTAL" "$STAGE_FAILED" "$failed_json" > "$tmp"
  mv "$tmp" "$SUMMARY_FILE"
}

write_skipped_summary() {
  local tmp="$SKIPPED_SUMMARY_FILE.tmp.$BASHPID"
  printf '{\n  "run_id": "%s",\n  "status": "already-running",\n  "timestamp": "%s",\n  "pid": %s\n}\n' \
    "$RUN_ID" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BASHPID" > "$tmp"
  mv "$tmp" "$SKIPPED_SUMMARY_FILE"
}

if [ "${PAI_SELF_IMPROVE_DISABLED:-0}" = "1" ]; then
  : > "$STATUS_FILE"
  record_status pipeline disabled 0 0
  write_run_summary disabled
  exit 0
fi

if [ "$LEARNING_PRESENT" -eq 0 ]; then
  : > "$STATUS_FILE"
  STAGE_FAILED=1
  FAILED_STAGES+=("learning_directory")
  record_status pipeline failed 1 0
  write_run_summary failed
  printf 'Flywheel learning directory does not exist: %s\n' "$LEARNING" >&2
  exit 1
fi

if [ -n "${HARNESS_PYTHON:-}" ]; then
  read -r -a PY <<< "$HARNESS_PYTHON"
elif command -v pyenv >/dev/null 2>&1; then
  PY=(pyenv exec python3)
else
  PY=(python3)
fi

record_skipped_run() {
  printf '%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "already-running" "$BASHPID" >> "$SKIPPED_FILE"
  write_skipped_summary
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
  local started_ms finished_ms duration_ms code status
  started_ms=$(epoch_millis)
  STAGE_TOTAL=$((STAGE_TOTAL + 1))
  if "$@" > "$log" 2>&1; then
    code=0
    status=ok
  else
    code=$?
    status=failed
    STAGE_FAILED=$((STAGE_FAILED + 1))
    FAILED_STAGES+=("$stage")
  fi
  finished_ms=$(epoch_millis)
  duration_ms=$((finished_ms - started_ms))
  record_status "$stage" "$status" "$code" "$duration_ms"
  return "$code"
}

skip_stage() {
  local stage=$1 reason=$2 log="$LOG_DIR/$1.log"
  STAGE_TOTAL=$((STAGE_TOTAL + 1))
  printf '%s\n' "$reason" > "$log"
  record_status "$stage" skipped 0 0
}

run_surface_gate_selftest() {
  local log="$LOG_DIR/surface_gate_selftest.log"
  local started_ms finished_ms duration_ms code
  started_ms=$(epoch_millis)
  STAGE_TOTAL=$((STAGE_TOTAL + 1))
  "${PY[@]}" surface_gate.py "$HARNESS_HOME/hooks/claude-session-end" > "$log" 2>&1
  code=$?
  finished_ms=$(epoch_millis)
  duration_ms=$((finished_ms - started_ms))
  if [ "$code" -eq 1 ]; then
    printf '%s\n' '[surface_gate] enforcement OK — hooks path denied' >> "$log"
    record_status surface_gate_selftest ok 0 "$duration_ms"
  else
    STAGE_FAILED=$((STAGE_FAILED + 1))
    FAILED_STAGES+=("surface_gate_selftest")
    printf '[surface_gate] WARN: expected deny (exit 1), got %s\n' "$code" >> "$log"
    record_status surface_gate_selftest failed "$code" "$duration_ms"
  fi
}

run_pipeline() {
  if ! acquire_pipeline_lock; then
    return 0
  fi
  trap 'if [ "$LOCK_KIND" = "directory" ]; then rm -rf "$LOCK_DIR"; fi' EXIT

  : > "$STATUS_FILE"
  record_status pipeline started 0 0
  cd "$LEARNING" || {
    STAGE_FAILED=$((STAGE_FAILED + 1))
    FAILED_STAGES+=("learning_directory")
    record_status pipeline failed 1 0
    write_run_summary failed
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
  local validation_ok=1
  run_stage held_out_suite "${PY[@]}" held_out_suite.py --gate || validation_ok=0
  run_stage agent_rollouts "${PY[@]}" agent_rollouts.py --gate || validation_ok=0
  if [ "$validation_ok" -eq 1 ]; then
    if run_stage self_harness "${PY[@]}" self_harness.py --apply --gate --skip-rollouts; then
      run_stage consolidate_memory "${PY[@]}" consolidate_memory.py --apply || true
    else
      skip_stage consolidate_memory "Skipped: self-harness validation or ACE rebuild failed."
    fi
  else
    skip_stage self_harness "Skipped: held-out or rollout prerequisite failed."
    skip_stage consolidate_memory "Skipped: self-harness was not accepted."
  fi
  run_stage session_graphiti_autoseed "${PY[@]}" session_graphiti_autoseed.py || true
  run_stage sync_graph_memory "${PY[@]}" sync_graph_memory.py || true
  run_stage flush_graphiti "${PY[@]}" flush_graphiti_pending.py --limit 50 || true
  run_stage harness_changelog "${PY[@]}" harness_changelog.py || true
  run_surface_gate_selftest

  local final_status=completed
  if [ "$STAGE_FAILED" -gt 0 ]; then
    final_status=completed_with_failures
  fi
  record_status pipeline completed 0 "$(( $(epoch_millis) - RUN_STARTED_MS ))"
  write_run_summary "$final_status"
}

run_pipeline >/dev/null 2>&1 &
exit 0
