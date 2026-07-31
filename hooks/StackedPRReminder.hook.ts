#!/usr/bin/env bun
/**
 * StackedPRReminder.hook.ts - PreToolUse / PostToolUse hook for GitHub Stacked PRs (gh-stack).
 *
 * PURPOSE:
 * Provides workflow guidance and quality verification prompts when working with stacked
 * pull requests using the 'gh-stack' extension.
 *
 * TRIGGER: PostToolUse (Bash)
 */

import { readFileSync } from "fs";

try {
  const rawInput = readFileSync(0, "utf-8");
  if (rawInput) {
    const data = JSON.parse(rawInput);
    const toolName = data.tool_name || "";
    const toolInput = data.tool_input || {};

    const command = toolInput.command || "";

    if (command && (command.includes("gh stack") || command.includes("gh-stack"))) {
      process.stdout.write(
        `\n[STACKED PR WORKFLOW]: GitHub Stacked PR activity detected ('${command.slice(0, 60)}...').\n` +
        `Reminder: Ensure each stacked branch is focused and reviewable. Run unit tests and 'verify-doc-symbols.sh' on every layer before running 'gh stack submit'.\n\n`
      );
    }
  }
} catch (err) {
  // Silent fail
}
