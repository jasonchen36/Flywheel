/**
 * pai-enforcement-gate.ts — EnforcementGate parity for pi (Stop equivalent)
 *
 * Claude's EnforcementGate fires on Stop and can block ending the turn.
 * Pi has no Stop hook; this fires on agent_end and, on block-mode fires,
 * injects a follow-up user message that forces a fix turn (sendUserMessage).
 *
 * Same config, detectors, ALWAYS_ON, and enforcement_log.jsonl as Claude.
 * Kill switch: ENFORCEMENT_OFF=1 or enforcement_config.json enabled:false
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  existsSync,
  readFileSync,
  writeFileSync,
  appendFileSync,
  mkdirSync,
} from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const HOME = process.env.HOME!;
const PAI_DIR = process.env.HARNESS_HOME || process.env.PAI_DIR || join(HOME, ".claude");
const SCORES_JSON = join(PAI_DIR, "MEMORY", "STATE", "effectiveness_scores.json");
const CONFIG_JSON = join(PAI_DIR, "MEMORY", "STATE", "enforcement_config.json");
const ENFORCE_LOG = join(PAI_DIR, "MEMORY", "LEARNING", "enforcement_log.jsonl");
const TURN_STATE_FILE = join(PAI_DIR, "MEMORY", "STATE", "pi_turn_state.json");

function runPythonHelper(scriptName: string, input: string, timeout: number): string {
  const script = join(PAI_DIR, "tools", scriptName);
  return execFileSync("pyenv", ["exec", "python3", script], {
    input,
    timeout,
    encoding: "utf-8",
    stdio: ["pipe", "pipe", "ignore"],
  }).trim();
}

type Mode = "off" | "warn" | "block";
interface Detector {
  defaultMode: Mode;
  detect: (resp: string) => string | null;
}

// Weak (hedges): path/filename OK. Strong (completion): fence/CLI/URL required.
// Mirrors EnforcementGate.hook.ts — 2026-07 regression fix (path-only false positives).
function hasWeakArtifact(r: string): boolean {
  if (r.includes("```")) return true;
  if (/https?:\/\/\S+/.test(r)) return true;
  if (/(?:[\w-]+\/)+[\w.-]+/.test(r)) return true;
  if (
    /\b[\w-]+\.(py|ts|tsx|js|jsx|sh|md|json|ya?ml|sql|sqlx|go|tf|java|rb|txt|csv)\b/i.test(
      r
    )
  )
    return true;
  if (/\bEXIT[: ]|\bexit code\b|\bPASS(ED)?\b|\bFAIL(ED)?\b/i.test(r))
    return true;
  // Bare N-rows is NOT artifact (2026-07-09b)
  if (
    /\b\d+\s+(tests?|rows?|files?|passed|failed|matches)\b/i.test(r) &&
    (r.includes("```") || /\b(EXIT|PASS|FAIL|pytest|rtk |\$ )/i.test(r))
  )
    return true;
  return false;
}

function hasStrongArtifact(r: string): boolean {
  if (r.includes("```") && r.split("```").length >= 3) return true;
  if (/https?:\/\/\S+/.test(r)) return true;
  if (/\bEXIT[: ]\s*\d|\bexit code\s*[=:]?\s*\d/i.test(r)) return true;
  if (
    /\b\d+\s+(passed|failed)\b/i.test(r) &&
    /\b(pytest|jest|mocha|unittest|go test|tests?\b|rtk |\$ )/i.test(r)
  )
    return true;
  if (
    /\b\d+\s+(tests?|rows?|files?|matches)\b/i.test(r) &&
    /\b(pytest|rtk |\$ |bq |gh |exit|stdout|output|evidence:|proof:|verified (via|with|by))\b/i.test(
      r
    )
  )
    return true;
  if (
    /\b(verified (via|with|by)|proof:|evidence:|dry[- ]?run|bq query|pytest|rtk |\$ )\b/i.test(
      r
    ) &&
    (/\b(output|stdout|result|shows?|passed|failed|exit)\b/i.test(r) ||
      r.includes("```"))
  ) {
    return true;
  }
  return false;
}

function hasArtifact(r: string): boolean {
  return hasWeakArtifact(r);
}

const COMPLETION_CLAIM =
  /\b(done|complete|completed|fixed|merged|posted|deployed|shipped|finished|approved)\b|\b(picture is complete|full picture|now (have|the) complete|work is complete|everything is complete|analysis is complete|should be complete|marked (as )?complete)\b/i;

// Confident absolute claims about system/external state (anti-hallucination 2026-07-09).
const CONFIDENT_STATE =
  /\b(the (table|column|schema|partition|job|dag|pipeline|pr|check|ci|deployment|cluster|dataset|view|topic|subscription) (is|are|has|have|exists?|contains?|passed|failed|running|green|healthy|empty|missing)|column exists|partition(ed)? by|in production|already (deployed|merged|approved)|checks? (have )?passed|ci is green|schema shows|row count is|there (is|are) \d+|pr is (green|clean|approved|mergeable)|all (tests|checks) pass(ed)?|verified that|confirmed that|the (bug|issue|error) is (fixed|gone|resolved))\b/i;
const TAGGED_UNCERTAIN =
  /\[(INFERRED|GUESS|FRAME|UNKNOWN|VLOW)\]|haven'?t verified|not (yet )?verified|unverified|i (have )?(not |n'?t )?(checked|verified|run|confirmed)|\b(i think|i believe|i assume|probably|presumably|likely|guess)\b|\bdon'?t know\b/i;

const DETECTORS: Record<string, Detector> = {
  unverified_completion: {
    defaultMode: "block",
    detect: (r) => {
      const claim = COMPLETION_CLAIM.test(r);
      if (claim && !hasStrongArtifact(r)) {
        return (
          "Response claims completion (done/fixed/complete/picture is complete) without STRONG paper trace. " +
          "Bare file paths are not enough. Show CLI/test output in a code fence, exit codes, pass counts, or a live URL — " +
          "or revise the claim to what is still unverified."
        );
      }
      return null;
    },
  },

  // High-precision incomplete_analysis (regressed 2026-07-09; always-on 2026-07-10).
  // Mirrors Claude EnforcementGate.hook.ts — keep in lockstep.
  incomplete_analysis: {
    defaultMode: "block",
    detect: (r) => {
      const agree =
        /\b(you'?re right|that'?s correct|same issue|same problem|that would also|agree with you|that makes sense|you'?re correct|that is correct|correct that)\b/i.test(
          r
        );
      const technical =
        /\b(won'?t work|will work|can'?t work|would work|would fail|would break|should work|can work|does work|doesn'?t work|same behavior|same result|same (vpn|network|auth|oauth|error|issue|problem))\b/i.test(
          r
        );
      const dismiss =
        /\b(looks? (unrelated|fine|ok|good)|not related|unrelated to (this|the)|doesn'?t (seem|look) related|no changes? needed|nothing to (do|fix|change)|out of scope for this)\b/i.test(
          r
        );
      const research =
        hasArtifact(r) ||
        /\b(I (read|checked|fetched|reviewed|inspected|opened|ran|compared|verified)|gh pr (view|diff|checks)|bq show|from the (diff|file|ticket|schema|comments?)|existing comments|full diff)\b/i.test(
          r
        ) ||
        /```/.test(r);
      if (agree && technical && !research) {
        return (
          "Response confidently agrees with a technical claim without a research/verification trace. " +
          "incomplete_analysis (regressed). Verify with a tool and cite output, or withhold agreement."
        );
      }
      if (dismiss && !research) {
        return (
          "Response dismisses scope (unrelated/fine/no changes) without showing what was read. " +
          "incomplete_analysis (regressed). Re-read the full diff/comments/ticket and cite the trace first."
        );
      }
      return null;
    },
  },

  unverified_claims: {
    // 2026-07-09: promoted warn→block; 2026-07-09b metric/line claims
    defaultMode: "block",
    detect: (r) => {
      if (
        CONFIDENT_STATE.test(r) &&
        !hasStrongArtifact(r) &&
        !TAGGED_UNCERTAIN.test(r)
      ) {
        return (
          "Response asserts system/external state with certainty but no STRONG paper trace " +
          "and no [INFERRED]/[GUESS]/unverified tag. Run a tool and cite output, or tag the claim."
        );
      }
      const metricClaim =
        /\b(\d{2,}\s+(rows?|tests?|files?|bytes?|partitions?|columns?|records?)|PR\s*#?\d{2,}|pull\/\d{2,}|line\s+\d{2,}|exit\s+code\s+\d+|took\s+\d+(\.\d+)?\s*(ms|s|sec|seconds)|latency\s+(of\s+)?\d+)\b/i.test(
          r
        );
      if (
        metricClaim &&
        !hasStrongArtifact(r) &&
        !TAGGED_UNCERTAIN.test(r)
      ) {
        return (
          "Response cites a concrete metric/PR/line/exit code without STRONG paper trace. " +
          "Fence the tool output that produced that number, or tag [GUESS]/unverified."
        );
      }
      const hedge =
        /\b(i think|i believe|i assume|probably|should be|likely|presumably|must be)\b/i.test(
          r
        );
      if (hedge && !hasArtifact(r)) {
        return (
          "Response hedges about system state ('I think'/'probably'/'should be') without verifying. " +
          "Verify with a tool (CLI/MCP/Read) and state the fact, or say explicitly it is unverified."
        );
      }
      const looksTechnical =
        CONFIDENT_STATE.test(r) ||
        metricClaim ||
        /\b(schema|partition|deploy|merged|query|airflow|dataflow|bigquery|mysql|terraform|kubernetes|prod|uat|prd|error|fail|pass|bug|fix|verify|confirmed)\b/i.test(
          r
        );
      if (!looksTechnical) return null;
      try {
        const stdout = runPythonHelper(
          "adversarial_claim_detector.py",
          r,
          20000
        );
        if (stdout.startsWith("BLOCK:")) {
          return stdout.replace("BLOCK:", "").trim();
        }
      } catch {
        // fail-safe
      }
      return null;
    },
  },
  duplicate_approval: {
    defaultMode: "block",
    detect: (r) => {
      // Action-only: performing a second approval. Observing already APPROVED
      // or "skipping redundant approval" is CORRECT and must not fire
      // (roll_duplicate_approve_in 2026-07-09 false-positive).
      const dup =
        /\b(approved twice|twice approved|approved again|approving again|I (just )?approved( it| the pr)? again|approving (it |the pr )?again|re-?approved|approved (it |the pr )?just in case|I approved again|adding another approval|(add(ed|ing)?|left|submitted|gave|posted) (another|a second) approval)\b/i.test(
          r
        );
      if (dup) {
        return (
          "Response claims it performed a second/duplicate PR approval. " +
          "If reviewDecision is already APPROVED, skip — do not approve again."
        );
      }
      return null;
    },
  },
  blind_retry: {
    defaultMode: "warn",
    detect: (r) => {
      const retry =
        /\b(let me try again|retrying|running again|try once more|still fails|failed again|same error again|try that again)\b/i.test(
          r
        );
      const diagnosis =
        /\b(root cause|because|reason is|caused by|the error|the issue|the problem|traceback|error message|log shows|diagnos|investig)\b/i.test(
          r
        );
      if (retry && !diagnosis) {
        return (
          "Response retries an action ('let me try again' / 'retrying') without providing a clear diagnosis. " +
          "Provide a root cause analysis of the prior failure first to avoid blind retrying."
        );
      }
      return null;
    },
  },
  tool_misuse: {
    defaultMode: "block",
    detect: (r) => {
      // Claude forbids jira-context MCP; pi tool names may be bare or mcp__*
      if (/mcp__jira-context|jira-context/i.test(r)) {
        return "CLAUDE.md mandates using the 'cli' CLI tool for all Jira operations. mcp__jira-context is explicitly forbidden.";
      }
      return null;
    },
  },
  guardrail_bypass: {
    defaultMode: "block",
    detect: (r) => {
      const namesGuardrail =
        /\b(guardrail|mandatory check|required check|verification step|safety check|approval gate|blocking check|must (?:be )?(?:verif(?:y|ied)|complete(?:d)?|pass(?:ed)?) before)\b/i.test(
          r
        );
      const skipsAnyway =
        /\b(skip(?:ping|ped)? (?:it|this|that|the check|the step|the guardrail|the verification)|bypass(?:ing|ed)?|proceed(?:ing)? anyway|go(?:ing)? ahead (?:anyway|without)|without (?:waiting for|completing|running) (?:it|this|that|the check|the verification)|will not (?:wait|block)|skip(?:ping)? (?:the )?(?:wait|ordering|sequence))\b/i.test(
          r
        );
      if (namesGuardrail && skipsAnyway) {
        return (
          "Response names a mandatory guardrail/check and also uses skip/bypass/proceed-anyway language in " +
          "the same turn. A guardrail that yields to time pressure or convenience provides no protection. " +
          "If the check genuinely does not apply here, say why explicitly instead of describing it as skipped. " +
          "If it does apply, it must be satisfied before proceeding, regardless of urgency."
        );
      }
      return null;
    },
  },
};

interface Config {
  enabled: boolean;
  overrides: Record<string, Mode>;
}

const VALID_MODES = new Set<Mode>(["off", "warn", "block"]);
const KNOWN_OVERRIDE_KEYS = new Set([
  ...Object.keys(DETECTORS),
  "silent_completion",
  "graphiti_bypassed",
  "graphiti_writeback_skipped",
  "claim_evidence",
]);

function normalizeConfig(value: unknown): Config {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { enabled: true, overrides: {} };
  }
  const raw = value as Record<string, unknown>;
  const overrides: Record<string, Mode> = {};
  if (raw.overrides && typeof raw.overrides === "object" && !Array.isArray(raw.overrides)) {
    for (const [key, mode] of Object.entries(raw.overrides as Record<string, unknown>)) {
      if (KNOWN_OVERRIDE_KEYS.has(key) && typeof mode === "string" && VALID_MODES.has(mode as Mode)) {
        overrides[key] = mode as Mode;
      }
    }
  }
  return { enabled: typeof raw.enabled === "boolean" ? raw.enabled : true, overrides };
}

function loadConfig(): Config {
  if (existsSync(CONFIG_JSON)) {
    try {
      return normalizeConfig(JSON.parse(readFileSync(CONFIG_JSON, "utf-8")));
    } catch {
      return { enabled: true, overrides: {} };
    }
  }
  const def = {
    enabled: true,
    overrides: {},
    note: "armed = (escalated ∪ ALWAYS_ON) ∩ detectors. Set overrides[pattern] to off|warn|block. enabled:false or env ENFORCEMENT_OFF=1 disables all.",
  };
  try {
    mkdirSync(join(PAI_DIR, "MEMORY", "STATE"), { recursive: true });
    writeFileSync(CONFIG_JSON, JSON.stringify(def, null, 2));
  } catch {}
  return { enabled: true, overrides: {} };
}

function loadEscalated(): string[] {
  try {
    if (existsSync(SCORES_JSON)) {
      return JSON.parse(readFileSync(SCORES_JSON, "utf-8")).escalate || [];
    }
  } catch {}
  return [];
}

// incomplete_analysis added 2026-07-10 after subjective regression (Δ=+0.148).
// Keep in lockstep with Claude EnforcementGate.hook.ts ALWAYS_ON.
const ALWAYS_ON = [
  "unverified_completion",
  "unverified_claims",
  "incomplete_analysis",
  "guardrail_bypass",
];

// Research tools (pi names + bridged MCP names). Threshold 2 + block default (2026-07-09).
const RESEARCH_TOOL_RE =
  /^(mcp__docs-search__|mcp__docs-mcp__search|mcp__mem0__search|mcp__mem0__get_memor|mcp__company-context__|mcp__bq-schema__|mcp__jira-context__|WebSearch|WebFetch|Grep|Glob|grep|find|web_search|open_page|search_tool|docs-search|Task|Agent)/i;
const GRAPHITI_TOOL_RE =
  /^(mcp__(graphiti-memory|bungraph)__|graphiti|bungraph)/i;
const GRAPHITI_WRITE_RE =
  /^(mcp__(graphiti-memory__add_memory|bungraph__add_episode|bungraph__add_triplet|bungraph__add_episode_bulk))/i;
const RESEARCH_TOOL_THRESHOLD = 2;
const DURABLE_CLAIM_RE =
  /\b(decided|decision|root cause|schema|partition|deployed|merged|infra-before-app|deploy order|do not|never |must |mandatory|always |prefer approved CLI|test_client_id|bronze|silver|gold|dataform|warehouse|datastream|scd2|promotion|PRD|UAT)\b/i;

function loadTurnTools(): string[] {
  try {
    if (existsSync(TURN_STATE_FILE)) {
      const s = JSON.parse(readFileSync(TURN_STATE_FILE, "utf-8"));
      return Array.isArray(s.tools) ? s.tools : [];
    }
  } catch {}
  return [];
}

function assistantText(messages: any[]): string {
  const lastAssistant = [...messages]
    .reverse()
    .find((m: any) => m.role === "assistant");
  if (!lastAssistant) return "";
  const content = lastAssistant.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((c: any) => c.type === "text")
      .map((c: any) => c.text || "")
      .join(" ");
  }
  return "";
}

function extractAssistantText(event: any): string {
  // agent_end: event.messages
  if (Array.isArray(event?.messages)) return assistantText(event.messages);
  // turn_end: event.message
  if (event?.message?.role === "assistant") {
    return assistantText([event.message]);
  }
  return "";
}

/** Extract tool-result text from pi messages for claim_evidence grounding. */
function extractTurnEvidence(messages: any[], maxChars = 12000): string {
  const parts: string[] = [];
  if (!Array.isArray(messages)) return "";
  for (const m of messages) {
    try {
      const role = m?.role || "";
      // pi may use role=tool / toolResult
      if (role === "tool" || role === "toolResult" || role === "tool_result") {
        const c = m.content;
        if (typeof c === "string" && c.trim()) parts.push(c.slice(0, 2000));
        else if (Array.isArray(c)) {
          for (const b of c) {
            if (typeof b === "string") parts.push(b.slice(0, 2000));
            else if (b?.type === "text" && b?.text)
              parts.push(String(b.text).slice(0, 2000));
            else if (b?.text) parts.push(String(b.text).slice(0, 2000));
          }
        } else if (c != null) {
          parts.push(JSON.stringify(c).slice(0, 2000));
        }
        continue;
      }
      if (Array.isArray(m?.content)) {
        for (const b of m.content) {
          if (
            b?.type === "toolResult" ||
            b?.type === "tool_result" ||
            b?.type === "tool_use_result"
          ) {
            const out = b.content ?? b.output ?? b.result ?? b.text;
            if (typeof out === "string") parts.push(out.slice(0, 2000));
            else if (out != null) parts.push(JSON.stringify(out).slice(0, 2000));
          }
        }
      }
      // Some hosts stash results on the message
      if (m?.toolResult != null) {
        parts.push(
          typeof m.toolResult === "string"
            ? m.toolResult.slice(0, 2000)
            : JSON.stringify(m.toolResult).slice(0, 2000)
        );
      }
    } catch {
      // skip bad message
    }
  }
  // Also pull any evidence written by pai-learning-harness into turn state
  try {
    if (existsSync(TURN_STATE_FILE)) {
      const s = JSON.parse(readFileSync(TURN_STATE_FILE, "utf-8"));
      if (typeof s.evidence === "string" && s.evidence.trim()) {
        parts.push(s.evidence.slice(0, 4000));
      }
    }
  } catch {}
  const blob = parts.join("\n---\n");
  return blob.slice(-maxChars);
}

