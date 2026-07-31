#!/usr/bin/env bun
/**
 * SymbolGroundingVerifier.hook.ts - PreToolUse / PostToolUse hook for code and doc edits.
 *
 * PURPOSE:
 * Prevents Category 1 symbol hallucinations (e.g. inventing class names like
 * 'WarehouseDatasetFactory' instead of actual 'WarehouseDatasetUtility') by enforcing
 * deterministic symbol grounding checks whenever Markdown design docs or code are modified.
 *
 * TRIGGER: PreToolUse / PostToolUse (Write, Edit, search_replace, Bash)
 */

import { readFileSync, existsSync } from "fs";
import { execSync } from "child_process";

try {
  const rawInput = readFileSync(0, "utf-8");
  if (rawInput) {
    const data = JSON.parse(rawInput);
    const toolName = data.tool_name || "";
    const toolInput = data.tool_input || {};

    let filePath = toolInput.file_path || toolInput.target_file || "";
    let content = toolInput.content || toolInput.new_string || "";
    let command = toolInput.command || "";

    // Check if tool is editing a design doc, PR description, or Python/TS code file
    const isDocOrCode = filePath && (
      filePath.endsWith(".md") ||
      filePath.endsWith(".py") ||
      filePath.endsWith(".ts")
    );

    const isPRCommand = command && (
      command.includes("gh pr create") ||
      command.includes("gh pr edit") ||
      command.includes("gh stack submit")
    );

    if (isDocOrCode || isPRCommand) {
      const textToScan = content || command;

      // Extract framework class symbols ending in Utility, Factory, Operator, Sensor, Manager, Service, Helper, Util
      const matches = textToScan.match(/\b[A-Z][a-zA-Z0-9]+(Utility|Factory|Operator|Sensor|Manager|Service|Helper|Util)\b/g);
      if (matches && matches.length > 0) {
        const uniqueSymbols = Array.from(new Set(matches));

        process.stdout.write(
          `\n[SYMBOL GROUNDING MANDATE]: Framework symbols detected (${uniqueSymbols.join(", ")}).\n` +
          `Category 1 Anti-Hallucination Rule: You MUST verify exact symbol existence in the target codebase (` +
          `e.g. using 'rtk rg "<symbol>"') before committing or publishing design docs/PRs. Never guess or assume class/method signatures.\n\n`
        );
      }
    }
  }
} catch (err) {
  // Silent fail on invalid input
}
