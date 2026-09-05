#!/usr/bin/env bun
/**
 * RatingCapture.hook.ts - Unified Rating & Sentiment Capture (UserPromptSubmit)
 *
 * PURPOSE:
 * Single hook for all rating capture. Handles both explicit ratings (1-10 pattern)
 * and implicit sentiment detection (AI inference).
 *
 * TRIGGER: UserPromptSubmit
 *
 * FLOW:
 * 1. Parse input from stdin
 * 2. Check for explicit rating pattern → if found, write and exit
 * 3. If no explicit rating, run AI sentiment inference (Gemini Flash, cheap)
 * 4. Write result to ratings.jsonl
 * 5. Capture learnings for low ratings (<6), full failure capture for <=3
 *
 * OUTPUT:
 * - exit(0): Normal completion
 *
 * SIDE EFFECTS:
 * - Writes to: MEMORY/LEARNING/SIGNALS/ratings.jsonl
 * - Writes to: MEMORY/LEARNING/<category>/<YYYY-MM>/*.md (for low ratings)
 * - API call: Vertex Gemini 2.5 Flash for implicit sentiment (fast/cheap)
 *
 * PERFORMANCE:
 * - Explicit rating path: <50ms (no inference)
 * - Implicit sentiment path: ~0.5-2s (Gemini Flash via Inference fast tier)
 */

import { appendFileSync, mkdirSync, existsSync, readFileSync, writeFileSync, readdirSync } from 'fs';
import { execFileSync } from 'child_process';
import { join } from 'path';
import { inference } from '../PAI/Tools/Inference';
import { getIdentity, getPrincipal, getPrincipalName } from './lib/identity';
import { getLearningCategory } from './lib/learning-utils';
import { getISOTimestamp, getPSTComponents } from './lib/time';
import { captureFailure } from '../PAI/Tools/FailureCapture';
import { applyPaiSettingsEnv, detectAgentTag, paiFlagOn } from './lib/pai-env';

// Grok Build (and long sessions): re-apply settings.json PAI_* so kill-switch /
// Gemini model edits take effect without restarting the TUI.
applyPaiSettingsEnv();


// ── Shared Types ──

interface HookInput {
  session_id: string;
  prompt?: string;
  user_prompt?: string;  // Legacy field name
  transcript_path: string;
  hook_event_name: string;
}

interface RatingEntry {
  timestamp: string;
  rating: number;
  session_id: string;
  comment?: string;
  source?: 'implicit' | 'explicit';
  sentiment_summary?: string;
  confidence?: number;
  response_preview?: string;  // Truncated last response that was rated (from cache)
  // #5 richer signal — what the rated turn actually did (for pattern × tool × repo credit assignment)
  tools_used?: string[];
  files_touched?: string[];
  repo?: string;
  skill?: string;  // primary skill/command in the rated turn (for skill-failure attribution)
  skill_candidates?: string[];  // multi-label candidates (Skill tool, /cmd, path/repo)
  agent?: string;  // claude | grok | pi — for multi-agent skill_autofix / evals
  // Binary eval results on the FULL rated response (objective signal). Only evals that
  // fired are stored: { eval_id: { passed, pattern } }. evals.py is the source of truth.
  eval_results?: Record<string, { passed: boolean | null; pattern: string }>;
}

// ── Shared Constants ──

const BASE_DIR = process.env.HARNESS_HOME || process.env.PAI_DIR || join(process.env.HOME!, '.claude');
const SIGNALS_DIR = join(BASE_DIR, 'MEMORY', 'LEARNING', 'SIGNALS');
const RATINGS_FILE = join(SIGNALS_DIR, 'ratings.jsonl');
const PENDING_JUDGE_FILE = join(SIGNALS_DIR, 'pending_judge.jsonl');  // unrated turns → judge_outcomes.py
const EVALS_SCRIPT = join(BASE_DIR, 'MEMORY', 'LEARNING', 'evals.py');
const LAST_RESPONSE_CACHE = join(BASE_DIR, 'MEMORY', 'STATE', 'last-response.txt');
const MIN_PROMPT_LENGTH = 3;
const MIN_CONFIDENCE = 0.5;

// ── #5 Richer signal capture ──
// Captured once per hook run, spread into every rating by writeRating(). Records
// what the rated assistant turn actually did so the loop can cluster failures by
// (pattern × tool × repo), not just pattern. All fields optional → back-compatible.
//
// Skill attribution (2026-07-16): multi-label skill_candidates from
//   (1) last invoked Skill tool / slash command
//   (2) path under ~/.claude/commands (known command names in files_touched/tools)
//   (3) repo/task path classifier (dataform, bq, pr-workflow, …)
// general-session is ONLY a fallback when no real signal exists — never the dump bin.
type SignalCtx = {
  tools_used?: string[];
  files_touched?: string[];
  repo?: string;
  skill?: string;
  skill_candidates?: string[];
};
let SIGNAL_CTX: SignalCtx = {};

