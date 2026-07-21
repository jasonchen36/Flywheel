#!/usr/bin/env python3
"""
ace_reflector.py — ACE Reflector: distill lessons into high-quality playbook bullets.

Zhang et al. 2025 (Agentic Context Engineering): separate *insight extraction*
from *curation* so the playbook never accumulates vague prompt sludge.

  Generator  → ratings + failures (self_improve / SessionEnd)
  Reflector  → THIS FILE: weak-rule detection, evidence distill, quality score
  Curator    → ace_playbook.py: counters, dedupe, rank, emit STATE/ace_playbook.*

Quality bar (repair or demote if fail):
  - Reject stubs: "Avoid X — verify before acting"
  - Prefer imperative DO / DON'T with concrete check
  - Section taxonomy: strategy | pitfall | formula (ACE playbook sections)

Deterministic by default (SessionEnd often runs --no-llm). Optional LLM
reflection only when --llm and rule still weak after heuristic distill.

Usage:
  python3 ace_reflector.py --self-test
  python3 ace_reflector.py --dry-run          # score current lessons
  from ace_reflector import reflect_lesson, is_weak_rule, quality_score
"""

from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

# ── Weak-rule detection ──────────────────────────────────────────────────────

# Canonical stub from self_improve template fallback (pre-fix).
_STUB_RE = re.compile(
    r"^avoid\s+[\w\s/\-]+(?:—|--|-|–)\s*verify before acting\.?$",
    re.I,
)
_GENERIC_AVOID_RE = re.compile(
    r"^avoid\s+[\w\s/\-]{3,60}\.?\s*$",
    re.I,
)
# Hollow "be careful / verify" without a concrete check.
_HOLLOW_RE = re.compile(
    r"\b(be careful|double[- ]?check|verify before acting|always be mindful)\b",
    re.I,
)

