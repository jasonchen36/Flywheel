#!/bin/bash
# session-end.sh — Self-learning harness SessionEnd pipeline
# Wire from Claude Code hooks.SessionEnd (command hook).
# Requires: pyenv or python3 with deps; optional Graphiti MCP on :8000

set -u
HARNESS_HOME="${HARNESS_HOME:-$HOME/.claude}"
LEARNING="$HARNESS_HOME/MEMORY/LEARNING"
export HARNESS_HOME

# Kill switch
if [ "${PAI_SELF_IMPROVE_DISABLED:-0}" = "1" ]; then
  echo "[session-end] self-improve disabled" >> /tmp/self-improve-disabled.log
  exit 0
fi

PY="${HARNESS_PYTHON:-pyenv exec python3}"
# Fallback if pyenv missing
if ! command -v pyenv >/dev/null 2>&1; then
  PY=python3
fi

# Optional continuous jobs (background)
(
  cd "$LEARNING" \
    && $PY ratings_hygiene.py --apply > /tmp/ratings_hygiene.log 2>&1 \
    && $PY meeting_summary_ingest.py --once --flush --limit 15 > /tmp/meeting_ingest.log 2>&1 \
    && $PY intent_how_audit.py > /tmp/intent_how_audit.log 2>&1 \
    || true
) &

# Main self-improve pipeline (parallel stages)
(
  cd "$LEARNING" \
  && ($PY self_improve.py --no-llm --classify-other > /tmp/self_improve.log 2>&1 & \
      $PY evals.py > /tmp/evals.log 2>&1 & \
      $PY judge_outcomes.py > /tmp/judge_outcomes.log 2>&1 & \
      $PY pattern_promotion.py > /tmp/pattern_promotion.log 2>&1 & \
      wait) \
  && ($PY measure_effectiveness.py > /tmp/measure_effectiveness.log 2>&1 & \
      $PY skill_autofix.py --apply > /tmp/skill_autofix.log 2>&1 & \
      $PY enforcement_promotion.py > /tmp/enforcement_promotion.log 2>&1 & \
      $PY held_out_regression.py --apply > /tmp/held_out_regression.log 2>&1 & \
      $PY lesson_dedup.py --apply > /tmp/lesson_dedup.log 2>&1 & \
      wait) \
  && $PY lesson_evolve.py > /tmp/lesson_evolve.log 2>&1 \
  && $PY review_queue.py --auto-drain --min-age "${SELF_IMPROVE_AUTO_DRAIN_MIN_AGE:-0}" \
       > /tmp/review_queue_auto_drain.log 2>&1 \
  && ($PY held_out_suite.py --gate > /tmp/held_out_suite.log 2>&1 || true) \
  && ($PY agent_rollouts.py --gate > /tmp/agent_rollouts.log 2>&1 || true) \
  && $PY self_harness.py --apply --skip-rollouts > /tmp/self_harness.log 2>&1 \
  && $PY consolidate_memory.py --apply > /tmp/consolidate_memory.log 2>&1 \
  && $PY session_graphiti_autoseed.py > /tmp/session_graphiti_autoseed.log 2>&1 \
  && $PY sync_graph_memory.py > /tmp/sync_graph_memory.log 2>&1 \
  && $PY flush_graphiti_pending.py --limit 50 > /tmp/flush_graphiti.log 2>&1 \
  || true
) >> /tmp/self-improve.log 2>&1 &

exit 0