/** Known domain skills inferred from file paths / tool names (not slash-invoked). */
const PATH_SKILL_RULES: Array<{ skill: string; test: (s: string) => boolean }> = [
  { skill: 'dataform', test: (s) => /\.sqlx\b|\/dataform\/|definitions\//i.test(s) },
  { skill: 'bq', test: (s) => /\/bq\b|bigquery|`bq |bq query/i.test(s) },
  { skill: 'pr-workflow', test: (s) => /pull request|\/\.github\/|gh pr |create.?pr/i.test(s) },
  { skill: 'bronze-table-guide', test: (s) => /bronze_native|biglake_external|bronze\//i.test(s) },
  { skill: 'gold-layer-guide', test: (s) => /_gold\/|gold layer|program_key range/i.test(s) },
  { skill: 'silver-layer-guide', test: (s) => /_silver\/|silver layer/i.test(s) },
  { skill: 'datastream-check', test: (s) => /datastream|shard_db\.yml/i.test(s) },
  { skill: 'debug-airflow', test: (s) => /airflow|dags\//i.test(s) },
  { skill: 'jira', test: (s) => /\bjira\b|PROJ-|cli jira/i.test(s) },
  { skill: 'terraform', test: (s) => /\.tf\b|terragrunt|terraform/i.test(s) },
  { skill: 'review', test: (s) => /code.?review|review this pr|gh pr review/i.test(s) },
];

function loadKnownCommands(): Set<string> {
  try {
    const dir = join(BASE_DIR, 'commands');
    if (!existsSync(dir)) return new Set<string>();
    return new Set(
      readdirSync(dir)
        .filter((f: string) => f.endsWith('.md'))
        .map((f: string) => f.replace(/\.md$/, '')),
    );
  } catch {
    return new Set<string>();
  }
}

/**
 * Rank skill candidates → primary. Prefer explicit Skill/slash over path inference.
 * general-session is never preferred when any other candidate exists.
 */
function pickPrimarySkill(candidates: string[], known: Set<string>): string {
  const uniq = [...new Set(candidates.map((s) => s.toLowerCase().replace(/^\/+/, '')).filter(Boolean))];
  const nonGeneral = uniq.filter((s) => s !== 'general-session');
  if (!nonGeneral.length) return 'general-session';
  // Prefer known commands that match real skill files
  const knownHit = nonGeneral.find((s) => known.has(s));
  if (knownHit) return knownHit;
  // Prefer path-classifier / domain skills over noise tokens
  const pathHit = nonGeneral.find((s) => PATH_SKILL_RULES.some((r) => r.skill === s));
  if (pathHit) return pathHit;
  return nonGeneral[0];
}

function getSignalContext(transcriptPath: string, currentPrompt: string): SignalCtx {
  const ctx: SignalCtx = {};
  try {
    if (!transcriptPath || !existsSync(transcriptPath)) return ctx;
    const entries: any[] = [];
    for (const l of readFileSync(transcriptPath, 'utf-8').trim().split('\n')) {
      if (l.trim()) { try { entries.push(JSON.parse(l)); } catch {} }
    }

    const textOf = (c: any): string =>
      typeof c === 'string' ? c
      : Array.isArray(c) ? c.filter((p: any) => p.type === 'text').map((p: any) => p.text || '').join(' ')
      : '';

    // Indices of real user-text messages (skip tool_result-only user entries).
    const userIdx: number[] = [];
    entries.forEach((e, i) => {
      if (e.type === 'user' && e.message?.content && textOf(e.message.content).trim()) userIdx.push(i);
    });

    // Rated turn = assistant work for the PREVIOUS prompt. If the current prompt
    // is already the last transcript entry, window is before it; else after last.
    let start = -1, end = entries.length;
    if (userIdx.length >= 1) {
      const lastU = userIdx[userIdx.length - 1];
      const lastText = textOf(entries[lastU].message.content).trim();
      const isCurrent = !!currentPrompt && lastText.startsWith(currentPrompt.trim().slice(0, 40));
      if (isCurrent) { end = lastU; start = userIdx.length >= 2 ? userIdx[userIdx.length - 2] : -1; }
      else { start = lastU; }
    }

    const tools = new Set<string>();
    const files = new Set<string>();
    // Ordered candidates: last explicit skill wins for primary ranking later.
    const skillOrder: string[] = [];
    const skillSeen = new Set<string>();
    const pushSkill = (raw: string | undefined | null) => {
      if (!raw || typeof raw !== 'string') return;
      const s = raw.toLowerCase().replace(/^\/+/, '').trim();
      if (!s || s.length > 64 || !/^[a-z][\w-]*$/i.test(s)) return;
      // Drop common false positives from free-form text
      if (['the', 'this', 'that', 'with', 'from', 'and', 'for', 'use', 'run'].includes(s)) return;
      if (!skillSeen.has(s)) {
        skillSeen.add(s);
        skillOrder.push(s);
      } else {
        // re-mention → move to end (last invoked wins)
        const idx = skillOrder.indexOf(s);
        if (idx >= 0) {
          skillOrder.splice(idx, 1);
          skillOrder.push(s);
        }
      }
    };
    let cwd = '';
    for (let i = start + 1; i < end; i++) {
      const e = entries[i];
      if (e?.cwd) cwd = e.cwd;
      if (e?.type === 'assistant' && Array.isArray(e.message?.content)) {
        for (const p of e.message.content) {
          if (p?.type === 'tool_use' && p.name) {
            tools.add(p.name);
            // Skill tool invocation names the active skill directly (highest signal).
            if (p.name === 'Skill' && typeof p.input?.skill === 'string') pushSkill(p.input.skill);
            const fp = p.input?.file_path || p.input?.path || p.input?.notebook_path;
            if (typeof fp === 'string' && fp) files.add(fp);
          }
        }
      }
    }
    if (tools.size) ctx.tools_used = [...tools].slice(0, 25);
    if (files.size) ctx.files_touched = [...files].slice(0, 25);

    // Slash command / skill name attribution.
    // Claude: Skill tool_use + <command-name> wrappers.
    // Grok / freeform: bare /slug anywhere in the rated user turn, or "use skill X".
    const knownCommands = loadKnownCommands();

    const harvestSkillNames = (text: string) => {
      if (!text) return;
      const tag = text.match(/<command-name>\s*\/?([a-z][\w-]*)/i);
      if (tag?.[1]) pushSkill(tag[1]);
      const bareLead = text.trim().match(/^\/([a-z][\w-]*)/i);
      if (bareLead?.[1]) pushSkill(bareLead[1]);
      // Any /known-command mention in the turn (not only leading)
      for (const m of text.matchAll(/(?:^|[\s`])\/([a-z][\w-]*)\b/gi)) {
        const slug = m[1];
        if (knownCommands.has(slug) || knownCommands.size === 0) pushSkill(slug);
      }
      // "use skill review" / "running skill X" phrasing
      for (const m of text.matchAll(/\b(?:skill|command)\s+[\"'`]?([a-z][\w-]*)/gi)) {
        pushSkill(m[1]);
      }
    };

    if (start >= 0 && entries[start]?.message?.content) {
      harvestSkillNames(textOf(entries[start].message.content));
    }
    // Also scan assistant tool wrappers that Grok/other hosts may use instead of Skill.
    for (let i = start + 1; i < end; i++) {
      const e = entries[i];
      if (e?.type === 'assistant' && Array.isArray(e.message?.content)) {
        for (const p of e.message.content) {
          if (p?.type === 'tool_use' && p.name) {
            const n = String(p.name);
            // e.g. skill__review, mcp_skill_review, or input.skill / input.name
            if (/skill/i.test(n) && typeof p.input?.skill === 'string') pushSkill(p.input.skill);
            if (typeof p.input?.command === 'string' && /^[a-z][\w-]*$/i.test(p.input.command)) {
              if (knownCommands.has(p.input.command) || /skill/i.test(n)) pushSkill(p.input.command);
            }
          }
        }
      }
    }

    // Path / repo classifier: files_touched + tools + cwd → domain skills
    const pathBlob = [
      ...files,
      ...tools,
      cwd || '',
      currentPrompt || '',
    ].join('\n');
    for (const rule of PATH_SKILL_RULES) {
      if (rule.test(pathBlob)) pushSkill(rule.skill);
    }
    // If a touched path is under the configured commands directory, attribute that skill.
    const commandsPrefix = `${join(BASE_DIR, 'commands').replace(/\\/g, '/')}/`;
    for (const fp of files) {
      const normalized = String(fp).replace(/\\/g, '/');
      if (!normalized.startsWith(commandsPrefix)) continue;
      const slug = normalized.slice(commandsPrefix.length).match(/^([a-z][\w-]*)\.md$/i)?.[1];
      if (slug) pushSkill(slug);
    }

    try {
      const dir = cwd || process.cwd();
      const root = execFileSync('git', ['-C', dir, 'rev-parse', '--show-toplevel'], {
        encoding: 'utf-8', timeout: 1000, stdio: ['ignore', 'pipe', 'ignore'],
      }).trim();
      if (root) ctx.repo = root.split('/').pop() || '';
    } catch {}

    // Multi-label + primary. general-session only when nothing else fired.
    if (skillOrder.length) {
      ctx.skill_candidates = skillOrder.slice(-8); // keep last/most-recent up to 8
      ctx.skill = pickPrimarySkill(skillOrder, knownCommands);
    }
  } catch {}
  return ctx;
}