# Patterns → high-quality reflected rules (domain bank). Used when lesson body
# is a stub or hollow. Keep imperative, specific, domain-specific.
PATTERN_BANK: dict[str, dict] = {
    "unverified_completion": {
        "section": "strategy",
        "description": (
            "Never claim done/fixed/complete without STRONG paper trace: fenced "
            "CLI/test output, exit codes, pass counts next to a runner, or live URL. "
            "Bare paths and bare 'N rows/tests' are NOT evidence."
        ),
    },
    "incomplete_analysis": {
        "section": "strategy",
        "description": (
            "Before concluding or agreeing: read ALL relevant context (full diff, "
            "PR comments, ticket, related files). Never say looks-unrelated / "
            "you're-right without a research trace (I read X / gh pr diff / fenced output)."
        ),
    },
    "unverified_claims": {
        "section": "strategy",
        "description": (
            "Never assert system state (schema/CI/PR/partition/row counts) without "
            "tool output. Tag [GUESS]/unverified when unverified. Never invent metrics "
            "or line refs."
        ),
    },
    "acting_without_permission": {
        "section": "strategy",
        "description": (
            "Draft → Show → Ask → Wait → Post. Never post, push, commit, or comment "
            "on colleague-owned work without explicit user approval."
        ),
    },
    "duplicate_approval": {
        "section": "pitfall",
        "description": (
            "If reviewDecision is already APPROVED, skip. Never leave a second "
            "approval 'just in case'."
        ),
    },
    "silent_completion": {
        "section": "strategy",
        "description": (
            "After any tool use, emit at least one user-visible line: what changed "
            "and how verified. Silent tool turns hide failures."
        ),
    },
    "blind_retry": {
        "section": "pitfall",
        "description": (
            "On failure: STOP. Read the error, change approach. Never re-run the "
            "exact same failing command."
        ),
    },
    "tool_misuse": {
        "section": "strategy",
        "description": (
            "Check CLAUDE.md tool routing before acting (cli for Jira, rtk wrap, "
            "MCP for reads). Wrong tool = wasted work."
        ),
    },
    "stale_context": {
        "section": "strategy",
        "description": (
            "Re-fetch current state immediately before acting. Prior-turn file "
            "contents and PR state may be stale."
        ),
    },
    "scope_misunderstanding": {
        "section": "strategy",
        "description": (
            "When an instruction has multiple plausible meanings, state the chosen "
            "interpretation before executing. Ambiguity = ask, never silent guess."
        ),
    },
    "explicit_instruction_violation": {
        "section": "pitfall",
        "description": (
            "When the user forbids a behavior, never do it again in the same or later "
            "turns. Re-read explicit constraints before every external action."
        ),
    },
    "violated_explicit_user_constraint": {
        "section": "pitfall",
        "description": (
            "User-stated constraints are absolute for the session. Before post/push/"
            "approve/global-replace: re-check the constraint list. One violation = fail."
        ),
    },
    "explicit_requirement_violated": {
        "section": "pitfall",
        "description": (
            "Treat numbered or bolded user requirements as a checklist. Do not mark "
            "done until each requirement has a verification artifact."
        ),
    },
    "explicitly_forbidden_behavior": {
        "section": "pitfall",
        "description": (
            "If user said never/don't/forbid X, block X at the planning step. "
            "Do not rationalize an exception without re-asking."
        ),
    },
    "approved_without_verification": {
        "section": "strategy",
        "description": (
            "Never approve a PR when open review findings or failing checks exist. "
            "Run `gh pr view --json reviewDecision,statusCheckRollup` first."
        ),
    },
    "missed_context": {
        "section": "strategy",
        "description": (
            "Before answering: load ticket, full PR diff, existing comments, and "
            "CLAUDE.md routing. If any is unread, say so — do not conclude."
        ),
    },
    "missed_analysis_edge_case": {
        "section": "strategy",
        "description": (
            "Before claiming complete analysis: list edge cases (empty input, "
            "permissions, env mismatch, partial deploy). If untested, mark residual risk."
        ),
    },
    "missing_validation": {
        "section": "strategy",
        "description": (
            "Pair every change with a validation step (test, dry-run, schema show, "
            "or live check). No validation artifact = not done."
        ),
    },
    "missed_validation_against_precedent": {
        "section": "strategy",
        "description": (
            "Before proposing a new pattern: search repo for existing precedent "
            "(similar YAML/SQLX/DAG). Prefer match local style over inventing a third way."
        ),
    },
    "failed_documentation_check": {
        "section": "strategy",
        "description": (
            "Before architectural decisions: query rtfmcp/Confluence/local docs. "
            "Do not invent process that docs already define."
        ),
    },
    "should_have_consulted_docs": {
        "section": "strategy",
        "description": (
            "Unknown internal process → docs first (rtfmcp, Confluence, skill README). "
            "Guessing internal policy is a failure mode."
        ),
    },
    "rtfm_required": {
        "section": "strategy",
        "description": (
            "When stuck or off-policy: read RTFM / skill SKILL.md before inventing. "
            "Code without the relevant skill doc is incomplete analysis."
        ),
    },
    "performance_regression": {
        "section": "pitfall",
        "description": (
            "Do not widen queries, drop partition filters, or full-scan BQ 'for "
            "convenience'. Filter-before-limit; dry-run bytes before claiming safe."
        ),
    },
    "performance_slower_than_baseline": {
        "section": "pitfall",
        "description": (
            "If a change may add latency/cost: measure baseline first, then after. "
            "Ship only if not worse without an explicit tradeoff stated."
        ),
    },
    "unhelpful_debugging_response": {
        "section": "strategy",
        "description": (
            "On debug: restate error, show repro command + output, change one variable. "
            "Never 'works on my side' without fresh repro in current env."
        ),
    },
    "unhelpful_troubleshooting": {
        "section": "strategy",
        "description": (
            "Troubleshooting loop: error → evidence → hypothesis → different action. "
            "Three identical retries without new evidence = stop and reframe."
        ),
    },
    "implicit_correction_needed": {
        "section": "strategy",
        "description": (
            "If user must restate the same constraint twice, the first response failed. "
            "Surface the constraint explicitly and confirm before continuing."
        ),
    },
    "variable_identification_error": {
        "section": "pitfall",
        "description": (
            "Before using a name (table, env, branch, secret): resolve it with a tool "
            "(`bq show`, `gh`, file read). Never swap similar identifiers by memory."
        ),
    },
    "variable_confusion": {
        "section": "pitfall",
        "description": (
            "When two similar symbols exist (dev/uat/prd, silver/gold, cow/goat): "
            "quote the exact ID from tool output before acting."
        ),
    },
    "capability_doubt": {
        "section": "strategy",
        "description": (
            "Do not claim inability without trying the routed tool. If blocked, report "
            "the exact error and next concrete step — not vague capability doubt."
        ),
    },
    "retained_learning_doubt": {
        "section": "strategy",
        "description": (
            "Session-start and UserPromptSubmit inject lessons; act as if they bind. "
            "Do not re-ask whether lessons persist — apply the active ACE bullets."
        ),
    },
    "context_retention_doubt": {
        "section": "strategy",
        "description": (
            "Use prior-turn decisions and open ticket/PR state. If uncertain, re-read "
            "files — do not claim amnesia as an excuse for re-scoping."
        ),
    },
    "stateless_concern": {
        "section": "strategy",
        "description": (
            "Harness state (lessons, playbook, ratings) persists across sessions. "
            "Do not behave as if every session is a clean slate on known rules."
        ),
    },
    "stateless_behavior": {
        "section": "strategy",
        "description": (
            "Re-load prior decisions from MEMORY/PR/ticket before restarting work. "
            "Stateless restarts of finished analysis waste the user."
        ),
    },
    "consistency_concern": {
        "section": "strategy",
        "description": (
            "Scoped review comments change only the named location. Global renames "
            "require an explicit 'everywhere' ask — never expand scope silently."
        ),
    },
    "poor_tone_authenticity": {
        "section": "strategy",
        "description": (
            "Caveman lite default: short, direct, no filler, no emoji. Match user "
            "register; skip corporate throat-clearing."
        ),
    },
    "tone_not_human": {
        "section": "strategy",
        "description": (
            "Write like a sharp teammate: concrete verbs, evidence, next step. "
            "Avoid robotic templates and empty empathy padding."
        ),
    },
    "lack_natural_voice": {
        "section": "strategy",
        "description": (
            "Prefer plain engineering English over marketing or agent-boilerplate. "
            "One clear claim per sentence; no 'I'd be happy to' filler."
        ),
    },
    "lack_naturalness": {
        "section": "strategy",
        "description": (
            "Skip stock assistant openers. Lead with the answer or the blocker, "
            "then evidence."
        ),
    },
    "lack_of_authenticity": {
        "section": "strategy",
        "description": (
            "If unsure, say [GUESS] and verify. Fake confidence is worse than a short "
            "uncertainty tag."
        ),
    },
    "lacking_natural_communication_style": {
        "section": "strategy",
        "description": (
            "Match local engineering voice: terse, evidence-first, "
            "no emoji, no performative agreement."
        ),
    },
    "robotic_output": {
        "section": "pitfall",
        "description": (
            "Do not dump numbered boilerplate when a 3-line answer works. Structure "
            "only when it reduces ambiguity."
        ),
    },
    "robotic_tone": {
        "section": "pitfall",
        "description": (
            "Avoid scripted empathy and 'As an AI' framing. State facts and actions."
        ),
    },
    "inauthentic_tone": {
        "section": "pitfall",
        "description": (
            "Do not over-agree ('you're absolutely right') before checking. Verify, "
            "then respond with evidence."
        ),
    },
    "suboptimal_tool_choice": {
        "section": "strategy",
        "description": (
            "Prefer specialized tools (cli, bq skill, MCP schema search) over raw "
            "shell guesswork. Re-read routing when unsure."
        ),
    },
    "missed_uncertainty_acknowledgment": {
        "section": "strategy",
        "description": (
            "When evidence is partial: tag [INFERRED]/[GUESS] and name what would "
            "confirm. Do not present guesses as known."
        ),
    },
    "acknowledged_uncertainty": {
        "section": "strategy",
        "description": (
            "Uncertainty is fine only when paired with the next verification step. "
            "Do not stall on vague doubt — run the check or ask one precise question."
        ),
    },
    "unverified_claim_accepted": {
        "section": "pitfall",
        "description": (
            "Do not accept bot/CI/self claims at face value. Re-check live git/PR/BQ "
            "state before agreeing."
        ),
    },
    "regression_introduction": {
        "section": "pitfall",
        "description": (
            "Touch only what the task requires. No bonus cleanup. Run tests on every "
            "file you modify before claiming done."
        ),
    },
    "no_dry_run_sql": {
        "section": "formula",
        "description": (
            "SQL/schema change: `bq query --dry-run` before claim done; "
            "`bq show --schema` before and after for schema edits."
        ),
    },
    "wrong_env_promotion": {
        "section": "formula",
        "description": (
            "Promotion order: master → STG → UAT → PRD via promotion PRs. Never merge "
            "direct to UAT/PRD; never approve PRD until UAT verified."
        ),
    },
    "airflow_blind_retry": {
        "section": "strategy",
        "description": (
            "Airflow failure: read task logs + upstream states before retry. Never "
            "re-trigger without root cause."
        ),
    },
    "pr_review_failure": {
        "section": "strategy",
        "description": (
            "Read ALL existing PR comments before posting findings. Do not re-raise "
            "resolved concerns. Read full diff, not just hunks."
        ),
    },
    "redundant_recommendation": {
        "section": "pitfall",
        "description": (
            "Search for existing capability before recommending a new one. Do not "
            "propose features that already ship."
        ),
    },
    "missing_dependency": {
        "section": "strategy",
        "description": (
            "After setup: run the binary/import. Do not claim setup complete until "
            "a real execution succeeds."
        ),
    },
}


