/**
 * pai-learning-harness.ts — full PAI self-learning harness for pi
 *
 * Claude parity (FailurePatternReminder + RatingCapture + session signals):
 * - Explicit 1–10 + praise ratings → shared ratings.jsonl (agent: "pi")
 * - Skill attribution for skill_autofix (slash /skill:name, bare /slug)
 * - EVERY turn: ACE playbook bullets (relevance + effectiveness floor)
 * - Recent FAILURES/ + low-rating corrections
 * - Signal trends + pi-agent auto-learned guardrails
 * - Tool-use tracking for EnforcementGate (graphiti_bypassed, silent_completion)
 * - Last-response cache for rating context
 *
 * Shares MEMORY/* with Claude Code / Grok. SessionEnd loop runs via claude-bridge.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  existsSync,
  readFileSync,
  appendFileSync,
  mkdirSync,
  writeFileSync,
  readdirSync,
} from "node:fs";
import { join } from "node:path";

const HOME = process.env.HOME!;
const PAI_DIR = process.env.PAI_DIR || join(HOME, ".claude");
const SIGNALS_DIR = join(PAI_DIR, "MEMORY", "LEARNING", "SIGNALS");
const RATINGS_FILE = join(SIGNALS_DIR, "ratings.jsonl");
const LAST_RESPONSE_CACHE = join(PAI_DIR, "MEMORY", "STATE", "last-response.txt");
const TURN_STATE_FILE = join(PAI_DIR, "MEMORY", "STATE", "pi_turn_state.json");
const ACE_PLAYBOOK = join(PAI_DIR, "MEMORY", "STATE", "ace_playbook.json");
const SCORES_JSON = join(PAI_DIR, "MEMORY", "STATE", "effectiveness_scores.json");
const FAILURES_DIR = join(PAI_DIR, "MEMORY", "LEARNING", "FAILURES");
const LEARNING_DIR = join(PAI_DIR, "MEMORY", "LEARNING");
const MEMORY_DIR = join(PAI_DIR, "projects", "-USER-", "memory");
const PI_AGENT_SKILL = join(HOME, ".pi", "agent", "skills", "pi-agent", "SKILL.md");
const PI_SKILLS_DIR = join(HOME, ".pi", "agent", "skills");
const CLAUDE_COMMANDS = join(PAI_DIR, "commands");
const GRAPH_PREFLIGHT = join(PAI_DIR, "MEMORY", "STATE", "graph_preflight.md");
const ANTI_HALLUC = join(PAI_DIR, "MEMORY", "STATE", "anti_hallucination.md");
const ENFORCE_LOG = join(PAI_DIR, "MEMORY", "LEARNING", "enforcement_log.jsonl");

const STOPWORDS = new Set([
  "this", "that", "with", "from", "have", "will", "your", "what", "when", "where",
  "which", "about", "would", "could", "should", "there", "their", "then", "them",
  "they", "were", "been", "being", "into", "more", "some", "than", "also", "just",
  "like", "want", "need", "make", "made", "does", "done", "using", "use", "the",
  "and", "for",
]);

const VERDICT_WEIGHT: Record<string, number> = {
  regressed: 5,
  flat: 3,
  pending: 0,
  improving: -1,
  working: -2,
  resolved: -999,
};

interface Lesson {
  pattern: string;
  rule: string;
  text: string;
}

// ── FS helpers ──────────────────────────────────────────────────────────────

function ensureDir(dir: string): void {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

function writeRating(entry: Record<string, unknown>): void {
  ensureDir(SIGNALS_DIR);
  appendFileSync(RATINGS_FILE, JSON.stringify(entry) + "\n", "utf-8");
}

function writeTurnState(state: Record<string, unknown>): void {
  ensureDir(join(PAI_DIR, "MEMORY", "STATE"));
  writeFileSync(TURN_STATE_FILE, JSON.stringify(state), "utf-8");
}

// ── Rating detection (mirrors RatingCapture.hook.ts) ────────────────────────

function parseExplicitRating(
  prompt: string
): { rating: number; comment?: string } | null {
  const trimmed = prompt.trim();
  const match = trimmed.match(/^(10|[1-9])(?:\s*[-:]\s*|\s+)?(.*)$/);
  if (!match) return null;

  const rating = parseInt(match[1], 10);
  const rest = match[2]?.trim() || undefined;
  if (rating < 1 || rating > 10) return null;

  const afterNumber = trimmed.slice(match[1].length);
  if (afterNumber.length > 0 && /^[/.\dA-Za-z]/.test(afterNumber)) return null;

  if (rest) {
    const sentenceStarters =
      /^(items?|things?|steps?|files?|lines?|bugs?|issues?|errors?|times?|minutes?|hours?|days?|seconds?|percent|%|th\b|st\b|nd\b|rd\b|of\b|in\b|at\b|to\b|the\b|a\b|an\b)/i;
    if (sentenceStarters.test(rest)) return null;
  }

  return { rating, comment: rest };
}

const POSITIVE_PRAISE_WORDS = new Set([
  "excellent", "amazing", "brilliant", "fantastic", "wonderful", "beautiful",
  "incredible", "awesome", "perfect", "great", "nice", "superb", "outstanding",
  "magnificent", "stellar", "phenomenal", "remarkable", "terrific", "splendid",
  "exactly", "correct", "bingo",
]);
const POSITIVE_PHRASES = new Set([
  "great job", "good job", "nice work", "well done", "nice job", "good work",
  "love it", "nailed it", "looks great", "looks good", "thats great", "that works",
  "sounds good", "yes please", "thats it", "looks right", "makes sense",
  "spot on", "good catch", "bang on", "you nailed", "nice catch",
]);

function parsePositivePraise(prompt: string): number | null {
  const normalized = prompt.trim().toLowerCase().replace(/[.!?,'"]/g, "");
  const words = normalized.split(/\s+/);
  if (words.length > 3) return null;
  if (
    POSITIVE_PRAISE_WORDS.has(normalized) ||
    POSITIVE_PHRASES.has(normalized) ||
    (words.length === 2 && words.every((w) => POSITIVE_PRAISE_WORDS.has(w)))
  ) {
    return 8;
  }
  return null;
}

// ── Skill attribution ───────────────────────────────────────────────────────

function knownSkillNames(): Set<string> {
  const names = new Set<string>();
  try {
    if (existsSync(CLAUDE_COMMANDS)) {
      for (const f of readdirSync(CLAUDE_COMMANDS)) {
        if (f.endsWith(".md")) names.add(f.replace(/\.md$/, ""));
      }
    }
  } catch {}
  try {
    if (existsSync(PI_SKILLS_DIR)) {
      for (const ent of readdirSync(PI_SKILLS_DIR, { withFileTypes: true })) {
        if (ent.isDirectory()) names.add(ent.name);
        else if (ent.isFile() && ent.name.endsWith(".md")) {
          names.add(ent.name.replace(/\.md$/, ""));
        }
      }
    }
  } catch {}
  names.add("pi-agent");
  names.add("self-improve");
  return names;
}

function extractSkill(prompt: string): string {
  const skills = new Set<string>();
  const known = knownSkillNames();

  // /skill:name (pi skill command)
  for (const m of prompt.matchAll(/\/skill:([a-z][\w-]*)/gi)) {
    skills.add(m[1].toLowerCase());
  }
  // bare /slug when known
  for (const m of prompt.matchAll(/(?:^|\s)\/([a-z][\w-]{1,40})\b/gi)) {
    const slug = m[1].toLowerCase();
    if (known.has(slug) || known.size === 0) skills.add(slug);
  }
  // "use skill X"
  for (const m of prompt.matchAll(/\b(?:skill|command)\s+["'`]?([a-z][\w-]*)/gi)) {
    skills.add(m[1].toLowerCase());
  }

  // Default pi-agent so skill_autofix has a real surface (SKILL.md with markers)
  if (skills.size === 0) return "pi-agent";
  const arr = [...skills];
  return arr.find((s) => s !== "pi-agent" && s !== "general-session") || arr[0];
}

// ── ACE / FailurePatternReminder parity ─────────────────────────────────────

function tokenize(s: string): Set<string> {
  return new Set(
    (s.toLowerCase().match(/[a-z]+/g) || []).filter(
      (w) => w.length > 3 && !STOPWORDS.has(w)
    )
  );
}

function loadLessons(): Lesson[] {
  if (existsSync(ACE_PLAYBOOK)) {
    try {
      const pb = JSON.parse(readFileSync(ACE_PLAYBOOK, "utf-8"));
      const bullets = Array.isArray(pb.bullets) ? pb.bullets : [];
      if (bullets.length > 0) {
        return bullets.map(
          (b: { pattern?: string; description?: string; id?: string }) => {
            const pattern = (b.pattern || b.id || "unknown").toString();
            const rule = (b.description || "").toString();
            return {
              pattern,
              rule,
              text: pattern.replace(/_/g, " ") + " " + rule,
            };
          }
        );
      }
    } catch {}
  }

  const out: Lesson[] = [];
  if (!existsSync(MEMORY_DIR)) return out;
  try {
    for (const f of readdirSync(MEMORY_DIR).filter((n) =>
      /^lesson_autogen_.*\.md$/.test(n)
    )) {
      const pattern = f.replace(/^lesson_autogen_/, "").replace(/\.md$/, "");
      const content = readFileSync(join(MEMORY_DIR, f), "utf-8");
      const body = content.split(/\n---\n/).slice(1).join("\n---\n") || content;
      const rule = (
        body
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l && !l.startsWith("**")) || ""
      ).trim();
      out.push({
        pattern,
        rule,
        text: pattern.replace(/_/g, " ") + " " + rule,
      });
    }
  } catch {}
  return out;
}

function loadEffectiveness(): { scores: Record<string, { verdict: string }> } {
  try {
    if (existsSync(SCORES_JSON)) {
      return JSON.parse(readFileSync(SCORES_JSON, "utf-8"));
    }
  } catch {}
  return { scores: {} };
}

function rankLessons(prompt: string, limit: number): Lesson[] {
  const ptoks = tokenize(prompt);
  if (ptoks.size === 0) return [];
  const eff = loadEffectiveness();
  const scored = loadLessons()
    .map((l) => {
      const ltoks = tokenize(l.text);
      let overlap = 0;
      for (const t of ptoks) if (ltoks.has(t)) overlap++;
      const verdict = eff.scores[l.pattern]?.verdict || "pending";
      const weight = VERDICT_WEIGHT[verdict] ?? 0;
      const score = overlap > 0 ? overlap + weight : -1000;
      return { lesson: l, score };
    })
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, limit).map((s) => s.lesson);
}

function getWorstLessons(limit: number): Lesson[] {
  const eff = loadEffectiveness();
  const PRIORITY: Record<string, number> = { regressed: 0, flat: 1, pending: 2 };
  return loadLessons()
    .filter((l) => {
      const v = eff.scores[l.pattern]?.verdict || "pending";
      return v in PRIORITY;
    })
    .sort((a, b) => {
      const va = eff.scores[a.pattern]?.verdict || "pending";
      const vb = eff.scores[b.pattern]?.verdict || "pending";
      return (PRIORITY[va] ?? 99) - (PRIORITY[vb] ?? 99);
    })
    .slice(0, limit);
}

function getRecentFailureSlugs(count: number): string[] {
  const patterns: string[] = [];
  if (!existsSync(FAILURES_DIR)) return patterns;
  try {
    const months = readdirSync(FAILURES_DIR, { withFileTypes: true })
      .filter((d) => d.isDirectory() && /^\d{4}-\d{2}$/.test(d.name))
      .map((d) => d.name)
      .sort()
      .reverse();

    for (const month of months) {
      if (patterns.length >= count) break;
      const dirs = readdirSync(join(FAILURES_DIR, month), { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => d.name)
        .sort()
        .reverse();
      for (const dir of dirs) {
        if (patterns.length >= count) break;
        const dateMatch = dir.match(/^(\d{4}-\d{2}-\d{2})/);
        const date = dateMatch ? dateMatch[1] : "";
        const slug = dir
          .replace(/^\d{4}-\d{2}-\d{2}-\d{6}_/, "")
          .replace(/-/g, " ");
        patterns.push(`[${date}] ${slug.substring(0, 70)}`);
      }
    }
  } catch {}
  return patterns;
}

function getRecentLowRatingCorrections(count: number): string[] {
  const corrections: string[] = [];
  for (const subdir of ["ALGORITHM", "SYSTEM"]) {
    if (corrections.length >= count) break;
    const learningDir = join(LEARNING_DIR, subdir);
    if (!existsSync(learningDir)) continue;
    try {
      const months = readdirSync(learningDir, { withFileTypes: true })
        .filter((d) => d.isDirectory() && /^\d{4}-\d{2}$/.test(d.name))
        .map((d) => d.name)
        .sort()
        .reverse();
      for (const month of months) {
        if (corrections.length >= count) break;
        const files = readdirSync(join(learningDir, month))
          .filter((f) => f.endsWith(".md"))
          .sort()
          .reverse();
        for (const file of files) {
          if (corrections.length >= count) break;
          try {
            const content = readFileSync(
              join(learningDir, month, file),
              "utf-8"
            );
            const ratingMatch = content.match(/rating:\s*(\d+)/);
            const feedbackMatch = content.match(/\*\*Feedback:\*\*\s*(.+)/);
            if (ratingMatch && feedbackMatch) {
              const rating = parseInt(ratingMatch[1], 10);
              if (rating <= 4) {
                corrections.push(
                  `[${rating}/10] ${feedbackMatch[1].substring(0, 80)}`
                );
              }
            }
          } catch {}
        }
      }
    } catch {}
  }
  return corrections;
}

function loadSignalTrends(): string | null {
  if (!existsSync(RATINGS_FILE)) return null;
  const lines = readFileSync(RATINGS_FILE, "utf-8")
    .trim()
    .split("\n")
    .filter(Boolean);
  if (lines.length === 0) return null;

  const now = Date.now();
  const DAY = 24 * 60 * 60 * 1000;
  const WEEK = 7 * DAY;
  const MONTH = 30 * DAY;
  const today: number[] = [];
  const week: number[] = [];
  const month: number[] = [];
  let total = 0;
  let piTotal = 0;

  for (const line of lines) {
    try {
      const entry = JSON.parse(line);
      if (typeof entry.rating !== "number") continue;
      total++;
      if (entry.agent === "pi") piTotal++;
      const age = now - new Date(entry.timestamp).getTime();
      if (age < DAY) today.push(entry.rating);
      if (age < WEEK) week.push(entry.rating);
      if (age < MONTH) month.push(entry.rating);
    } catch {}
  }

  const avg = (arr: number[]) =>
    arr.length
      ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1)
      : "N/A";
  const weekAvg = week.length ? parseFloat(avg(week)) : null;
  const monthAvg = month.length ? parseFloat(avg(month)) : null;
  const trend =
    weekAvg && monthAvg
      ? weekAvg > monthAvg + 0.2
        ? "improving"
        : weekAvg < monthAvg - 0.2
          ? "declining"
          : "stable"
      : "stable";

  return `**Performance Signals:** Today: ${avg(today)} | Week: ${avg(week)} | Month: ${avg(month)} | Trend: ${trend} | Total: ${total} (pi: ${piTotal})`;
}

function loadPiAgentGuardrails(): string | null {
  if (!existsSync(PI_AGENT_SKILL)) return null;
  try {
    const text = readFileSync(PI_AGENT_SKILL, "utf-8");
    const start = "<!-- AUTO-LEARNED-GUARDRAILS:start -->";
    const end = "<!-- AUTO-LEARNED-GUARDRAILS:end -->";
    const i = text.indexOf(start);
    const j = text.indexOf(end);
    if (i < 0 || j < 0 || j <= i) return null;
    const block = text.slice(i + start.length, j).trim();
    if (!block) return null;
    return `**Pi-agent auto-learned guardrails:**\n${block}`;
  } catch {
    return null;
  }
}

function loadGraphPreflight(): string | null {
  try {
    if (!existsSync(GRAPH_PREFLIGHT)) return null;
    const body = readFileSync(GRAPH_PREFLIGHT, "utf-8").trim();
    if (!body) return null;
    let extra = "";
    try {
      if (existsSync(SCORES_JSON)) {
        const sc = JSON.parse(readFileSync(SCORES_JSON, "utf-8"));
        const esc = Array.isArray(sc.escalate) ? sc.escalate : [];
        if (esc.length) {
          extra = `\n**Active escalated patterns (search graph for these):** ${esc.join(", ")}`;
        }
      }
    } catch {}
    return (
      "**Graph memory preflight (mandatory before broad research):**\n" +
      body.slice(0, 1800) +
      extra +
      "\n→ graphiti_bypassed is BLOCK if ≥2 research tools and zero graphiti/bungraph calls.\n" +
      "Retrieval SOP: graphiti/bungraph → code/schema → scrum → web (see MEMORY/STATE/retrieval_sop.md).\n" +
      "Model tiering: default cheap/mid; high only for design/high-blast (MEMORY/STATE/model_tiering.md)."
    );
  } catch {
    return null;
  }
}

function buildFailurePatternReminder(prompt: string): string | null {
  const relevant = rankLessons(prompt, 3);
  const worstLessons = getWorstLessons(2);
  const relevantPatterns = new Set(relevant.map((l) => l.pattern));
  const floor = worstLessons.filter((l) => !relevantPatterns.has(l.pattern));
  const allLessons = [...relevant, ...floor];
  const failures = getRecentFailureSlugs(allLessons.length > 0 ? 3 : 5);
  const corrections = getRecentLowRatingCorrections(
    allLessons.length > 0 ? 2 : 3
  );
  const trends = loadSignalTrends();
  const piGuardrails = loadPiAgentGuardrails();
  const graphPreflight = loadGraphPreflight();

  if (
    allLessons.length === 0 &&
    failures.length === 0 &&
    corrections.length === 0 &&
    !trends &&
    !piGuardrails &&
    !graphPreflight
  ) {
    return null;
  }

  const lines: string[] = [
    "⛔ FAILURE PATTERN REMINDER — check these before responding:",
  ];

  if (graphPreflight) {
    lines.push(graphPreflight);
  }

  if (relevant.length > 0) {
    lines.push("Lessons relevant to THIS task (highest priority):");
    relevant.forEach((l) =>
      lines.push(`  ◆ [${l.pattern}] ${l.rule.substring(0, 140)}`)
    );
  }
  if (floor.length > 0) {
    lines.push(
      "Always-on (worst-performing patterns, injected regardless of task):"
    );
    floor.forEach((l) =>
      lines.push(`  ◆ [${l.pattern}] ${l.rule.substring(0, 140)}`)
    );
  }
  if (failures.length > 0) {
    lines.push("Recent failures (do NOT repeat):");
    failures.forEach((f) => lines.push(`  • ${f}`));
  }
  if (corrections.length > 0) {
    lines.push("Recent low-rating corrections:");
    corrections.forEach((c) => lines.push(`  • ${c}`));
  }
  if (trends) lines.push(trends);
  if (piGuardrails) lines.push(piGuardrails);
  // Recent enforcement blocks
  try {
    if (existsSync(ENFORCE_LOG)) {
      const linesLog = readFileSync(ENFORCE_LOG, "utf-8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .slice(-40);
      const blocked = new Map<string, number>();
      for (const ln of linesLog) {
        try {
          const e = JSON.parse(ln);
          if (e.blocked || e.mode === "block") {
            const p = String(e.pattern || "unknown");
            blocked.set(p, (blocked.get(p) || 0) + 1);
          }
        } catch {}
      }
      if (blocked.size > 0) {
        const top = [...blocked.entries()]
          .sort((a, b) => b[1] - a[1])
          .slice(0, 5);
        lines.push("Recent ENFORCEMENT blocks (do NOT repeat):");
        top.forEach(([p, n]) => lines.push(`  ⛔ ${p} ×${n}`));
      }
    }
  } catch {}

  try {
    if (existsSync(ANTI_HALLUC)) {
      const brief = readFileSync(ANTI_HALLUC, "utf-8")
        .trim()
        .split("\n")
        .filter((l) => l && !l.startsWith("#") && !l.startsWith("*"))
        .slice(0, 8)
        .map((l) => `  ${l}`)
        .join("\n");
      if (brief) {
        lines.push("Anti-hallucination brief:");
        lines.push(brief);
      }
    }
  } catch {}

  lines.push("Anti-hallucination (always-on):");
  lines.push(
    "  • State/schema/CI/PR/row claims → tool first, then claim (or tag [GUESS]/unverified)."
  );
  lines.push(
    "  • done/fixed/complete → STRONG paper trace only (fence CLI/test + tool name, URL). Paths + bare N-rows fail."
  );
  lines.push(
    "  • Invented metrics/PR#/line numbers without fence = block."
  );
  lines.push(
    "  • graphiti/bungraph before ≥2 research tools; write durable findings back."
  );
  lines.push(
    "  • No silent tool turns — always emit a one-line user-visible summary."
  );
  lines.push(
    "EPISTEMIC: TAG claims [KNOWN]·[COMPUTED]·[INFERRED]·[COMMON]·[FRAME]·[GUESS]. " +
      "No untagged system-state. DON'T KNOW first. No fabricated citations."
  );
  lines.push(
    "→ If this task touches any of the above patterns, explicitly state how you will avoid repeating them."
  );

  return "\n\n## PAI Learning Context (pi parity — every turn)\n\n" + lines.join("\n");
}

// ── Extension ───────────────────────────────────────────────────────────────

export default function paiLearningHarness(pi: ExtensionAPI) {
  // Per-turn tool names + evidence snippets for EnforcementGate claim_evidence
  let turnToolNames: string[] = [];
  let turnEvidence: string[] = [];
  let lastPromptSkill = "pi-agent";
  let lastPrompt = "";

  pi.on("session_start", async (_event, ctx) => {
    turnToolNames = [];
    turnEvidence = [];
    writeTurnState({ tools: [], evidence: "", skill: "pi-agent", prompt: "" });
    try {
      ctx.ui.setStatus("pai-learn", "PAI learning: on");
    } catch {}
  });

  pi.on("before_agent_start", async (event) => {
    const prompt = (event as any).prompt || "";
    lastPrompt = prompt;
    lastPromptSkill = extractSkill(prompt);
    turnToolNames = []; // reset each user prompt
    turnEvidence = [];
    writeTurnState({
      tools: [],
      evidence: "",
      skill: lastPromptSkill,
      prompt: prompt.slice(0, 500),
      ts: new Date().toISOString(),
    });

    // 1. Explicit rating
    const explicit = parseExplicitRating(prompt);
    if (explicit) {
      let responsePreview = "";
      try {
        if (existsSync(LAST_RESPONSE_CACHE)) {
          responsePreview = readFileSync(LAST_RESPONSE_CACHE, "utf-8").slice(
            0,
            500
          );
        }
      } catch {}
      writeRating({
        timestamp: new Date().toISOString(),
        rating: explicit.rating,
        source: "explicit",
        agent: "pi",
        skill: lastPromptSkill,
        ...(explicit.comment ? { comment: explicit.comment } : {}),
        ...(responsePreview
          ? { response_preview: responsePreview }
          : {}),
      });
      console.error(
        `[pai-learning] Explicit rating ${explicit.rating}/10 skill=${lastPromptSkill} → ratings.jsonl`
      );
      return {};
    }

    // 2. Praise
    const praiseRating = parsePositivePraise(prompt);
    if (praiseRating !== null) {
      writeRating({
        timestamp: new Date().toISOString(),
        rating: praiseRating,
        source: "implicit",
        agent: "pi",
        skill: lastPromptSkill,
        sentiment_summary: `Direct praise: "${prompt.trim().slice(0, 60)}"`,
        confidence: 0.9,
      });
      console.error(
        `[pai-learning] Implicit praise ${praiseRating}/10 skill=${lastPromptSkill}`
      );
      return {};
    }

    // 3. FailurePatternReminder + ACE — EVERY turn (Claude parity)
    const learningContext = buildFailurePatternReminder(prompt);
    if (learningContext) {
      console.error(
        `[pai-learning] Injecting ACE/reminder (${learningContext.length} chars) skill=${lastPromptSkill}`
      );
      return {
        systemPrompt: (event as any).systemPrompt + learningContext,
      };
    }

    return {};
  });

  pi.on("tool_call", async (event) => {
    const name = (event as any).toolName || "";
    if (name) {
      turnToolNames.push(name);
      writeTurnState({
        tools: turnToolNames,
        evidence: turnEvidence.join("\n---\n").slice(-12000),
        skill: lastPromptSkill,
        prompt: lastPrompt.slice(0, 500),
        ts: new Date().toISOString(),
      });
    }
    return {};
  });

  // Capture tool results when the host emits them (for claim_evidence grounding)
  pi.on("tool_result", async (event) => {
    try {
      const ev: any = event;
      const name = ev.toolName || ev.name || "";
      let text = "";
      const r = ev.result ?? ev.output ?? ev.content ?? ev;
      if (typeof r === "string") text = r;
      else if (r != null) text = JSON.stringify(r);
      if (text.trim()) {
        turnEvidence.push(
          (name ? `[${name}] ` : "") + text.slice(0, 2000)
        );
        // Cap total stored evidence
        if (turnEvidence.length > 40) turnEvidence = turnEvidence.slice(-40);
        writeTurnState({
          tools: turnToolNames,
          evidence: turnEvidence.join("\n---\n").slice(-12000),
          skill: lastPromptSkill,
          prompt: lastPrompt.slice(0, 500),
          ts: new Date().toISOString(),
        });
      }
    } catch {}
    return {};
  });

  pi.on("agent_end", async (event) => {
    try {
      const messages: any[] = (event as any).messages || [];
      const lastAssistant = [...messages]
        .reverse()
        .find((m: any) => m.role === "assistant");
      if (!lastAssistant) return;

      const content = lastAssistant.content;
      const text =
        typeof content === "string"
          ? content
          : Array.isArray(content)
            ? content
                .filter((c: any) => c.type === "text")
                .map((c: any) => c.text || "")
                .join(" ")
            : "";

      if (text.length > 50) {
        ensureDir(join(PAI_DIR, "MEMORY", "STATE"));
        writeFileSync(LAST_RESPONSE_CACHE, text.slice(0, 3000), "utf-8");
      }

      // Persist tools + evidence for enforcement gate (agent_end order may race)
      writeTurnState({
        tools: turnToolNames,
        evidence: turnEvidence.join("\n---\n").slice(-12000),
        skill: lastPromptSkill,
        prompt: lastPrompt.slice(0, 500),
        response_len: text.length,
        ts: new Date().toISOString(),
      });
    } catch {}
  });

  // Operator surface: /self-improve status command (pi native)
  pi.registerCommand("self-improve", {
    description:
      "Show PAI self-improvement harness status (shared with Claude/Grok)",
    handler: async (_args, ctx) => {
      const parts: string[] = ["# PAI self-improve status (pi)"];
      try {
        if (existsSync(ACE_PLAYBOOK)) {
          const pb = JSON.parse(readFileSync(ACE_PLAYBOOK, "utf-8"));
          parts.push(
            `ACE bullets: ${pb.bullet_count ?? (pb.bullets || []).length} (generated ${pb.generated_at || "?"})`
          );
        } else {
          parts.push("ACE playbook: missing");
        }
      } catch {
        parts.push("ACE playbook: unreadable");
      }
      try {
        if (existsSync(SCORES_JSON)) {
          const sc = JSON.parse(readFileSync(SCORES_JSON, "utf-8"));
          const escalate = sc.escalate || [];
          parts.push(`Escalated patterns: ${escalate.length}`);
          if (escalate.length) {
            parts.push("  " + escalate.slice(0, 8).join(", "));
          }
        }
      } catch {}
      try {
        if (existsSync(RATINGS_FILE)) {
          const n = readFileSync(RATINGS_FILE, "utf-8")
            .trim()
            .split("\n")
            .filter(Boolean).length;
          parts.push(`Ratings signals: ${n}`);
        }
      } catch {}
      parts.push(
        "Loop: SessionEnd via claude-bridge → claude-session-end (self_harness + skill_autofix)."
      );
      parts.push(
        "CLI: cd ~/.claude/MEMORY/LEARNING && pyenv exec python3 self_harness.py --gate"
      );
      ctx.ui.notify(parts.join("\n"), "info");
    },
  });
}