/**
 * Read cached last response written by StopHooks/LastResponseCache.
 * Stop fires before next UserPromptSubmit, so cache is usually fresh —
 * but Grok/Claude can miss it; prefer transcript extraction when available.
 */
function getLastResponse(): string {
  try {
    if (existsSync(LAST_RESPONSE_CACHE)) return readFileSync(LAST_RESPONSE_CACHE, 'utf-8');
  } catch {}
  return '';
}

/** Extract text from Claude/Grok-style message content. */
function messageText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((c: any) => c && (c.type === 'text' || typeof c.text === 'string'))
      .map((c: any) => c.text || '')
      .join('\n');
  }
  return '';
}

/**
 * Rated response = assistant turn for the PREVIOUS user message (not the current
 * prompt, which is the rating/reaction itself). Aligns rating → response for
 * classify_entry / evals / judge. Falls back to last-response.txt cache.
 */
function getRatedAssistantResponse(transcriptPath: string | undefined, currentPrompt: string): string {
  try {
    if (transcriptPath && existsSync(transcriptPath)) {
      const entries: any[] = [];
      for (const l of readFileSync(transcriptPath, 'utf-8').trim().split('\n')) {
        if (l.trim()) {
          try { entries.push(JSON.parse(l)); } catch { /* skip */ }
        }
      }

      // Indices of real user-text turns
      const userIdx: number[] = [];
      entries.forEach((e, i) => {
        const role = e.type || e.role;
        const content = e.message?.content ?? e.content;
        const text = messageText(content).trim();
        if ((role === 'user' || e.type === 'user') && text) userIdx.push(i);
      });

      let start = -1;
      let end = entries.length;
      if (userIdx.length >= 1) {
        const lastU = userIdx[userIdx.length - 1];
        const lastText = messageText(entries[lastU].message?.content ?? entries[lastU].content).trim();
        const isCurrent = !!currentPrompt && lastText.startsWith(currentPrompt.trim().slice(0, 40));
        // Window of assistant work for the rated turn
        if (isCurrent) {
          end = lastU;
          start = userIdx.length >= 2 ? userIdx[userIdx.length - 2] : -1;
        } else {
          start = lastU;
        }
      }

      // Concatenate assistant text in window (prefer last substantial block)
      const chunks: string[] = [];
      for (let i = start + 1; i < end; i++) {
        const e = entries[i];
        const role = e.type || e.role;
        if (role === 'assistant' || e.type === 'assistant') {
          const t = messageText(e.message?.content ?? e.content).trim();
          if (t) chunks.push(t);
        }
      }
      if (chunks.length) {
        // Prefer last chunk; if it's a thin banner, prepend prior substance
        let text = chunks[chunks.length - 1];
        if (text.length < 120 && chunks.length >= 2) {
          text = chunks.slice(-2).join('\n\n');
        }
        // Strip leading PAI mode chrome so classifiers see the actual content
        text = text
          .replace(/^═+[^\n]*\n/gm, '')
          .replace(/^[🗒️🔧✅📋🗣️🔄📃]+\s*[A-Z][^\n]*\n/gm, '')
          .trim();
        if (text.length >= 40) return text.slice(0, 2500);
      }
    }
  } catch (err) {
    console.error(`[RatingCapture] transcript extract failed: ${err}`);
  }
  // Fallback: disk cache (may be thin or stale on Grok)
  return getLastResponse().slice(0, 2500);
}

