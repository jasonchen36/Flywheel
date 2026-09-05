#!/usr/bin/env bun
/**
 * EnforcementGate.hook.ts — graduation to hard enforcement
 *
 * PURPOSE:
 * The last rung of the self-improvement loop. When a lesson provably FAILS to
 * fix its pattern (measure_effectiveness.py marks it flat/regressed → escalate
 * list), and that pattern has a high-precision text detector, this Stop hook
 * BLOCKS the response and forces a fix — converting a soft memory note into
 * deterministic enforcement.
 *
 * SAFE BY CONSTRUCTION:
 * armed = escalate(from effectiveness_scores.json) ∩ DETECTORS − overrides('off')
 * If nothing is escalated, the gate is INERT (exits clean, blocks nothing).
 * It self-arms only when the data proves a lesson isn't working.
 *
 * CONTROLS:
 * - MEMORY/STATE/enforcement_config.json : { enabled, overrides:{pattern:"off|warn|block"} }
 * - env ENFORCEMENT_OFF=1 : global kill switch
 * - every fire logged to MEMORY/LEARNING/enforcement_log.jsonl
 *
 * TRIGGER: Stop
 */

import { readHookInput, parseTranscriptFromInput } from './lib/hook-io';
import { readFileSync, existsSync, writeFileSync, appendFileSync } from 'fs';
import { execFileSync } from 'child_process';
import { join } from 'path';
import { homedir } from 'os';

const PAI_DIR = process.env.HARNESS_HOME || process.env.PAI_DIR || join(homedir(), '.claude');
const SCORES_JSON = join(PAI_DIR, 'MEMORY', 'STATE', 'effectiveness_scores.json');
const CONFIG_JSON = join(PAI_DIR, 'MEMORY', 'STATE', 'enforcement_config.json');
const ENFORCE_LOG = join(PAI_DIR, 'MEMORY', 'LEARNING', 'enforcement_log.jsonl');

function runPythonHelper(scriptName: string, input: string, timeout: number): string {
  const script = join(PAI_DIR, 'tools', scriptName);
  return execFileSync('pyenv', ['exec', 'python3', script], {
    input,
    timeout,
    encoding: 'utf-8',
    stdio: ['pipe', 'pipe', 'ignore'],
  }).trim();
}

type Mode = 'off' | 'warn' | 'block';
interface Detector {
  defaultMode: Mode;
  // returns a short reason string if the failure pattern is present, else null
  detect: (resp: string) => string | null;
}

// ── Artifact detection: does the response contain a verifiable paper trace? ──
// Weak: path/filename OK for hedges (unverified_claims).
// Strong: completion claims must show CLI/test/URL/code-fence evidence —
// bare paths alone were the 2026-07 regression (subj Δ=+0.134): agents said
// "picture is complete" with a table of paths and still got rated 3/10.
function hasWeakArtifact(r: string): boolean {
  if (r.includes('```')) return true;
  if (/https?:\/\/\S+/.test(r)) return true;
  if (/(?:[\w-]+\/)+[\w.-]+/.test(r)) return true;
  if (/\b[\w-]+\.(py|ts|tsx|js|jsx|sh|md|json|ya?ml|sql|sqlx|go|tf|java|rb|txt|csv)\b/i.test(r)) return true;
  if (/\bEXIT[: ]|\bexit code\b|\bPASS(ED)?\b|\bFAIL(ED)?\b/i.test(r)) return true;
  // Bare "N rows/tests" is NOT an artifact — agents invent those. Only count
  // metrics inside a fence or next to a real CLI/PASS marker (see hasStrongArtifact).
  if (/\b\d+\s+(tests?|rows?|files?|passed|failed|matches)\b/i.test(r) &&
      (r.includes('```') || /\b(EXIT|PASS|FAIL|pytest|rtk |\$ )/i.test(r))) {
    return true;
  }
  return false;
}