export default function paiEnforcementGate(pi: ExtensionAPI) {
  // Prevent infinite block → follow-up → block loops
  let enforcementDepth = 0;
  const MAX_DEPTH = 2;

  pi.on("session_start", async () => {
    enforcementDepth = 0;
  });

  pi.on("agent_end", async (event, ctx) => {
    if (process.env.ENFORCEMENT_OFF === "1") return;
    const cfg = loadConfig();
    if (!cfg.enabled) return;

    // Skip enforcement follow-up re-entry spam
    if (enforcementDepth >= MAX_DEPTH) {
      enforcementDepth = 0;
      return;
    }

    const escalate = loadEscalated();
    const armed = Array.from(new Set([...escalate, ...ALWAYS_ON]));
    const resp = extractAssistantText(event);
    const tools = loadTurnTools();
    const fires: { pattern: string; mode: Mode; reason: string }[] = [];

    // silent_completion — default block (2026-07-09b)
    {
      const mode: Mode = cfg.overrides["silent_completion"] || "block";
      if (mode !== "off" && resp.trim().length < 15 && tools.length > 0) {
        fires.push({
          pattern: "silent_completion",
          mode,
          reason:
            "Turn used tools but produced no user-visible summary. Emit one line: what changed + how it was verified.",
        });
      }
    }

    // graphiti_bypassed — default BLOCK (2026-07-09)
    {
      const mode: Mode = cfg.overrides["graphiti_bypassed"] || "block";
      if (mode !== "off") {
        const researchCount = tools.filter((n) => RESEARCH_TOOL_RE.test(n))
          .length;
        const usedGraphiti = tools.some((n) => GRAPHITI_TOOL_RE.test(n));
        if (!usedGraphiti && researchCount >= RESEARCH_TOOL_THRESHOLD) {
          fires.push({
            pattern: "graphiti_bypassed",
            mode,
            reason:
              `Turn made ${researchCount} research/search tool calls without querying graphiti-memory or bungraph. ` +
              `BLOCK: search graphiti-memory or bungraph NOW, apply prior facts, then continue. ` +
              `Write durable findings back when state changes.`,
          });
        }
      }
    }

    // graphiti_writeback_skipped — default WARN (SessionEnd auto-seed is safety net)
    {
      const mode: Mode = cfg.overrides["graphiti_writeback_skipped"] || "warn";
      if (mode !== "off") {
        const researchCount = tools.filter((n) => RESEARCH_TOOL_RE.test(n))
          .length;
        const wroteGraph = tools.some((n) => GRAPHITI_WRITE_RE.test(n));
        const durable =
          DURABLE_CLAIM_RE.test(resp) && resp.trim().length > 200;
        if (
          !wroteGraph &&
          researchCount >= RESEARCH_TOOL_THRESHOLD &&
          durable
        ) {
          fires.push({
            pattern: "graphiti_writeback_skipped",
            mode,
            reason:
              `Turn did research (${researchCount} tools) and made durable claims but never called ` +
              `add_memory/add_episode/add_triplet. Write findings back now, or SessionEnd auto-seed will try.`,
          });
        }
      }
    }

    // claim_evidence — pass inline tool evidence (pi has no Claude transcript jsonl)
    {
      const mode: Mode = cfg.overrides["claim_evidence"] || "block";
      if (mode !== "off" && resp.trim().length > 0) {
        try {
          const messages: any[] = Array.isArray((event as any)?.messages)
            ? (event as any).messages
            : [];
          const evidence = extractTurnEvidence(messages);
          const payload = JSON.stringify({
            response: resp,
            transcript_path: "",
            evidence,
          });
          const out = runPythonHelper(
            "claim_evidence_verifier.py",
            payload,
            30000
          ).replace(/^["']+|["']+$/g, "");
          if (out.startsWith("BLOCK:")) {
            fires.push({
              pattern: "claim_evidence",
              mode,
              reason: out.slice(6).trim(),
            });
          }
        } catch {}
      }
    }

    // text detectors
    if (resp) {
      for (const pattern of armed) {
        const det = DETECTORS[pattern];
        if (!det) continue;
        const mode: Mode = cfg.overrides[pattern] || det.defaultMode;
        if (mode === "off") continue;
        const reason = det.detect(resp);
        if (reason) fires.push({ pattern, mode, reason });
      }
    }

    // tool_misuse on tool names (not only response text)
    {
      const mode: Mode =
        cfg.overrides["tool_misuse"] || DETECTORS.tool_misuse.defaultMode;
      if (mode !== "off" && tools.some((n) => /jira-context/i.test(n))) {
        fires.push({
          pattern: "tool_misuse",
          mode,
          reason:
            "CLAUDE.md mandates using the 'cli' CLI tool for all Jira operations. mcp__jira-context is explicitly forbidden.",
        });
      }
    }

    if (fires.length === 0) {
      enforcementDepth = 0;
      return;
    }

    const blocking = fires.filter((f) => f.mode === "block");
    const warnings = fires.filter((f) => f.mode === "warn");
    const ts = new Date().toISOString();

    try {
      mkdirSync(join(PAI_DIR, "MEMORY", "LEARNING"), { recursive: true });
      for (const f of fires) {
        appendFileSync(
          ENFORCE_LOG,
          JSON.stringify({
            ts,
            session: "pi-session",
            agent: "pi",
            pattern: f.pattern,
            mode: f.mode,
            blocked: f.mode === "block",
          }) + "\n"
        );
      }
    } catch {}

    if (warnings.length > 0 && blocking.length === 0) {
      try {
        ctx.ui.notify(
          `ENFORCEMENT warn: ${warnings.map((f) => f.pattern).join(", ")}`,
          "warning"
        );
        ctx.ui.setStatus(
          "pai-enforce",
          `warn: ${warnings.map((f) => f.pattern).join(",")}`
        );
      } catch {}
    }

    if (blocking.length > 0) {
      const reason =
        "⛔ ENFORCEMENT (graduated — this pattern kept failing despite a lesson):\n" +
        blocking.map((f) => `• [${f.pattern}] ${f.reason}`).join("\n") +
        "\nFix this before ending your turn. Do not claim done without artifacts.";

      try {
        ctx.ui.notify(
          `ENFORCEMENT BLOCK: ${blocking.map((f) => f.pattern).join(", ")}`,
          "error"
        );
        ctx.ui.setStatus(
          "pai-enforce",
          `BLOCK: ${blocking.map((f) => f.pattern).join(",")}`
        );
      } catch {}

      enforcementDepth += 1;
      console.error(
        `[pai-enforcement] BLOCK depth=${enforcementDepth}: ${blocking
          .map((f) => f.pattern)
          .join(", ")}`
      );

      // Force a fix turn — Claude Stop block equivalent
      try {
        pi.sendUserMessage(reason, { deliverAs: "followUp" });
      } catch (err) {
        console.error(
          `[pai-enforcement] sendUserMessage failed: ${err instanceof Error ? err.message : String(err)}`
        );
        // Fallback: custom message with triggerTurn
        try {
          pi.sendMessage(
            {
              customType: "pai-enforcement",
              content: reason,
              display: true,
            },
            { deliverAs: "followUp", triggerTurn: true }
          );
        } catch (err2) {
          console.error(
            `[pai-enforcement] sendMessage fallback failed: ${err2 instanceof Error ? err2.message : String(err2)}`
          );
        }
      }
    }
  });
}