// ── Stdin Reader ──

async function readStdinWithTimeout(timeout: number = 5000): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    const timer = setTimeout(() => reject(new Error('Timeout')), timeout);
    process.stdin.on('data', (chunk) => { data += chunk.toString(); });
    process.stdin.on('end', () => { clearTimeout(timer); resolve(data); });
    process.stdin.on('error', (err) => { clearTimeout(timer); reject(err); });
  });
}

// ── Explicit Rating Detection ──

/**
 * Parse explicit rating pattern from prompt.
 * Matches: "7", "8 - good work", "6: needs work", "9 excellent", "10!"
 * Rejects: "3 items", "5 things to fix", "7th thing"
 */
function parseExplicitRating(prompt: string): { rating: number; comment?: string } | null {
  const trimmed = prompt.trim();
  // Rating must be: number alone, or number followed by whitespace/dash/colon then comment
  // Reject: "10/10", "3.5", "7th", "5x" — number followed by non-separator chars
  const ratingPattern = /^(10|[1-9])(?:\s*[-:]\s*|\s+)?(.*)$/;
  const match = trimmed.match(ratingPattern);
  if (!match) return null;

  const rating = parseInt(match[1], 10);
  const rest = match[2]?.trim() || undefined;

  if (rating < 1 || rating > 10) return null;

  // Reject if the character immediately after the number is not a separator
  // This catches "10/10", "3.5", "7th", "5x", etc.
  const afterNumber = trimmed.slice(match[1].length);
  if (afterNumber.length > 0 && /^[/.\dA-Za-z]/.test(afterNumber)) return null;

  // Reject if comment starts with words indicating a sentence, not a rating
  if (rest) {
    const sentenceStarters = /^(items?|things?|steps?|files?|lines?|bugs?|issues?|errors?|times?|minutes?|hours?|days?|seconds?|percent|%|th\b|st\b|nd\b|rd\b|of\b|in\b|at\b|to\b|the\b|a\b|an\b)/i;
    if (sentenceStarters.test(rest)) return null;
  }

  return { rating, comment: rest };
}

// ── Implicit Sentiment Analysis ──

const PRINCIPAL_NAME = getPrincipal().name;
const ASSISTANT_NAME = getIdentity().name;