@dataclass
class ReflectedBullet:
    pattern: str
    description: str
    section: str = "strategy"  # strategy | pitfall | formula
    quality: int = 0           # 0=stub .. 4=seed-grade
    source: str = "passthrough"  # passthrough | bank | evidence | heuristic | llm
    weak_input: bool = False
    notes: str = ""
    evidence_used: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def is_weak_rule(rule: str) -> bool:
    """True if rule is a stub or hollow advice that must not enter strategy injection."""
    r = (rule or "").strip()
    if not r:
        return True
    if _STUB_RE.match(r):
        return True
    if _GENERIC_AVOID_RE.match(r) and len(r) < 70:
        return True
    if len(r) < 40:
        return True
    # "Avoid {pattern words} — verify before acting" variants with extra spaces
    if r.lower().startswith("avoid ") and "verify before acting" in r.lower():
        return True
    if _HOLLOW_RE.search(r) and len(r) < 90:
        return True
    return False


def quality_score(rule: str) -> int:
    """0=stub … 4=highly specific actionable bullet."""
    r = (rule or "").strip()
    if not r:
        return 0
    if is_weak_rule(r):
        return 0
    score = 1
    # Imperative / prohibition signal
    if re.search(
        r"\b(never|always|do not|don't|must|before |after |run |check |read )\b",
        r,
        re.I,
    ):
        score += 1
    # Concrete artifact (tool, command, path, gate)
    if re.search(
        r"(`[^`]+`|--[a-z]|gh |bq |cli|rtk |CLAUDE\.md|dry-run|fence|exit code|"
        r"reviewDecision|Draft|Show|Ask)",
        r,
        re.I,
    ):
        score += 1
    # Dual guidance (don't + do) or multi-clause specificity
    if (" instead" in r.lower() or (";" in r and len(r) > 80)
            or re.search(r"\b(then|before|after|without)\b", r, re.I)):
        score += 1
    if len(r) >= 120 and score >= 3:
        score = min(4, score + 0)
    return min(4, score)


