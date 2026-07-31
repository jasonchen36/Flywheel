#!/usr/bin/env bun
/**
 * ReviewFeedbackPersistence.hook.ts - PreToolUse / PostToolUse hook for PR review feedback.
 *
 * PURPOSE:
 * Enforces the Mandatory Immediate Memory Persistence Protocol. Whenever review feedback,
 * PR comments, human corrections, or verification failures are encountered, this hook reminds
 * the agent that fixing local code is only half the job — persisting the lesson into memory
 * (Graphiti, MEM0, skills) is MANDATORY before completing the turn.
 *
 * TRIGGER: PreToolUse / PostToolUse (Bash, Skill, Write)
 */

import { readFileSync } from "fs";

try {
  const rawInput = readFileSync(0, "utf-8");
  if (rawInput) {
    const data = JSON.parse(rawInput);
    const toolName = data.tool_name || "";
    const toolInput = data.tool_input || {};

    const command = toolInput.command || "";
    const prompt = toolInput.prompt || "";
    const filePath = toolInput.file_path || "";

    const isReviewFeedback =
      (command && (command.includes("gh pr view") || command.includes("pr comments") || command.includes("discussion_r") || command.includes("issuecomment"))) ||
      (prompt && (prompt.includes("review feedback") || prompt.includes("PR review") || prompt.includes("fix review"))) ||
      (filePath && (filePath.includes("CLAUDE.md") || filePath.includes("AGENTS.md") || filePath.includes("errors-and-lessons.md")));

    if (isReviewFeedback) {
      process.stdout.write(
        `\n[MANDATORY IMMEDIATE MEMORY PERSISTENCE]: PR review feedback or policy correction detected.\n` +
        `Rule: Fixing local code or text is only HALF the job. You MUST IMMEDIATELY:\n` +
        `1. Call 'graphiti-memory__add_memory' to persist the feedback, root cause, and prevention rule to long-term memory graph.\n` +
        `2. Patch the relevant skill (SKILL.md) or CLAUDE.md policy so this mistake NEVER recurs in future turns/sessions.\n\n`
      );
    }
  }
} catch (err) {
  // Silent fail
}