const SENTIMENT_SYSTEM_PROMPT = `Analyze ${PRINCIPAL_NAME}'s message for emotional sentiment toward ${ASSISTANT_NAME} (the AI assistant).

CONTEXT: This is a personal AI system. ${PRINCIPAL_NAME} is the ONLY user. Never say "users" - always "${PRINCIPAL_NAME}."
IMPORTANT: Ratings come ONLY from ${PRINCIPAL_NAME}'s messages. ${ASSISTANT_NAME} must NEVER self-rate. If the message being analyzed is from ${ASSISTANT_NAME} (not ${PRINCIPAL_NAME}), return null.

OUTPUT FORMAT (JSON only):
{
  "rating": <1-10 or null>,
  "sentiment": "positive" | "negative" | "neutral",
  "confidence": <0.0-1.0>,
  "summary": "<brief explanation, 10 words max>",
  "detailed_context": "<comprehensive analysis for learning, 100-256 words>"
}

DETAILED_CONTEXT REQUIREMENTS (critical for learning system):
Write 100-256 words covering:
1. What ${PRINCIPAL_NAME} was trying to accomplish
2. What ${ASSISTANT_NAME} did (or failed to do)
3. Why ${PRINCIPAL_NAME} is frustrated/satisfied (the root cause)
4. What specific behavior triggered this reaction
5. What ${ASSISTANT_NAME} should have done differently (for negative) or what worked well (for positive)
6. Any patterns this reveals about ${PRINCIPAL_NAME}'s expectations

This context will be used retroactively to improve ${ASSISTANT_NAME}, so include enough detail that someone reading it months later can understand exactly what went wrong or right.

RATING SCALE:
- 1-2: Strong frustration, anger, disappointment with ${ASSISTANT_NAME}
- 3-4: Mild frustration, dissatisfaction
- 5: Neutral (no strong sentiment)
- 6-7: Satisfaction, approval
- 8-9: Strong approval, impressed
- 10: Extraordinary enthusiasm, blown away

CRITICAL DISTINCTIONS:
- Profanity can indicate EITHER frustration OR excitement
  - "What the fuck?!" + complaint about work = LOW (1-3)
  - "Holy shit, this is amazing!" = HIGH (9-10)
- Context is KEY: Is the emotion directed AT ${ASSISTANT_NAME}'s work?
- Sarcasm: "Oh great, another error" = negative despite "great"

SHORT POSITIVE EXPRESSIONS (CRITICAL — DO NOT UNDER-RATE):
When ${PRINCIPAL_NAME} gives short, direct praise like "great job", "nice work", "well done", "love it", "nailed it", "perfect", "awesome" — these are STRONG APPROVAL (8-9). ${PRINCIPAL_NAME} went out of his way to express satisfaction. Do NOT rate these as 6-7. Short praise = high signal. Rate 8 minimum.

IMPLIED SENTIMENT (CRITICAL — THESE ARE NOT NEUTRAL):
Most of ${PRINCIPAL_NAME}'s feedback is IMPLIED, not explicit. Use CONTEXT to detect these patterns:

Implied NEGATIVE (rate 2-4, never null):
- CORRECTIONS: "No, I meant..." / "That's not what I said" / "I said X not Y" → 3-4
- REPEATED REQUESTS: Having to ask the same thing twice → 2-3 (${ASSISTANT_NAME} failed to listen)
- TERSE REDIRECTS: ${ASSISTANT_NAME} gives long output, ${PRINCIPAL_NAME} responds with short redirect ignoring it → 4
- BEHAVIORAL CORRECTIONS: "Don't do that" / "Stop doing X" / "Never X" → 3 (past behavior was wrong)
- EXASPERATED QUESTIONS: "Why is this still broken?" / "How many times..." / "This is still happening" → 2-3
- SHORT DISMISSALS: "whatever" / "fine" / "just do it" / "never mind" → 3-4
- POINTING OUT OMISSIONS: "What about X?" (when X was obviously required) → 4
- ESCALATING FRUSTRATION: "after 20 attempts" / "I keep telling you" → 1-2

Implied POSITIVE (rate 6-8, never null):
- TRUST SIGNALS: "Alright, fix all of it" / "Go ahead" (after analysis) → 7
- BUILDING ON WORK: "Now also add..." / "Next, do..." (accepting prior result) → 6-7
- ENGAGED FOLLOW-UPS: "What about X?" (exploring, not correcting) → 6
- MOVING FORWARD: Accepting output and immediately giving next task → 6

RULE: If ${PRINCIPAL_NAME}'s message is a RESPONSE to ${ASSISTANT_NAME}'s work (check CONTEXT), it almost always carries sentiment. Pure neutral is RARE in responses. Default to detecting signal, not returning null.

WHEN TO RETURN null FOR RATING:
- Neutral technical questions ("Can you check the logs?")
- Simple commands ("Do it", "Yes", "Continue")
- No emotional indicators present
- Emotion unrelated to ${ASSISTANT_NAME}'s work

EXAMPLES:
${PRINCIPAL_NAME}: "What the fuck, why did you delete my file?"
-> {"rating": 1, "sentiment": "negative", "confidence": 0.95, "summary": "Angry about deleted file", "detailed_context": "..."}

${PRINCIPAL_NAME}: "Oh my god, this is fucking incredible, you nailed it!"
-> {"rating": 10, "sentiment": "positive", "confidence": 0.95, "summary": "Extremely impressed with result", "detailed_context": "..."}

${PRINCIPAL_NAME}: "great job"
-> {"rating": 8, "sentiment": "positive", "confidence": 0.9, "summary": "Direct praise for completed work", "detailed_context": "..."}

${PRINCIPAL_NAME}: "Fix the auth bug"
-> {"rating": null, "sentiment": "neutral", "confidence": 0.9, "summary": "Neutral command, no sentiment", "detailed_context": ""}

${PRINCIPAL_NAME}: "Hmm, that's not quite right"
-> {"rating": 4, "sentiment": "negative", "confidence": 0.6, "summary": "Mild dissatisfaction", "detailed_context": "..."}

${PRINCIPAL_NAME}: "No, I said rename them, not delete them"
-> {"rating": 3, "sentiment": "negative", "confidence": 0.8, "summary": "Correction — assistant misunderstood instruction", "detailed_context": "..."}

${PRINCIPAL_NAME}: "This is still happening after I asked you to fix it"
-> {"rating": 2, "sentiment": "negative", "confidence": 0.9, "summary": "Frustrated — repeated failure on same issue", "detailed_context": "..."}

${PRINCIPAL_NAME}: "Alright, fix all of it"
-> {"rating": 7, "sentiment": "positive", "confidence": 0.7, "summary": "Trusts analysis, approves proceeding", "detailed_context": "..."}

${PRINCIPAL_NAME}: "What about X?" (after ${ASSISTANT_NAME} presented complete work)
-> {"rating": 4, "sentiment": "negative", "confidence": 0.65, "summary": "Pointed out omission in delivered work", "detailed_context": "..."}`;

interface SentimentResult {
  rating: number | null;
  sentiment: 'positive' | 'negative' | 'neutral';
  confidence: number;
  summary: string;
  detailed_context: string;
}

function getRecentContext(transcriptPath: string, maxTurns: number = 3): string {
  try {
    if (!transcriptPath || !existsSync(transcriptPath)) return '';

    const content = readFileSync(transcriptPath, 'utf-8');
    const lines = content.trim().split('\n');
    const turns: { role: string; text: string }[] = [];

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line);
        if (entry.type === 'user' && entry.message?.content) {
          let text = '';
          if (typeof entry.message.content === 'string') {
            text = entry.message.content;
          } else if (Array.isArray(entry.message.content)) {
            text = entry.message.content.filter((c: any) => c.type === 'text').map((c: any) => c.text).join(' ');
          }
          if (text.trim()) turns.push({ role: 'User', text: text.slice(0, 200) });
        }
        if (entry.type === 'assistant' && entry.message?.content) {
          const text = typeof entry.message.content === 'string'
            ? entry.message.content
            : Array.isArray(entry.message.content)
              ? entry.message.content.filter((c: any) => c.type === 'text').map((c: any) => c.text).join(' ')
              : '';
          if (text) {
            const summaryMatch = text.match(/SUMMARY:\s*([^\n]+)/i);
            turns.push({ role: 'Assistant', text: summaryMatch ? summaryMatch[1] : text.slice(0, 150) });
          }
        }
      } catch {}
    }

    const recent = turns.slice(-maxTurns);
    return recent.length > 0 ? recent.map(t => `${t.role}: ${t.text}`).join('\n') : '';
  } catch { return ''; }
}