def _pattern_phrase(pattern: str) -> str:
    return pattern.replace("_", " ").strip()


def _classify_section(pattern: str, rule: str) -> str:
    if pattern in PATTERN_BANK:
        return PATTERN_BANK[pattern].get("section", "strategy")
    low = (rule or "").lower()
    if any(k in low for k in ("formula", "order:", "master →", "bq query", "dry-run")):
        return "formula"
    if any(k in low for k in ("never ", "do not ", "don't ", "pitfall", "stop.")):
        return "pitfall"
    if pattern.endswith(("_error", "_failure", "_violation", "_regression")):
        return "pitfall"
    return "strategy"


def extract_evidence_lines(md_body: str, limit: int = 6) -> list[str]:
    """Pull evidence / sentiment lines from a lesson markdown body."""
    lines: list[str] = []
    for line in (md_body or "").splitlines():
        t = line.strip()
        if re.match(r"^-\s*\[\d", t):
            # strip leading "- [n] "
            t = re.sub(r"^-\s*\[\d+\]\s*", "", t).strip()
            if t:
                lines.append(t[:200])
        if len(lines) >= limit:
            break
    return lines


def extract_structured_fields(md: str) -> dict:
    """Parse lesson file into rule, evidence, root_cause, where."""
    parts = md.split("---", 2)
    body = (parts[2] if len(parts) >= 3 else md).lstrip("\n")
    rule = ""
    for line in body.splitlines():
        t = line.strip()
        if not t or t.startswith("**") or t.startswith("#") or t.startswith("<!--"):
            continue
        rule = t[:400]
        break
    root = ""
    m = re.search(r"\*\*Root cause:\*\*\s*(.+)", body)
    if m:
        root = m.group(1).strip()[:300]
    where = ""
    m = re.search(r"\*\*Where it happens:\*\*\s*(.+)", body)
    if m:
        where = m.group(1).strip()[:200]
    evidence = extract_evidence_lines(body)
    return {
        "rule": rule,
        "root_cause": root,
        "where": where,
        "evidence": evidence,
        "body": body,
    }