function hasStrongArtifact(r: string): boolean {
  // Code/output fence (must contain more than empty fence)
  if (/```[\s\S]{8,}?```/.test(r) || (r.includes('```') && r.split('```').length >= 3)) return true;
  // Live URL (PR, CI, dashboard)
  if (/https?:\/\/\S+/.test(r)) return true;
  // Explicit CLI exit markers
  if (/\bEXIT[: ]\s*\d|\bexit code\s*[=:]?\s*\d/i.test(r)) return true;
  // Pass/fail counts only with a test runner / tests keyword (not bare "8 passed")
  if (/\b\d+\s+(passed|failed)\b/i.test(r) &&
      /\b(pytest|jest|mocha|unittest|go test|tests?\b|rtk |\$ )/i.test(r)) {
    return true;
  }
  // Other metrics ONLY when tied to a tool/CLI signal — bare "5000 rows" is hallucinatable
  if (/\b\d+\s+(tests?|rows?|files?|matches)\b/i.test(r) &&
      /\b(pytest|rtk |\$ |bq |gh |exit|stdout|output|evidence:|proof:|verified (via|with|by))\b/i.test(r)) {
    return true;
  }
  // Named tool run with output-ish phrasing
  if (/\b(verified (via|with|by)|proof:|evidence:|dry[- ]?run|bq query|pytest|rtk |\$ )\b/i.test(r)
      && (/\b(output|stdout|result|shows?|passed|failed|exit)\b/i.test(r) || r.includes('```'))) {
    return true;
  }
  return false;
}

// Back-compat alias used by hedge detector
function hasArtifact(r: string): boolean {
  return hasWeakArtifact(r);
}

// Completion claims — broader than bare done/fixed (catches "picture is complete")
const COMPLETION_CLAIM =
  /\b(done|complete|completed|fixed|merged|posted|deployed|shipped|finished|approved)\b|\b(picture is complete|full picture|now (have|the) complete|work is complete|everything is complete|analysis is complete|should be complete|falsely marked complete|marked (as )?complete)\b/i;

// Confident absolute claims about system/external state (anti-hallucination 2026-07-09).
// High precision: only fires when BOTH confident-state language AND no strong artifact
// AND no epistemic hedge/tag. Complements claim_evidence (LLM) with a free fast path.
const CONFIDENT_STATE =
  /\b(the (table|column|schema|partition|job|dag|pipeline|pr|check|ci|deployment|cluster|dataset|view|topic|subscription) (is|are|has|have|exists?|contains?|passed|failed|running|green|healthy|empty|missing)|column exists|partition(ed)? by|in production|already (deployed|merged|approved)|checks? (have )?passed|ci is green|schema shows|row count is|there (is|are) \d+|pr is (green|clean|approved|mergeable)|all (tests|checks) pass(ed)?|verified that|confirmed that|the (bug|issue|error) is (fixed|gone|resolved))\b/i;
const TAGGED_UNCERTAIN =
  /\[(INFERRED|GUESS|FRAME|UNKNOWN|VLOW)\]|haven'?t verified|not (yet )?verified|unverified|i (have )?(not |n'?t )?(checked|verified|run|confirmed)|\b(i think|i believe|i assume|probably|presumably|likely|guess)\b|\bdon'?t know\b/i;