async function analyzeSentiment(prompt: string, context: string): Promise<SentimentResult | null> {
  // Re-read settings.json each call so Grok mid-session config flips apply live.
  applyPaiSettingsEnv();
  // Kill switch: overnight Haiku burn (2026-07-09). Fast tier now uses Gemini Flash
  // (PAI_BACKGROUND_LLM_*), but the same emergency stop still applies.
  // settings.json is SSOT (paiFlagOn) — stale process env alone cannot keep LLM off.
  const bgOff =
    paiFlagOn('PAI_HAIKU_BACKGROUND_DISABLED') ||
    paiFlagOn('PAI_RATING_CAPTURE_LLM_DISABLED');
  if (bgOff) {
    console.error('[RatingCapture] sentiment LLM DISABLED (PAI_HAIKU_BACKGROUND_DISABLED / PAI_RATING_CAPTURE_LLM_DISABLED)');
    return null;
  }

  const userPrompt = context ? `CONTEXT:\n${context}\n\nCURRENT MESSAGE:\n${prompt}` : prompt;

  const result = await inference({
    systemPrompt: SENTIMENT_SYSTEM_PROMPT,
    userPrompt,
    expectJson: true,
    timeout: 12000,
    level: 'fast',
  });

  if (!result.success || !result.parsed) {
    console.error(`[RatingCapture] Inference failed: ${result.error}`);
    return null;
  }

  return result.parsed as SentimentResult;
}

// ── Shared: Write Rating ──

/**
 * Score the rated response with the binary eval suite (evals.py, the single source of
 * truth). Uses the FULL cached response (not the 500-char preview) for a richer objective
 * signal. Best-effort: any failure (missing python, timeout, bad JSON) → undefined, never
 * blocks capture. Only evals that fired are returned.
 */
