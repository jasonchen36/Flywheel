#!/usr/bin/env bash
# Install portable self-learning harness into HARNESS_HOME (default ~/.claude)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
HARNESS_HOME="${HARNESS_HOME:-$HOME/.claude}"
LEARNING="$HARNESS_HOME/MEMORY/LEARNING"
STATE="$HARNESS_HOME/MEMORY/STATE"
HOOKS="$HARNESS_HOME/hooks"
PI_EXT="${HARNESS_PI_EXTENSIONS:-$HOME/.pi/agent/extensions}"

copy_tree() {
  local src="$1"
  local dest="$2"
  local exclude="${3:-}"
  mkdir -p "$dest"
  if [ "${HARNESS_FORCE_TAR_COPY:-0}" != "1" ] && command -v rsync >/dev/null 2>&1; then
    if [ -n "$exclude" ]; then
      rsync -a --exclude "$exclude" "$src/" "$dest/"
    else
      rsync -a "$src/" "$dest/"
    fi
  elif command -v tar >/dev/null 2>&1; then
    if [ -n "$exclude" ]; then
      (cd "$src" && tar --exclude "$exclude" -cf - .) | (cd "$dest" && tar -xf -)
    else
      (cd "$src" && tar -cf - .) | (cd "$dest" && tar -xf -)
    fi
  else
    echo "error: Flywheel installation requires either rsync or tar" >&2
    return 1
  fi
}

for required_dir in learning hooks templates skills config; do
  if [ ! -d "$ROOT/$required_dir" ]; then
    echo "error: installer source directory is missing: $ROOT/$required_dir" >&2
    exit 1
  fi
done
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: Python 3.11 or newer is required" >&2
  exit 1
fi
if ! command -v bun >/dev/null 2>&1; then
  echo "warning: Bun is not installed; TypeScript hooks will not run until Bun is available" >&2
fi

echo "Installing Flywheel → $HARNESS_HOME"

mkdir -p "$LEARNING" "$STATE" "$LEARNING/SIGNALS" "$LEARNING/DIAGNOSTICS" \
  "$LEARNING/FAILURES" "$HARNESS_HOME/MEMORY/lessons" \
  "$HARNESS_HOME/meeting-summaries" "$HOOKS" "$HOOKS/lib" \
  "$HARNESS_HOME/commands" "$HARNESS_HOME/skills"

# Python loop
copy_tree "$ROOT/learning" "$LEARNING" "__pycache__"

# Config templates (do not overwrite existing)
for f in model_tiering.md retrieval_sop.md; do
  if [ ! -f "$STATE/$f" ]; then
    cp "$ROOT/config/$f" "$STATE/$f"
    echo "  wrote STATE/$f"
  fi
done
if [ ! -f "$LEARNING/editable_surfaces.json" ]; then
  cp "$ROOT/config/editable_surfaces.example.json" "$LEARNING/editable_surfaces.json"
fi
if [ ! -f "$HARNESS_HOME/PAI/USER/PAISECURITYSYSTEM/patterns.yaml" ]; then
  mkdir -p "$HARNESS_HOME/PAI/USER/PAISECURITYSYSTEM"
  cp "$ROOT/config/patterns.example.yaml" "$HARNESS_HOME/PAI/USER/PAISECURITYSYSTEM/patterns.yaml"
  echo "  wrote security patterns.yaml"
fi

