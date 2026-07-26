#!/usr/bin/env bun
/**
 * VerificationReminder.hook.ts - PostToolUse hook for Edit/Write verification reminders.
 *
 * PURPOSE:
 * Automatically reminds the agent to execute live verification and integration tests
 * after making file edits or script changes, enforcing Rule 4 and Error 254.
 */

import { readFileSync } from "fs";

try {
  const rawInput = readFileSync(0, "utf-8");
  if (rawInput) {
    const data = JSON.parse(rawInput);
    const toolName = data.tool_name || "";
    const toolInput = data.tool_input || {};

    let filePath = "";
    if (toolName.toLowerCase().includes("write") || toolName.toLowerCase().includes("edit")) {
      filePath = toolInput.file_path || toolInput.path || toolInput.target_file || "";
    }

    if (filePath) {
      process.stdout.write(
        `\n[VERIFICATION MANDATE]: Modified file '${filePath}'. Remember: You MUST run live verification commands / end-to-end tests in this turn and inspect raw console output BEFORE claiming completion (Error 254).\n\n`
      );
    }
  }
} catch (err) {
  // Silent fail
}