function scoreEvals(text: string): RatingEntry['eval_results'] | undefined {
  if (!text || !text.trim()) return undefined;
  try {
    const out = execFileSync('python3', [EVALS_SCRIPT, '--score-stdin'], {
      input: text, encoding: 'utf-8', timeout: 5000, stdio: ['pipe', 'pipe', 'ignore'],
    }).trim();
    if (!out) return undefined;
    const parsed = JSON.parse(out) as Record<string, { applied: boolean; passed: boolean | null; pattern: string }>;
    const fired: NonNullable<RatingEntry['eval_results']> = {};
    for (const [id, r] of Object.entries(parsed)) {
      if (r && r.applied) fired[id] = { passed: r.passed, pattern: r.pattern };
    }
    return Object.keys(fired).length ? fired : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Enqueue an UNRATED substantive turn for the subagent judge (judge_outcomes.py).
 * The human didn't react with clear sentiment, so there's no rating — but the loop is
 * starved of signal. The judge labels these later (semantic gap patterns). Human-rated
 * turns are NEVER enqueued (the human label wins). Trivial responses are skipped.
 */
function enqueueForJudge(sessionId: string, response: string, context: string): void {
  try {
    if (!response || response.trim().length < 200) return;  // skip acks / trivial turns
    if (!existsSync(SIGNALS_DIR)) mkdirSync(SIGNALS_DIR, { recursive: true });
    const row = {
      timestamp: getISOTimestamp(),
      session_id: sessionId,
      response: response.slice(0, 3000),
      context: (context || '').slice(0, 2000),
      ...SIGNAL_CTX,  // tools_used, files_touched, repo, skill
    };
    appendFileSync(PENDING_JUDGE_FILE, JSON.stringify(row) + '\n', 'utf-8');
    console.error('[RatingCapture] Enqueued unrated turn for outcome judge');
  } catch (err) {
    console.error(`[RatingCapture] enqueueForJudge failed (non-fatal): ${err}`);
  }
}

function writeRating(entry: RatingEntry): void {
  if (!existsSync(SIGNALS_DIR)) mkdirSync(SIGNALS_DIR, { recursive: true });
  // Score the RATED response (entry preview), not a possibly-stale cache alone.
  const textForEvals = entry.response_preview || getLastResponse();
  const evalResults = scoreEvals(textForEvals);
  // Multi-agent attribution: explicit entry.agent → PAI_AGENT_TAG → detect (GROK_AGENT=1 → grok).
  // Was defaulting everything to "claude", so Grok Build sessions were mis-attributed.
  const agentTag = entry.agent || detectAgentTag();
  // Multi-label skill attribution (2026-07-16):
  //   skill_candidates = Skill tool + /cmd + path/repo classifier
  //   skill = primary (last explicit or best-ranked); general-session ONLY as fallback
  const candidates = [
    ...(entry.skill_candidates || []),
    ...(SIGNAL_CTX.skill_candidates || []),
    entry.skill,
    SIGNAL_CTX.skill,
  ].filter((s): s is string => !!s && typeof s === 'string');
  const known = loadKnownCommands();
  const skillTag =
    entry.skill && entry.skill !== 'general-session'
      ? entry.skill
      : pickPrimarySkill(candidates.length ? candidates : ['general-session'], known);
  const skillCandidates = [
    ...new Set(
      (candidates.length ? candidates : [skillTag])
        .map((s) => s.toLowerCase().replace(/^\/+/, ''))
        .filter(Boolean),
    ),
  ].slice(0, 8);
  if (!skillCandidates.includes(skillTag)) skillCandidates.unshift(skillTag);

  const enriched = {
    ...entry,
    ...SIGNAL_CTX,
    agent: agentTag,
    skill: skillTag,
    skill_candidates: skillCandidates,
    ...(evalResults ? { eval_results: evalResults } : {}),
  };
  appendFileSync(RATINGS_FILE, JSON.stringify(enriched) + '\n', 'utf-8');
  const source = entry.source === 'implicit' ? 'implicit' : 'explicit';

  console.error(
    `[RatingCapture] Wrote ${source} rating ${entry.rating} agent=${agentTag} ` +
    `skill=${skillTag} candidates=[${skillCandidates.join(',')}] ` +
    `preview_len=${(entry.response_preview || '').length} to ${RATINGS_FILE}`,
  );
}

// ── Shared: Capture Low Rating Learning ──

function captureLowRatingLearning(
  rating: number,
  summaryOrComment: string,
  detailedContext: string,
  source: 'explicit' | 'implicit'
): void {
  if (rating >= 5) return;  // 5 = neutral (no sentiment), only capture actual negatives (<=4)
  if (!detailedContext?.trim()) return;  // Skip if no meaningful context to learn from

  const { year, month, day, hours, minutes, seconds } = getPSTComponents();
  const yearMonth = `${year}-${month}`;
  const category = getLearningCategory(detailedContext, summaryOrComment);
  const learningsDir = join(BASE_DIR, 'MEMORY', 'LEARNING', category, yearMonth);

  if (!existsSync(learningsDir)) mkdirSync(learningsDir, { recursive: true });

  const label = source === 'explicit' ? `low-rating-${rating}` : `sentiment-rating-${rating}`;
  const filename = `${year}-${month}-${day}-${hours}${minutes}${seconds}_LEARNING_${label}.md`;
  const filepath = join(learningsDir, filename);

  const tags = source === 'explicit'
    ? '[low-rating, improvement-opportunity]'
    : '[sentiment-detected, implicit-rating, improvement-opportunity]';

  const content = `---
capture_type: LEARNING
timestamp: ${year}-${month}-${day} ${hours}:${minutes}:${seconds} PST
rating: ${rating}
source: ${source}
auto_captured: true
tags: ${tags}
---

# ${source === 'explicit' ? 'Low Rating' : 'Implicit Low Rating'} Captured: ${rating}/10

**Date:** ${year}-${month}-${day}
**Rating:** ${rating}/10
**Detection Method:** ${source === 'explicit' ? 'Explicit Rating' : 'Sentiment Analysis'}
${summaryOrComment ? `**Feedback:** ${summaryOrComment}` : ''}

---

## Context

${detailedContext || 'No context available'}

---

## Improvement Notes

This response was rated ${rating}/10 by ${getPrincipalName()}. Use this as an improvement opportunity.

---
`;

  writeFileSync(filepath, content, 'utf-8');
  console.error(`[RatingCapture] Captured low ${source} rating learning to ${filepath}`);
}

// ── Main ──

async function main() {
  try {
    console.error('[RatingCapture] Hook started');
    const input = await readStdinWithTimeout();
    const data: HookInput = JSON.parse(input);
    const prompt = data.prompt || data.user_prompt || '';
    SIGNAL_CTX = getSignalContext(data.transcript_path, prompt);  // #5: enrich every rating this run

    // ── Path 1: Explicit Rating ──
    const explicitResult = parseExplicitRating(prompt);
    if (explicitResult) {
      console.error(`[RatingCapture] Explicit rating: ${explicitResult.rating}${explicitResult.comment ? ` - ${explicitResult.comment}` : ''}`);

      const ratedResponse = getRatedAssistantResponse(data.transcript_path, prompt);
      const entry: RatingEntry = {
        timestamp: getISOTimestamp(),
        rating: explicitResult.rating,
        session_id: data.session_id,
        source: 'explicit' as const,
      };
      if (explicitResult.comment) entry.comment = explicitResult.comment;
      if (ratedResponse) entry.response_preview = ratedResponse.slice(0, 2500);

      writeRating(entry);


      if (explicitResult.rating < 5) {
        captureLowRatingLearning(explicitResult.rating, explicitResult.comment || '', ratedResponse, 'explicit');

        if (explicitResult.rating <= 3) {
          try {
            await captureFailure({
              transcriptPath: data.transcript_path,
              rating: explicitResult.rating,
              sentimentSummary: explicitResult.comment || `Explicit low rating: ${explicitResult.rating}/10`,
              detailedContext: ratedResponse,
              sessionId: data.session_id,
            });
            console.error(`[RatingCapture] Created failure capture for explicit rating ${explicitResult.rating}`);
          } catch (err) {
            console.error(`[RatingCapture] Error creating failure capture: ${err}`);
          }
        }
      }

      process.exit(0);
    }

    // ── Path 2: Implicit Sentiment ──

    if (prompt.length < MIN_PROMPT_LENGTH) {
      console.error('[RatingCapture] Prompt too short for sentiment, exiting');
      process.exit(0);
    }

    // BUG FIX: Filter system-injected text before wasting inference on it
    // These are not {PRINCIPAL.NAME}'s messages — they're system notifications, task completions, etc.
    const SYSTEM_TEXT_PATTERNS = [
      /^<task-notification>/i,
      /^<system-reminder>/i,
      /^This session is being continued from a previous conversation/i,
      /^Please continue the conversation/i,
      /^Note:.*was read before/i,
    ];
    if (SYSTEM_TEXT_PATTERNS.some(re => re.test(prompt.trim()))) {
      console.error('[RatingCapture] System-injected text detected, skipping sentiment analysis');
      process.exit(0);
    }

    // BUG FIX: Positive word fast-path — short praise gets rating 8 directly
    // Prevents inference timeout from dropping positive signals (the "Excellent!" bug)
    const POSITIVE_PRAISE_WORDS = new Set([
      'excellent', 'amazing', 'brilliant', 'fantastic', 'wonderful', 'beautiful',
      'incredible', 'awesome', 'perfect', 'great', 'nice', 'superb', 'outstanding',
      'magnificent', 'stellar', 'phenomenal', 'remarkable', 'terrific', 'splendid',
      'exactly', 'correct', 'bingo',
    ]);
    const POSITIVE_PHRASES = new Set([
      'great job', 'good job', 'nice work', 'well done', 'nice job', 'good work',
      'love it', 'nailed it', 'looks great', 'looks good', 'thats great', 'that works',
      'sounds good', 'yes please', 'thats it', 'looks right', 'makes sense',
      'spot on', 'good catch', 'bang on', 'you nailed', 'nice catch',
    ]);
    const normalizedPrompt = prompt.trim().toLowerCase().replace(/[.!?,'"]/g, '');
    const promptWords = normalizedPrompt.split(/\s+/);
    if (promptWords.length <= 2) {
      if (POSITIVE_PRAISE_WORDS.has(normalizedPrompt) || POSITIVE_PHRASES.has(normalizedPrompt)
          || (promptWords.length === 2 && promptWords.every(w => POSITIVE_PRAISE_WORDS.has(w)))) {
        console.error(`[RatingCapture] Positive praise fast-path: "${prompt.trim()}" → rating 8`);
        const ratedResponse = getRatedAssistantResponse(data.transcript_path, prompt);
        writeRating({
          timestamp: getISOTimestamp(),
          rating: 8,
          session_id: data.session_id,
          source: 'implicit',
          sentiment_summary: `Direct praise: "${prompt.trim()}"`,
          confidence: 0.95,
          ...(ratedResponse ? { response_preview: ratedResponse.slice(0, 2500) } : {}),
        });

        process.exit(0);
      }
    }

    // Await sentiment analysis — must complete before process exits
    const context = getRecentContext(data.transcript_path, 6);  // BUG FIX: 6 turns instead of 3
    const ratedResponse = getRatedAssistantResponse(data.transcript_path, prompt);
    console.error(`[RatingCapture] Running implicit sentiment analysis (rated_resp_len=${ratedResponse.length})...`);

    try {
      const sentiment = await analyzeSentiment(prompt, context);
      if (!sentiment) {
        console.error('[RatingCapture] Sentiment returned null, exiting');
        process.exit(0);
      }

      // BUG FIX: null means "no sentiment detected" — skip, don't convert to 5
      // Previously null→5 inflated neutral count (60% of all entries were noise)
      if (sentiment.rating === null) {
        console.error('[RatingCapture] Sentiment returned null rating (no sentiment), skipping write');
        enqueueForJudge(data.session_id, ratedResponse, context);  // unrated → let the judge label it
        process.exit(0);
      }
      if (sentiment.confidence < MIN_CONFIDENCE) {
        console.error(`[RatingCapture] Confidence ${sentiment.confidence} below ${MIN_CONFIDENCE}, skipping`);
        enqueueForJudge(data.session_id, ratedResponse, context);  // unrated → let the judge label it
        process.exit(0);
      }

      console.error(`[RatingCapture] Implicit: ${sentiment.rating}/10 (conf: ${sentiment.confidence}) - ${sentiment.summary}`);

      const entry: RatingEntry = {
        timestamp: getISOTimestamp(),
        rating: sentiment.rating,
        session_id: data.session_id,
        source: 'implicit',
        sentiment_summary: sentiment.summary,
        confidence: sentiment.confidence,
      };
      if (ratedResponse) entry.response_preview = ratedResponse.slice(0, 2500);

      writeRating(entry);


      if (sentiment.rating < 5) {
        captureLowRatingLearning(
          sentiment.rating,
          sentiment.summary,
          sentiment.detailed_context || '',
          'implicit'
        );

        if (sentiment.rating <= 3) {
          await captureFailure({
            transcriptPath: data.transcript_path,
            rating: sentiment.rating,
            sentimentSummary: sentiment.summary,
            detailedContext: sentiment.detailed_context || '',
            sessionId: data.session_id,
          }).catch((err) => console.error(`[RatingCapture] Failure capture error: ${err}`));
        }
      }
    } catch (err) {
      // BUG FIX: Log failures visibly — write a marker entry so inference failures show up in the data
      console.error(`[RatingCapture] Sentiment error: ${err}`);
      const failedPromptPreview = prompt.trim().slice(0, 80);
      console.error(`[RatingCapture] FAILED for prompt: "${failedPromptPreview}"`);
      // Write a visible failure marker so we can track inference reliability
      writeRating({
        timestamp: getISOTimestamp(),
        rating: 5,
        session_id: data.session_id,
        source: 'implicit',
        sentiment_summary: `INFERENCE_FAILED: "${failedPromptPreview}"`,
        confidence: 0,
      });

    }

    process.exit(0);
  } catch (err) {
    console.error(`[RatingCapture] Error: ${err}`);
    process.exit(0);
  }
}

main();