def _evidence_distill(pattern: str, evidence: list[str]) -> Optional[str]:
    """Turn rating evidence into a concrete rule when evidence is rich enough."""
    if not evidence:
        return None
    joined = " ".join(evidence).lower()
    # High-signal correction phrases → map to known actions
    if "forbade" in joined or "without permission" in joined or "overstep" in joined:
        return (
            "Draft → Show → Ask → Wait → Post. Never post, push, or act on "
            "colleague-owned work without explicit approval."
        )
    if "already approved" in joined or "second approval" in joined:
        return (
            "If reviewDecision is already APPROVED, skip — do not approve again."
        )
    if "globally" in joined or "sre" in joined and "dbre" in joined:
        return (
            "Scoped comment = change only named location. Global replace only when "
            "user says all/everywhere. Confirm before mass-change."
        )
    if "repeat" in joined and ("fail" in joined or "mistake" in joined):
        return (
            "On failure: stop, diagnose with new evidence, change approach. "
            "Never re-run the exact same failing action."
        )
    if "retain" in joined or "between sessions" in joined or "amnesia" in joined:
        return (
            "Lessons and ACE playbook inject every turn — apply them. Do not claim "
            "or imply session amnesia for known rules."
        )
    if "unverified" in joined or "without testing" in joined or "claimed" in joined:
        return (
            "Never claim done or state system facts without fenced tool output in "
            "the same response."
        )
    # If evidence is only mood words, skip
    mood_only = all(
        re.search(r"\b(angry|frustrated|confused|mild|impatient|skeptic)\b", e, re.I)
        and len(e) < 80
        for e in evidence
    )
    if mood_only:
        return None
    # Use strongest (lowest rating-adjacent) evidence line as "what went wrong"
    top = evidence[0]
    # If evidence already looks like a correction instruction, promote it
    if re.search(r"\b(never|don't|do not|must|always|before)\b", top, re.I) and len(top) > 40:
        return top[:280]
    return None


def _heuristic_from_pattern(pattern: str) -> str:
    """Last-resort structured rule from pattern tokens — never the old stub."""
    phrase = _pattern_phrase(pattern)
    # Verb-ish patterns
    if pattern.startswith("missed_"):
        what = phrase.removeprefix("missed ").strip()
        return (
            f"Before concluding: explicitly cover {what}. If unread or untested, "
            f"say so and fetch/check it — do not skip."
        )
    if pattern.startswith("missing_"):
        what = phrase.removeprefix("missing ").strip()
        return (
            f"Do not claim done without {what}. Attach the verification artifact "
            f"in the same response."
        )
    if pattern.startswith("unverified_"):
        what = phrase.removeprefix("unverified ").strip()
        return (
            f"Never assert {what} without tool output. Fence the proof or tag "
            f"[GUESS] and verify next."
        )
    if pattern.endswith("_doubt") or pattern.endswith("_concern"):
        return (
            f"Convert {phrase} into a concrete check: run the tool or re-read the "
            f"source of truth, then answer from evidence — not meta-worry."
        )
    if "tone" in pattern or "robotic" in pattern or "natural" in pattern or "authenticity" in pattern:
        return (
            "Caveman lite: short, direct, evidence-first, no emoji, no filler. "
            "Lead with answer or blocker."
        )
    if "permission" in pattern or "forbidden" in pattern or "constraint" in pattern:
        return (
            "Re-read user constraints before external actions. Draft → show → ask → "
            "wait. Never expand scope or post without approval."
        )
    if "retry" in pattern:
        return (
            "Failed action: stop, read error, change approach. No identical re-run."
        )
    if "performance" in pattern or "latency" in pattern or "slower" in pattern:
        return (
            "Preserve partition filters and baseline cost. Measure before claiming "
            "a change is safe or faster."
        )
    # Generic but still actionable (quality ~2)
    return (
        f"When {phrase} risk appears: stop, gather tool evidence, then act. "
        f"Do not proceed on memory or assumption alone."
    )


