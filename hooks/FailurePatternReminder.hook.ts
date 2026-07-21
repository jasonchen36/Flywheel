#!/usr/bin/env bun
/**
 * FailurePatternReminder.hook.ts - Inject recent failure patterns into every turn
 *
 * PURPOSE:
 * Closes the gap between MEMORY (rules exist) and BEHAVIOR (rules applied).
 * Fires on every UserPromptSubmit and injects the 5 most recent failure patterns
 * + 3 most recent low-rating corrections as additionalContext, so Claude sees them
 * BEFORE generating each response — not just at session start.
 *
 * TRIGGER: UserPromptSubmit
 *
 * OUTPUT:
 * - hookSpecificOutput.additionalContext: compact failure pattern reminder
 * - exit(0): always exits clean (never blocks)
 *
 * PERFORMANCE: <30ms (filesystem reads only, no inference)
 */

import { readFileSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';

const PAI_DIR = process.env.PAI_DIR || join(process.env.HOME!, '.claude');
const FAILURES_DIR = join(PAI_DIR, 'MEMORY', 'LEARNING', 'FAILURES');
const LEARNING_DIR = join(PAI_DIR, 'MEMORY', 'LEARNING');
const MEMORY_DIR = join(PAI_DIR, 'projects', '-USER-', 'memory');
const SCORES_JSON = join(PAI_DIR, 'MEMORY', 'STATE', 'effectiveness_scores.json');
// ACE playbook (Zhang et al. 2025 / Weng 2026): itemized bullets with helpful/harmful
const ACE_PLAYBOOK = join(PAI_DIR, 'MEMORY', 'STATE', 'ace_playbook.json');
const GRAPH_PREFLIGHT = join(PAI_DIR, 'MEMORY', 'STATE', 'graph_preflight.md');
const ANTI_HALLUC = join(PAI_DIR, 'MEMORY', 'STATE', 'anti_hallucination.md');
const ENFORCE_LOG = join(PAI_DIR, 'MEMORY', 'LEARNING', 'enforcement_log.jsonl');

const STOPWORDS = new Set(['this','that','with','from','have','will','your','what',
  'when','where','which','about','would','could','should','there','their','then',
  'them','they','were','been','being','into','more','some','than','also','just',
  'like','want','need','make','made','does','done','using','use','the','and','for']);

// Effectiveness-driven injection weights: keep nagging about what still fails,
// stop nagging about what's fixed. Consumes measure_effectiveness.py output.
const VERDICT_WEIGHT: Record<string, number> = {
  regressed: 5, flat: 3, pending: 0, improving: -1, working: -2, resolved: -999,
};

interface Lesson { pattern: string; rule: string; text: string; }

function getRecentFailureSlugs(count: number): string[] {
  const patterns: string[] = [];
  if (!existsSync(FAILURES_DIR)) return patterns;

  try {
    const months = readdirSync(FAILURES_DIR, { withFileTypes: true })
      .filter(d => d.isDirectory() && /^\d{4}-\d{2}$/.test(d.name))
      .map(d => d.name).sort().reverse();

    for (const month of months) {
      if (patterns.length >= count) break;
      const dirs = readdirSync(join(FAILURES_DIR, month), { withFileTypes: true })
        .filter(d => d.isDirectory()).map(d => d.name).sort().reverse();

      for (const dir of dirs) {
        if (patterns.length >= count) break;
        const dateMatch = dir.match(/^(\d{4}-\d{2}-\d{2})/);
        const date = dateMatch ? dateMatch[1] : '';
        const slug = dir.replace(/^\d{4}-\d{2}-\d{2}-\d{6}_/, '').replace(/-/g, ' ');
        patterns.push(`[${date}] ${slug.substring(0, 70)}`);
      }
    }
  } catch {}

  return patterns;
}

function getRecentLowRatingCorrections(count: number): string[] {
  const corrections: string[] = [];
  const subdirs = ['ALGORITHM', 'SYSTEM'];

  for (const subdir of subdirs) {
    if (corrections.length >= count) break;
    const learningDir = join(LEARNING_DIR, subdir);
    if (!existsSync(learningDir)) continue;

    try {
      const months = readdirSync(learningDir, { withFileTypes: true })
        .filter(d => d.isDirectory() && /^\d{4}-\d{2}$/.test(d.name))
        .map(d => d.name).sort().reverse();

      for (const month of months) {
        if (corrections.length >= count) break;
        const files = readdirSync(join(learningDir, month))
          .filter(f => f.endsWith('.md')).sort().reverse();

        for (const file of files) {
          if (corrections.length >= count) break;
          try {
            const content = readFileSync(join(learningDir, month, file), 'utf-8');
            const ratingMatch = content.match(/rating:\s*(\d+)/);
            const feedbackMatch = content.match(/\*\*Feedback:\*\*\s*(.+)/);
            if (ratingMatch && feedbackMatch) {
              const rating = parseInt(ratingMatch[1], 10);
              if (rating <= 4) {
                corrections.push(`[${rating}/10] ${feedbackMatch[1].substring(0, 80)}`);
              }
            }
          } catch {}
        }
      }
    } catch {}
  }

  return corrections;
}

function tokenize(s: string): Set<string> {
  return new Set(
    (s.toLowerCase().match(/[a-z]+/g) || [])
      .filter(w => w.length > 3 && !STOPWORDS.has(w))
  );
}

function loadLessons(): Lesson[] {
  // Prefer ACE playbook (itemized curated bullets) over raw lesson files.
  // Curator merges/dedupes; injection stays structured — not a full prompt rewrite.
  if (existsSync(ACE_PLAYBOOK)) {
    try {
      const pb = JSON.parse(readFileSync(ACE_PLAYBOOK, 'utf-8'));
      const bullets = Array.isArray(pb.bullets) ? pb.bullets : [];
      // v2 ACE: only inject active sections (skip resolved + deferred quality-fails)
      const active = bullets.filter((b: { section?: string; quality?: number }) => {
        const sec = (b.section || 'strategy').toString();
        if (sec === 'resolved' || sec === 'deferred') return false;
        if (typeof b.quality === 'number' && b.quality < 2) return false;
        return true;
      });
      if (active.length > 0) {
        return active.map((b: { pattern?: string; description?: string; id?: string }) => {
          const pattern = (b.pattern || b.id || 'unknown').toString();
          const rule = (b.description || '').toString();
          return { pattern, rule, text: pattern.replace(/_/g, ' ') + ' ' + rule };
        });
      }
    } catch {}
  }

  const out: Lesson[] = [];
  if (!existsSync(MEMORY_DIR)) return out;
  try {
    for (const f of readdirSync(MEMORY_DIR).filter(n => /^lesson_autogen_.*\.md$/.test(n))) {
      const pattern = f.replace(/^lesson_autogen_/, '').replace(/\.md$/, '');
      const content = readFileSync(join(MEMORY_DIR, f), 'utf-8');
      // Rule = first non-empty line after the closing frontmatter '---'.
      const body = content.split(/\n---\n/).slice(1).join('\n---\n') || content;
      const rule = (body.split('\n').map(l => l.trim()).find(l => l && !l.startsWith('**')) || '').trim();
      out.push({ pattern, rule, text: pattern.replace(/_/g, ' ') + ' ' + rule });
    }
  } catch {}
  return out;
}

function loadEffectiveness(): { scores: Record<string, { verdict: string }>; } {
  try {
    if (existsSync(SCORES_JSON)) return JSON.parse(readFileSync(SCORES_JSON, 'utf-8'));
  } catch {}
  return { scores: {} };
}

/** Rank lessons by keyword overlap with the prompt, weighted by effectiveness verdict. */
function rankLessons(prompt: string, limit: number): Lesson[] {
  const ptoks = tokenize(prompt);
  if (ptoks.size === 0) return [];
  const eff = loadEffectiveness();
  const scored = loadLessons().map(l => {
    const ltoks = tokenize(l.text);
    let overlap = 0;
    for (const t of ptoks) if (ltoks.has(t)) overlap++;
    const verdict = eff.scores[l.pattern]?.verdict || 'pending';
    const weight = VERDICT_WEIGHT[verdict] ?? 0;
    // Relevance gate: must share a keyword. Effectiveness only re-ranks matches,
    // except 'resolved' which is suppressed outright.
    const score = overlap > 0 ? overlap + weight : -1000;
    return { lesson: l, score, verdict };
  }).filter(s => s.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, limit).map(s => s.lesson);
}

/**
 * Floor guarantee: always inject the worst-performing lessons regardless of keyword match.
 * Prevents lessons from silently never firing when prompt keywords don't overlap with
 * lesson rule text (e.g. VPN debug prompt never triggering incomplete_analysis lesson).
 */
function getWorstLessons(limit: number): Lesson[] {
  const eff = loadEffectiveness();
  const PRIORITY: Record<string, number> = { regressed: 0, flat: 1, pending: 2 };
  return loadLessons()
    .filter(l => {
      const v = eff.scores[l.pattern]?.verdict || 'pending';
      return v in PRIORITY;  // only include patterns that aren't resolved/working/improving
    })
    .sort((a, b) => {
      const va = eff.scores[a.pattern]?.verdict || 'pending';
      const vb = eff.scores[b.pattern]?.verdict || 'pending';
      return (PRIORITY[va] ?? 99) - (PRIORITY[vb] ?? 99);
    })
    .slice(0, limit);
}

async function main() {
  // Read stdin (required by Claude Code hooks protocol)
  let _input = '';
  try {
    const decoder = new TextDecoder();
    const reader = Bun.stdin.stream().getReader();
    const timeout = new Promise<void>(r => setTimeout(r, 500));
    const read = (async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        _input += decoder.decode(value, { stream: true });
      }
    })();
    await Promise.race([read, timeout]);
  } catch {}

  // Extract the user's prompt from the hook payload for relevance matching.
  let prompt = '';
  try { prompt = (JSON.parse(_input).prompt || '').toString(); } catch {}

  // Relevance layer: lessons whose keywords match THIS task, re-ranked by
  // effectiveness (still-failing patterns boosted, resolved ones suppressed).
  const relevant = rankLessons(prompt, 3);

  // Floor layer: always inject worst-performing lessons even when prompt keywords
  // don't match lesson rule text. Prevents lessons silently never firing.
  // Floor of 3 so both active regressions (unverified_completion + incomplete_analysis)
  // always inject even when the prompt has no keyword overlap.
  const worstLessons = getWorstLessons(3);
  const relevantPatterns = new Set(relevant.map(l => l.pattern));
  const floor = worstLessons.filter(l => !relevantPatterns.has(l.pattern));
  const allLessons = [...relevant, ...floor];

  // When we have targeted signal, trim the recency noise; else full fallback.
  const failures = getRecentFailureSlugs(allLessons.length > 0 ? 3 : 5);
  const corrections = getRecentLowRatingCorrections(allLessons.length > 0 ? 2 : 3);

  // Graph preflight (always inject if present — graphiti_bypassed is BLOCK)
  let graphBlock = '';
  try {
    if (existsSync(GRAPH_PREFLIGHT)) {
      const g = readFileSync(GRAPH_PREFLIGHT, 'utf-8').trim();
      if (g) graphBlock = g.substring(0, 900);
    }
  } catch {}

  if (allLessons.length === 0 && failures.length === 0 && corrections.length === 0 && !graphBlock) {
    process.exit(0);
  }

  const lines: string[] = ['⛔ FAILURE PATTERN REMINDER — check these before responding:'];

  if (graphBlock) {
    lines.push('Graph memory (mandatory before ≥2 research tools):');
    lines.push(graphBlock.split('\n').slice(0, 12).map(l => `  ${l}`).join('\n'));
    lines.push('  → graphiti_bypassed is BLOCK if research tools run without graphiti-memory/bungraph.');
    lines.push('Retrieval SOP: graphiti/bungraph → code/schema/docs → scrum files → web. See MEMORY/STATE/retrieval_sop.md');
  }

  if (relevant.length > 0) {
    lines.push('Lessons relevant to THIS task (highest priority):');
    relevant.forEach(l => lines.push(`  ◆ [${l.pattern}] ${l.rule.substring(0, 140)}`));
  }

  if (floor.length > 0) {
    lines.push('Always-on (worst-performing patterns, injected regardless of task):');
    floor.forEach(l => lines.push(`  ◆ [${l.pattern}] ${l.rule.substring(0, 140)}`));
  }

  if (failures.length > 0) {
    lines.push('Recent failures (do NOT repeat):');
    failures.forEach(f => lines.push(`  • ${f}`));
  }

  if (corrections.length > 0) {
    lines.push('Recent low-rating corrections:');
    corrections.forEach(c => lines.push(`  • ${c}`));
  }

  // Recent enforcement blocks (closed loop: what the gate actually fired on)
  try {
    if (existsSync(ENFORCE_LOG)) {
      const linesLog = readFileSync(ENFORCE_LOG, 'utf-8').trim().split('\n').filter(Boolean);
      const recent = linesLog.slice(-40);
      const blocked = new Map<string, number>();
      for (const ln of recent) {
        try {
          const e = JSON.parse(ln);
          if (e.blocked || e.mode === 'block') {
            const p = String(e.pattern || 'unknown');
            blocked.set(p, (blocked.get(p) || 0) + 1);
          }
        } catch {}
      }
      if (blocked.size > 0) {
        const top = [...blocked.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
        lines.push('Recent ENFORCEMENT blocks (do NOT repeat):');
        top.forEach(([p, n]) => lines.push(`  ⛔ ${p} ×${n}`));
      }
    }
  } catch {}

  // Compact anti-hallucination brief
  try {
    if (existsSync(ANTI_HALLUC)) {
      const brief = readFileSync(ANTI_HALLUC, 'utf-8').trim().split('\n')
        .filter(l => l && !l.startsWith('#') && !l.startsWith('*'))
        .slice(0, 8)
        .map(l => `  ${l}`)
        .join('\n');
      if (brief) {
        lines.push('Anti-hallucination brief:');
        lines.push(brief);
      }
    }
  } catch {}

  // Always-on anti-hallucination floor (independent of lesson/failure matches)
  lines.push('Anti-hallucination (always-on):');
  lines.push('  • State/schema/CI/PR/row claims → tool first, then claim (or tag [GUESS]/unverified).');
  lines.push('  • done/fixed/complete → STRONG paper trace only (fence CLI/test output + tool name, URL). Paths + bare N-rows fail.');
  lines.push('  • Invented metrics/PR#/line numbers without fence = block.');
  lines.push('  • graphiti/bungraph before ≥2 research tools; write durable findings back.');
  lines.push('  • No silent tool turns — always emit a one-line user-visible summary.');
  lines.push('→ If this task touches any of the above patterns, explicitly state how you will avoid repeating them.');

  const context = lines.join('\n');

  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'UserPromptSubmit',
      additionalContext: context,
    }
  }));

  process.exit(0);
}

main().catch(() => process.exit(0));
