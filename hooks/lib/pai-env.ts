/**
 * pai-env.ts — SSOT loader for PAI_* env from ~/.claude/settings.json
 *
 * Why: Grok Build (and long-lived Claude sessions) inject settings env at
 * process start. Mid-session edits to settings.json (kill switches, Gemini
 * model routing, agent tag) would otherwise stay stale until restart.
 *
 * Policy: for keys starting with PAI_ (and ANTHROPIC_DEFAULT_HAIKU_MODEL used
 * by background LLM), the settings.json value wins over process.env.
 * Other process env is left alone.
 */
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';

const SETTINGS_PATH =
  process.env.CLAUDE_SETTINGS_PATH ||
  join(process.env.HOME || '', '.claude', 'settings.json');

let cached: { mtimeMs: number; env: Record<string, string> } | null = null;

function loadSettingsEnv(): Record<string, string> {
  try {
    if (!existsSync(SETTINGS_PATH)) return {};
    const st = require('fs').statSync(SETTINGS_PATH);
    if (cached && cached.mtimeMs === st.mtimeMs) return cached.env;
    const raw = JSON.parse(readFileSync(SETTINGS_PATH, 'utf-8'));
    const env: Record<string, string> = {};
    if (raw && typeof raw.env === 'object' && raw.env) {
      for (const [k, v] of Object.entries(raw.env)) {
        if (typeof v === 'string') env[k] = v;
      }
    }
    cached = { mtimeMs: st.mtimeMs, env };
    return env;
  } catch {
    return {};
  }
}

/** Apply settings.json PAI_* into process.env (settings wins). Idempotent. */
export function applyPaiSettingsEnv(): void {
  const env = loadSettingsEnv();
  for (const [k, v] of Object.entries(env)) {
    if (k.startsWith('PAI_') || k === 'ANTHROPIC_DEFAULT_HAIKU_MODEL') {
      process.env[k] = v;
    }
  }
}

/** Read one key: settings.json first, then process.env, then default. */
export function paiEnv(key: string, fallback = ''): string {
  const env = loadSettingsEnv();
  if (key in env) return env[key] ?? fallback;
  return process.env[key] ?? fallback;
}

export function paiFlagOn(key: string): boolean {
  return paiEnv(key, '0') === '1';
}

/**
 * Multi-agent attribution for ratings.jsonl / skill_autofix.
 * Prefer explicit PAI_AGENT_TAG; else detect Grok Build / pi / default claude.
 */
export function detectAgentTag(): string {
  const explicit = paiEnv('PAI_AGENT_TAG') || process.env.PAI_AGENT_TAG;
  if (explicit) return explicit;

  // Grok Build wrapper + binary set GROK_AGENT=1 / GROK_CLAUDE_*
  if (
    process.env.GROK_AGENT === '1' ||
    process.env.GROK_CLAUDE_HOOKS_ENABLED === 'true' ||
    process.env.GROK_CLAUDE_SKILLS_ENABLED === 'true' ||
    !!process.env.GROK_BIN
  ) {
    return 'grok';
  }

  // pi agent
  if (process.env.PI_AGENT === '1' || process.env.PAI_AGENT_HOST === 'pi') {
    return 'pi';
  }

  return 'claude';
}