def reflect_lesson(
    pattern: str,
    rule: str = "",
    evidence: Optional[list[str]] = None,
    root_cause: str = "",
    where: str = "",
    use_llm: bool = False,
) -> ReflectedBullet:
    """Distill one lesson into a quality-scored ACE bullet."""
    evidence = evidence or []
    weak = is_weak_rule(rule)
    q_in = quality_score(rule)

    # 1) Already high quality → passthrough
    if not weak and q_in >= 3:
        return ReflectedBullet(
            pattern=pattern,
            description=rule.strip()[:400],
            section=_classify_section(pattern, rule),
            quality=q_in,
            source="passthrough",
            weak_input=False,
            evidence_used=evidence[:3],
        )

    # 2) Domain bank
    if pattern in PATTERN_BANK:
        desc = PATTERN_BANK[pattern]["description"]
        return ReflectedBullet(
            pattern=pattern,
            description=desc,
            section=PATTERN_BANK[pattern].get("section", "strategy"),
            quality=quality_score(desc),
            source="bank",
            weak_input=weak,
            notes="pattern bank",
            evidence_used=evidence[:3],
        )

    # 3) Evidence distill
    ev = _evidence_distill(pattern, evidence)
    if ev and not is_weak_rule(ev):
        return ReflectedBullet(
            pattern=pattern,
            description=ev[:400],
            section=_classify_section(pattern, ev),
            quality=quality_score(ev),
            source="evidence",
            weak_input=weak,
            evidence_used=evidence[:3],
        )

    # 4) Root-cause boost into heuristic
    if root_cause and len(root_cause) > 30 and not is_weak_rule(root_cause):
        # Turn root cause into imperative if needed
        rc = root_cause.strip()
        if not re.match(r"^(Never|Always|Do |Don't|Before|After|When|Check|Read|Run)", rc):
            rc = f"Given root cause ({rc[:120]}): verify with tools before asserting or completing."
        if quality_score(rc) >= 2:
            return ReflectedBullet(
                pattern=pattern,
                description=rc[:400],
                section=_classify_section(pattern, rc),
                quality=quality_score(rc),
                source="evidence",
                weak_input=weak,
                notes="root_cause",
                evidence_used=evidence[:2],
            )

    # 5) Optional LLM (only when still weak)
    if use_llm and weak:
        llm_rule = _reflect_llm(pattern, rule, evidence, root_cause)
        if llm_rule and not is_weak_rule(llm_rule):
            return ReflectedBullet(
                pattern=pattern,
                description=llm_rule[:400],
                section=_classify_section(pattern, llm_rule),
                quality=quality_score(llm_rule),
                source="llm",
                weak_input=True,
                evidence_used=evidence[:3],
            )

    # 6) Heuristic from pattern tokens (never emit old stub)
    if weak or q_in < 2:
        h = _heuristic_from_pattern(pattern)
        if where:
            h = f"{h} Hotspots: {where[:80]}."
        return ReflectedBullet(
            pattern=pattern,
            description=h[:400],
            section=_classify_section(pattern, h),
            quality=quality_score(h),
            source="heuristic",
            weak_input=weak,
            evidence_used=evidence[:3],
        )

    # 7) Keep original mid-quality
    return ReflectedBullet(
        pattern=pattern,
        description=rule.strip()[:400],
        section=_classify_section(pattern, rule),
        quality=q_in,
        source="passthrough",
        weak_input=False,
        evidence_used=evidence[:3],
    )