# Hooks (backup if present)
for f in "$ROOT/hooks"/*.ts; do
  base=$(basename "$f")
  dest="$HOOKS/$base"
  if [ -f "$dest" ]; then
    cp "$dest" "$dest.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  fi
  cp "$f" "$dest"
done
if [ -d "$ROOT/hooks/lib" ]; then
  mkdir -p "$HOOKS/lib"
  cp "$ROOT/hooks/lib"/* "$HOOKS/lib/" 2>/dev/null || true
fi

# Hook runtime dependencies are isolated under hooks/node_modules.
if command -v bun >/dev/null 2>&1 && [ "${HARNESS_SKIP_BUN_INSTALL:-0}" != "1" ]; then
  cp "$ROOT/package.json" "$ROOT/bun.lock" "$HOOKS/"
  (cd "$HOOKS" && bun install --production --frozen-lockfile --silent)
  echo "  installed hook runtime dependencies"
fi

# Session end
cp "$ROOT/templates/session-end.sh" "$HOOKS/harness-session-end.sh"
chmod +x "$HOOKS/harness-session-end.sh"

# Skills
mkdir -p "$HARNESS_HOME/skills/self-improve" "$HARNESS_HOME/skills/model-tiering"
cp "$ROOT/skills/self-improve/SKILL.md" "$HARNESS_HOME/skills/self-improve/"
cp "$ROOT/skills/model-tiering/SKILL.md" "$HARNESS_HOME/skills/model-tiering/"
cp "$ROOT/skills/self-improve/SKILL.md" "$HARNESS_HOME/commands/self-improve.md" 2>/dev/null || true

# Pi extensions (optional)
if [ -d "$(dirname "$PI_EXT")" ]; then
  mkdir -p "$PI_EXT"
  cp "$ROOT/pi/"*.ts "$PI_EXT/"
  echo "  installed pi extensions → $PI_EXT"
fi

# Seed empty STATE files
[ -f "$STATE/enforcement_config.json" ] || echo '{"enabled":true,"overrides":{}}' > "$STATE/enforcement_config.json"
[ -f "$STATE/effectiveness_scores.json" ] || echo '{"scores":{},"escalate":[]}' > "$STATE/effectiveness_scores.json"
[ -f "$STATE/ace_playbook.json" ] || echo '{"bullets":[],"bullet_count":0}' > "$STATE/ace_playbook.json"
touch "$LEARNING/SIGNALS/ratings.jsonl"
# Portable knowledge pack
cp "$ROOT/docs/principles.md" "$STATE/principles.md"
cp "$ROOT/docs/memory.md" "$STATE/memory.md"
echo "  wrote STATE/principles.md + STATE/memory.md"

touch "$STATE/graphiti_pending_episodes.jsonl"
touch "$STATE/graph_preflight.md"


# Personal skill pack (portable)
mkdir -p "$HARNESS_HOME/skills" "$HOME/.agents/skills"
if [ -d "$ROOT/skills" ]; then
  copy_tree "$ROOT/skills" "$HARNESS_HOME/skills" "README.md"
  copy_tree "$ROOT/skills" "$HOME/.agents/skills" "README.md" 2>/dev/null || true
  mkdir -p "$HOME/.pi/agent/skills"
  for s in self-improve model-tiering pi-agent instincts caveman thermo-nuclear-code-quality-review; do
    if [ -d "$ROOT/skills/$s" ]; then
      mkdir -p "$HOME/.pi/agent/skills/$s"
      copy_tree "$ROOT/skills/$s" "$HOME/.pi/agent/skills/$s"
    fi
  done
  echo "  installed personal skills"
fi
echo "  Graphiti empty bootstrap: see graphiti/README.md"

cat <<MSG

Install complete.

Next steps:
1. Wire SessionEnd in Claude settings.json hooks to:
   bash $HOOKS/harness-session-end.sh

2. Wire PreToolUse Bash → SecurityValidator.hook.ts
   PostToolUse Write/Edit → VerificationReminder.hook.ts
   Stop → EnforcementGate.hook.ts
   UserPromptSubmit → RatingCapture + FailurePatternReminder

3. Optional env in settings.json:
   PAI_BACKGROUND_LLM_PROVIDER=gemini
   PAI_CLAUDE_HEADLESS_DISABLED=1
   HARNESS_HOME=$HARNESS_HOME
   GRAPHITI_MCP_URL=http://127.0.0.1:8000/mcp

4. Healthcheck:
   cd $LEARNING && python3 harness_healthcheck.py

5. See docs/ARCHITECTURE.md and docs/MIGRATE_EMPLOYER.md

MSG