// ── Detector registry — ONLY mechanically-checkable patterns live here. ───────
// Semantic patterns (scope_misunderstanding, …) intentionally stay soft-only when
// they cannot be detected precisely. incomplete_analysis has a high-precision
// subset detector (agreement/dismissal without research) as of 2026-07-10.
const DETECTORS: Record<string, Detector> = {
  unverified_completion: {
    defaultMode: 'block',
    detect: (r) => {
      const claim = COMPLETION_CLAIM.test(r);
      if (claim && !hasStrongArtifact(r)) {
        return "Response claims completion (done/fixed/complete/picture is complete) without STRONG paper trace. " +
               "Bare file paths are not enough. Show CLI/test output in a code fence, exit codes, pass counts, or a live URL — " +
               "or revise the claim to what is still unverified.";
      }
      return null;
    },
  },
  // High-precision incomplete_analysis (regressed 2026-07-09). Soft full-semantic
  // form stays out; these two shapes have high precision in ratings history.
  incomplete_analysis: {
    defaultMode: 'block',
    detect: (r) => {
      const agree =
        /\b(you'?re right|that'?s correct|same issue|same problem|that would also|agree with you|that makes sense|you'?re correct|that is correct|correct that)\b/i.test(r);
      const technical =
        /\b(won'?t work|will work|can'?t work|would work|would fail|would break|should work|can work|does work|doesn'?t work|same behavior|same result|same (vpn|network|auth|oauth|error|issue|problem))\b/i.test(r);
      const dismiss =
        /\b(looks? (unrelated|fine|ok|good)|not related|unrelated to (this|the)|doesn'?t (seem|look) related|no changes? needed|nothing to (do|fix|change)|out of scope for this)\b/i.test(r);
      const research =
        hasArtifact(r) ||
        /\b(I (read|checked|fetched|reviewed|inspected|opened|ran|compared|verified)|gh pr (view|diff|checks)|bq show|from the (diff|file|ticket|schema|comments?)|existing comments|full diff)\b/i.test(r) ||
        /```/.test(r);
      if (agree && technical && !research) {
        return "Response confidently agrees with a technical claim without a research/verification trace. " +
               "incomplete_analysis (regressed). Verify with a tool and cite output, or withhold agreement.";
      }
      if (dismiss && !research) {
        return "Response dismisses scope (unrelated/fine/no changes) without showing what was read. " +
               "incomplete_analysis (regressed). Re-read the full diff/comments/ticket and cite the trace first.";
      }
      return null;
    },
  },
  unverified_claims: {
    // 2026-07-09: promoted warn→block (hedge + confident-state are high precision;
    // adversarial LLM path remains fail-safe on errors).
    defaultMode: 'block',
    detect: (r) => {
      // 0. Confident system-state claim without strong artifact or epistemic tag
      if (CONFIDENT_STATE.test(r) && !hasStrongArtifact(r) && !TAGGED_UNCERTAIN.test(r)) {
        return "Response asserts system/external state with certainty but no STRONG paper trace " +
               "and no [INFERRED]/[GUESS]/unverified tag. Run a tool and cite output, or tag the claim.";
      }

      // 0b. Fabricated metrics / line refs / PR numbers without strong paper trace
      // Bare "5000 rows" or "bug on line 42" is a classic hallucination shape.
      const metricClaim =
        /\b(\d{2,}\s+(rows?|tests?|files?|bytes?|partitions?|columns?|records?)|PR\s*#?\d{2,}|pull\/\d{2,}|line\s+\d{2,}|exit\s+code\s+\d+|took\s+\d+(\.\d+)?\s*(ms|s|sec|seconds)|latency\s+(of\s+)?\d+)\b/i.test(r);
      if (metricClaim && !hasStrongArtifact(r) && !TAGGED_UNCERTAIN.test(r)) {
        return "Response cites a concrete metric/PR/line/exit code without STRONG paper trace. " +
               "Fence the tool output that produced that number, or tag [GUESS]/unverified.";
      }

      // 1. Fast synchronous regex check (hedge-words without any artifact)
      const hedge = /\b(i think|i believe|i assume|probably|should be|likely|presumably|must be)\b/i.test(r);
      if (hedge && !hasArtifact(r)) {
        return "Response hedges about system state ('I think'/'probably'/'should be') without verifying. " +
               "Verify with a tool (CLI/MCP/Read) and state the fact, or say explicitly it is unverified.";
      }

      // 2. Semantic LLM-in-the-loop — only when response looks technical (latency control)
      const looksTechnical =
        CONFIDENT_STATE.test(r) ||
        metricClaim ||
        /\b(schema|partition|deploy|merged|query|airflow|dataflow|bigquery|mysql|terraform|kubernetes|prod|uat|prd|error|fail|pass|bug|fix|verify|confirmed)\b/i.test(r);
      if (!looksTechnical) return null;

      try {
        // NOTE: must use pyenv's pinned 3.12.12, not bare 'python3'. Bare python3
        // resolves to Homebrew 3.14 (no `dotenv`/`rlm` deps installed there), which
        // silently falls into this script's fail-safe ImportError path and prints
        // APPROVE unconditionally — neutering this detector with no visible error.
        const stdout = runPythonHelper(
          'adversarial_claim_detector.py',
          r,
          20000, // measured live call latency ~9.2s; 4s guaranteed a timeout every call
        );

        if (stdout.startsWith('BLOCK:')) {
          return stdout.replace('BLOCK:', '').trim();
        }
      } catch (e) {
        // Fail-safe: do not block on subprocess errors
      }
      return null;
    },
  },
  duplicate_approval: {
    defaultMode: 'block',
    detect: (r) => {
      // Action-only: performing a second approval. Observing already APPROVED
      // or "skipping redundant approval" is CORRECT and must not fire
      // (roll_duplicate_approve_in 2026-07-09 false-positive).
      const dup =
        /\b(approved twice|twice approved|approved again|approving again|I (just )?approved( it| the pr)? again|approving (it |the pr )?again|re-?approved|approved (it |the pr )?just in case|I approved again|adding another approval|(add(ed|ing)?|left|submitted|gave|posted) (another|a second) approval)\b/i.test(r);
      if (dup) {
        return "Response claims it performed a second/duplicate PR approval. " +
               "If reviewDecision is already APPROVED, skip — do not approve again.";
      }
      return null;
    },
  },
  blind_retry: {
    defaultMode: 'warn',
    detect: (r) => {
      const retry = /\b(let me try again|retrying|running again|try once more|still fails|failed again|same error again|try that again)\b/i.test(r);
      const diagnosis = /\b(root cause|because|reason is|caused by|the error|the issue|the problem|traceback|error message|log shows|diagnos|investig)\b/i.test(r);
      if (retry && !diagnosis) {
        return "Response retries an action ('let me try again' / 'retrying') without providing a clear diagnosis. " +
               "Provide a root cause analysis of the prior failure first to avoid blind retrying.";
      }
      return null;
    },
  },
  tool_misuse: {
    defaultMode: 'block',
    detect: (r) => {
      const jiraMcp = /mcp__jira-context/i.test(r);
      if (jiraMcp) {
        return "CLAUDE.md mandates using the 'cli' CLI tool for all Jira operations. mcp__jira-context is explicitly forbidden.";
      }
      return null;
    },
  },
  // guardrail_bypass (Error 255): assistant identifies a mandatory check/guardrail
  // applies, then proceeds to skip it anyway -- most often correlated with the user
  // expressing impatience or urgency earlier in the turn. A guardrail that yields to
  // impatience provides no actual protection. Text-only heuristic: response names a
  // specific gating/check concept AND also contains skip/bypass/proceed-anyway language
  // in the same response. Deliberately narrow (two co-occurring signal classes) to keep
  // precision high and avoid false-firing on responses that correctly explain why a
  // check does NOT apply (which use similar vocabulary but without the bypass verb).
  guardrail_bypass: {
    defaultMode: 'block',
    detect: (r) => {
      const namesGuardrail = /\b(guardrail|mandatory check|required check|verification step|safety check|approval gate|blocking check|must (?:be )?(?:verif(?:y|ied)|complete(?:d)?|pass(?:ed)?) before)\b/i.test(r);
      const skipsAnyway = /\b(skip(?:ping|ped)? (?:it|this|that|the check|the step|the guardrail|the verification)|bypass(?:ing|ed)?|proceed(?:ing)? anyway|go(?:ing)? ahead (?:anyway|without)|without (?:waiting for|completing|running) (?:it|this|that|the check|the verification)|will not (?:wait|block)|skip(?:ping)? (?:the )?(?:wait|ordering|sequence))\b/i.test(r);
      if (namesGuardrail && skipsAnyway) {
        return "Response names a mandatory guardrail/check and also uses skip/bypass/proceed-anyway language in " +
               "the same turn. A guardrail that yields to time pressure or convenience provides no protection. " +
               "If the check genuinely does not apply here, say why explicitly instead of describing it as skipped. " +
               "If it does apply, it must be satisfied before proceeding, regardless of urgency.";
      }
      return null;
    },
  },
};

interface Config { enabled: boolean; overrides: Record<string, Mode>; }

function loadConfig(): Config {
  if (existsSync(CONFIG_JSON)) {
    try {
      const c = JSON.parse(readFileSync(CONFIG_JSON, 'utf-8'));
      return { enabled: c.enabled !== false, overrides: c.overrides || {} };
    } catch {}
  }
  // Seed a default config on first run so the user has a control surface.
  const def = {
    enabled: true,
    overrides: {},
    note: "armed = escalated patterns (effectiveness_scores.json) ∩ detectors. " +
          "Set overrides[pattern] to off|warn|block. enabled:false or env ENFORCEMENT_OFF=1 disables all.",
  };
  try { writeFileSync(CONFIG_JSON, JSON.stringify(def, null, 2)); } catch {}
  return { enabled: true, overrides: {} };
}

function loadEscalated(): string[] {
  try {
    if (existsSync(SCORES_JSON)) {
      return JSON.parse(readFileSync(SCORES_JSON, 'utf-8')).escalate || [];
    }
  } catch {}
  return [];
}

// ALWAYS_ON: structural gates evaluated every turn, independent of the
// effectiveness-loop escalate list (which is often empty). Not "graduated
// lessons" — standing behavioral guards.
// incomplete_analysis added 2026-07-10 after subjective regression (Δ=+0.148).
const ALWAYS_ON = ['unverified_completion', 'unverified_claims', 'incomplete_analysis', 'guardrail_bypass'];

// silent_completion (pattern 3): did the final turn use a tool? Heuristic =
// a tool_use marker in the transcript tail. String-search avoids newline-split
// escaping; 8KB tail ≈ the most recent turn. Any failure → false (fail-safe).
function lastTurnHadToolUse(transcriptPath?: string): boolean {
  try {
    if (!transcriptPath || !existsSync(transcriptPath)) return false;
    const tail = readFileSync(transcriptPath, 'utf-8').slice(-8000);
    return tail.includes('"type":"tool_use"') || tail.includes('"type": "tool_use"');
  } catch {}
  return false;
}

// graphiti_bypassed (CLAUDE.md: "ALWAYS use graphiti/Graphify/bungraph... BEFORE
// starting manual research or proposing changes"): count tool_use names in the
// CURRENT turn only (scanned from the last real user text prompt forward).
// 2026-07-09: default BLOCK; threshold lowered to 2 research calls so agents
// cannot do a multi-tool investigation without graph preflight.
const RESEARCH_TOOL_RE = /^(mcp__docs-search__|mcp__docs-mcp__search|mcp__mem0__search|mcp__mem0__get_memor|mcp__company-context__|mcp__bq-schema__|mcp__jira-context__|WebSearch|WebFetch|Grep|Glob|Task|Agent|search_tool|web_search|open_page|grep|find)/i;
const GRAPHITI_TOOL_RE = /^mcp__(graphiti-memory|bungraph)__/;
// Write-back tools (must call one after durable research findings)
const GRAPHITI_WRITE_RE = /^mcp__(graphiti-memory__add_memory|bungraph__add_episode|bungraph__add_triplet|bungraph__add_episode_bulk)/;
const RESEARCH_TOOL_THRESHOLD = 2; // >=2 research-tool calls this turn → must have graph query
// Durable-claim language that should be written back to graph memory
const DURABLE_CLAIM_RE = /\b(decided|decision|root cause|schema|partition|deployed|merged|infra-before-app|deploy order|do not|never |must |mandatory|always |prefer approved CLI|test_client_id|bronze|silver|gold|dataform|warehouse|datastream|scd2|promotion|PRD|UAT)\b/i;

function turnToolUseNames(transcriptPath?: string): string[] {
  try {
    if (!transcriptPath || !existsSync(transcriptPath)) return [];
    const lines = readFileSync(transcriptPath, 'utf-8').trim().split('\n');

    // Find the last REAL user prompt (string content, or array with a
    // non-empty text block) — tool_result entries are type='user' too but
    // carry no text block, so they don't reset the turn boundary.
    let lastHumanIndex = -1;
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      try {
        const entry = JSON.parse(lines[i]);
        if (entry.type === 'human' || entry.type === 'user') {
          const content = entry.message?.content;
          if (typeof content === 'string' && content.trim()) {
            lastHumanIndex = i;
          } else if (Array.isArray(content) && content.some((b: any) => b?.type === 'text' && b?.text?.trim())) {
            lastHumanIndex = i;
          }
        }
      } catch {}
    }

    const names: string[] = [];
    for (let i = lastHumanIndex + 1; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      try {
        const entry = JSON.parse(lines[i]);
        if (entry.type === 'assistant' && Array.isArray(entry.message?.content)) {
          for (const b of entry.message.content) {
            if (b?.type === 'tool_use' && typeof b.name === 'string') names.push(b.name);
          }
        }
      } catch {}
    }
    return names;
  } catch {}
  return [];
}

async function main() {
  if (process.env.ENFORCEMENT_OFF === '1') process.exit(0);

  const input = await readHookInput();
  if (!input) process.exit(0);

  const cfg = loadConfig();
  if (!cfg.enabled) process.exit(0);

  // armed = (escalated ∪ ALWAYS_ON) ∩ detectors. ALWAYS_ON keeps structural
  // gates active even when the effectiveness escalate list is empty.
  const escalate = loadEscalated();
  const armed = Array.from(new Set([...escalate, ...ALWAYS_ON]));

  let resp = input.last_assistant_message || '';
  if (!resp) {
    try { resp = (await parseTranscriptFromInput(input)).lastMessage || ''; } catch {}
  }

  const fires: { pattern: string; mode: Mode; reason: string }[] = [];

  // silent_completion: tool work but no user-visible summary.
  // 2026-07-09: default block — silent tool turns hide errors and invent status later.
  {
    const mode: Mode = cfg.overrides['silent_completion'] || 'block';
    if (mode !== 'off' && resp.trim().length < 15 && lastTurnHadToolUse((input as any).transcript_path)) {
      fires.push({
        pattern: 'silent_completion', mode,
        reason: 'Turn used tools but produced no user-visible summary. Emit one line: what changed + how it was verified.',
      });
    }
  }

  // graphiti_bypassed: CLAUDE.md mandates graphiti/bungraph BEFORE manual research.
  // Default BLOCK (2026-07-09) — override in enforcement_config.json if needed.
  {
    const mode: Mode = cfg.overrides['graphiti_bypassed'] || 'block';
    if (mode !== 'off') {
      const names = turnToolUseNames((input as any).transcript_path);
      const researchCount = names.filter(n => RESEARCH_TOOL_RE.test(n)).length;
      const usedGraphiti = names.some(n => GRAPHITI_TOOL_RE.test(n));
      if (!usedGraphiti && researchCount >= RESEARCH_TOOL_THRESHOLD) {
        fires.push({
          pattern: 'graphiti_bypassed', mode,
          reason: `Turn made ${researchCount} research/search tool calls without querying graphiti-memory or bungraph. ` +
                  `BLOCK: search graphiti-memory (search_memory_facts/search_nodes) or bungraph (search/search_facts) NOW, ` +
                  `apply any prior facts, then continue research. Write durable findings back when state changes.`,
        });
      }
    }
  }

  // graphiti_writeback_skipped: researched + durable claims but no add_memory/add_episode.
  // Default WARN (2026-07-09) — SessionEnd auto-seed is the safety net; promote to block if ignored.
  {
    const mode: Mode = cfg.overrides['graphiti_writeback_skipped'] || 'warn';
    if (mode !== 'off') {
      const names = turnToolUseNames((input as any).transcript_path);
      const researchCount = names.filter(n => RESEARCH_TOOL_RE.test(n)).length;
      const wroteGraph = names.some(n => GRAPHITI_WRITE_RE.test(n));
      const durable = DURABLE_CLAIM_RE.test(resp) && resp.trim().length > 200;
      if (!wroteGraph && researchCount >= RESEARCH_TOOL_THRESHOLD && durable) {
        fires.push({
          pattern: 'graphiti_writeback_skipped', mode,
          reason: `Turn did research (${researchCount} tools) and made durable claims but never called ` +
                  `graphiti-memory add_memory or bungraph add_episode/add_triplet. ` +
                  `Write durable findings back now (add_memory), or SessionEnd auto-seed will attempt a capture.`,
        });
      }
    }
  }

  // claim_evidence (strongest unverified-claims gate): block any concrete state-claim
  // not supported by THIS turn's tool outputs — catches confident, well-formed claims
  // the hedge-regex/adversarial check misses. LLM-in-the-loop; fail-safe on any error.
  {
    const mode: Mode = cfg.overrides['claim_evidence'] || 'block';
    if (mode !== 'off' && resp.trim().length > 0) {
      try {
        const payload = JSON.stringify({ response: resp, transcript_path: (input as any).transcript_path || '' });
        // Same pyenv note as adversarial_claim_detector.py above — bare python3 lacks
        // `dotenv`/`rlm` and silently fail-safes to APPROVE.
        const out = runPythonHelper('claim_evidence_verifier.py', payload, 30000)
          .replace(/^["']+|["']+$/g, '');
        if (out.startsWith('BLOCK:')) {
          fires.push({ pattern: 'claim_evidence', mode, reason: out.slice(6).trim() });
        }
      } catch {}
    }
  }

  // text-based detectors (need a response to match against)
  if (resp) {
    for (const pattern of armed) {
      const det = DETECTORS[pattern];
      if (!det) continue; // no detector → stays soft-only, not enforceable
      const mode: Mode = cfg.overrides[pattern] || det.defaultMode;
      if (mode === 'off') continue;
      const reason = det.detect(resp);
      if (reason) fires.push({ pattern, mode, reason });
    }
  }

  if (fires.length === 0) process.exit(0);

  const blocking = fires.filter(f => f.mode === 'block');
  const ts = new Date().toISOString();
  try {
    for (const f of fires) {
      appendFileSync(ENFORCE_LOG, JSON.stringify({
        ts, session: input.session_id, pattern: f.pattern,
        mode: f.mode, blocked: f.mode === 'block',
      }) + '\n');
    }
  } catch {}

  if (blocking.length > 0) {
    const reason = '⛔ ENFORCEMENT (graduated — this pattern kept failing despite a lesson):\n' +
      blocking.map(f => `• [${f.pattern}] ${f.reason}`).join('\n') +
      '\nFix this before ending your turn.';
    console.log(JSON.stringify({ decision: 'block', reason }));
    process.exit(0);
  }

  // warn-only fires: logged, non-blocking (Stop hooks can't inject post-stop context)
  process.exit(0);
}

main().catch(() => process.exit(0));