def _reflect_llm(
    pattern: str,
    rule: str,
    evidence: list[str],
    root_cause: str,
) -> Optional[str]:
    try:
        from self_improve import call_llm  # local import; may be unavailable
    except Exception:
        return None
    ex = "\n".join(f"- {e}" for e in evidence[:6]) or "(no evidence lines)"
    prompt = (
        f"Rewrite this AI-assistant failure lesson into ONE high-quality ACE playbook bullet.\n"
        f"Pattern: {pattern}\n"
        f"Current rule (may be weak): {rule or '(empty)'}\n"
        f"Root cause: {root_cause or '(none)'}\n"
        f"Evidence:\n{ex}\n\n"
        "Requirements:\n"
        "- One or two imperative sentences\n"
        "- Concrete check or tool when possible\n"
        "- Lead with NEVER/DO NOT or BEFORE, then what TO do\n"
        "- No 'Avoid X — verify before acting'\n"
        "- No markdown headers or bullets\n"
        "Reply with ONLY the bullet text."
    )
    raw = call_llm(
        prompt,
        max_tokens=220,
        system="You write ACE playbook bullets. Specific, actionable, no fluff.",
    )
    if not raw:
        return None
    line = raw.strip().splitlines()[0].strip().lstrip("-• ").strip()
    return line[:400] if line else None


def fallback_rule_from_examples(pattern: str, examples: list) -> str:
    """Replacement for self_improve stub fallback when template + LLM missing.

    `examples` items may be RatingEntry-like (sentiment_summary, comment, rating)
    or plain strings.
    """
    evidence: list[str] = []
    for e in examples[:8]:
        if isinstance(e, str):
            evidence.append(e)
            continue
        summary = getattr(e, "sentiment_summary", "") or ""
        comment = getattr(e, "comment", "") or ""
        bit = (summary + (" | " + comment if comment else "")).strip()
        if bit:
            evidence.append(bit[:200])
    reflected = reflect_lesson(pattern, rule="", evidence=evidence, use_llm=False)
    return reflected.description


def reflect_from_lesson_file(path_text: str, pattern: str, use_llm: bool = False) -> ReflectedBullet:
    fields = extract_structured_fields(path_text)
    return reflect_lesson(
        pattern=pattern,
        rule=fields["rule"],
        evidence=fields["evidence"],
        root_cause=fields["root_cause"],
        where=fields["where"],
        use_llm=use_llm,
    )


def self_test() -> int:
    fails = 0

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        if not cond:
            print(f"  FAIL {name}")
            fails += 1
        else:
            print(f"  ok   {name}")

    check("stub weak", is_weak_rule("Avoid missed context — verify before acting."))
    check("good not weak", not is_weak_rule(
        "Never claim done without fenced CLI output and exit codes."
    ))
    check("stub quality 0", quality_score("Avoid foo — verify before acting.") == 0)
    check("good quality>=3", quality_score(
        "Never claim done without fenced CLI output; run tests before saying complete."
    ) >= 3)

    r = reflect_lesson("missed_context", "Avoid missed context — verify before acting.")
    check("reflect upgrades stub", not is_weak_rule(r.description) and r.quality >= 2)
    check("bank hits", reflect_lesson("blind_retry", "").source == "bank")

    fb = fallback_rule_from_examples(
        "acting_without_permission",
        ["Angry — forbade this behavior", "acted without permission"],
    )
    check("fallback not stub", not is_weak_rule(fb))

    print(f"[ace_reflector] self_test {'PASS' if fails == 0 else f'FAIL ({fails})'}")
    return 0 if fails == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="ACE Reflector")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="score lessons on disk")
    ap.add_argument("--llm", action="store_true", help="allow LLM for residual weak rules")
    ap.add_argument("--max", type=int, default=30)
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    from pathlib import Path

    lessons_dir = Path.home() / ".claude/MEMORY/lessons"
    if not lessons_dir.exists():
        print("[ace_reflector] no lessons dir")
        return 1

    weak_n = 0
    upgraded = 0
    rows = []
    for p in sorted(lessons_dir.glob("lesson_autogen_*.md"))[: args.max * 3]:
        pattern = p.name.removeprefix("lesson_autogen_").removesuffix(".md")
        text = p.read_text(errors="replace")
        fields = extract_structured_fields(text)
        if is_weak_rule(fields["rule"]):
            weak_n += 1
        ref = reflect_lesson(
            pattern,
            fields["rule"],
            fields["evidence"],
            fields["root_cause"],
            fields["where"],
            use_llm=args.llm,
        )
        if ref.source != "passthrough":
            upgraded += 1
        rows.append(ref)
        if len(rows) >= args.max:
            break

    print(f"[ace_reflector] scanned={len(rows)} input_weak≈{weak_n} upgraded={upgraded}")
    for r in rows[:15]:
        print(
            f"  q{r.quality} {r.source:<11} {r.section:<8} {r.pattern:<36} "
            f"{r.description[:55]}"
        )
    if args.dry_run:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
