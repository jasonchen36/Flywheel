#!/usr/bin/env bun
/**
 * PRDescriptionReminder.hook.ts - PostToolUse hook for git push / PR branch updates.
 *
 * PURPOSE:
 * Automatically reminds the agent to update the PR description on GitHub
 * whenever code or workflow changes are pushed to a remote PR branch.
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

    let command = "";
    if (toolName === "Bash" || toolName === "bash" || toolName === "run_terminal_command") {
      command = toolInput.command || "";
    }

    // Detect git push or PR branch modifications
    if (command && (command.includes("git push") || command.includes("gh pr create"))) {
      process.stdout.write(
        `\n[PR DESCRIPTION MANDATE]: Remote push or PR activity detected ('${command.slice(0, 60)}...').\nRemember: You MUST update the PR description on GitHub (via 'gh pr edit <pr_number> --body "..."') to document all new changes, fixes, and verification evidence before declaring completion (use Rule 11 plain-text formatting).\n\n`
      );
    }
  }
} catch (err) {
  // Silent fail
}
