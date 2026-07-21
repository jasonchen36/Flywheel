---
name: stash-context
description: "Stashes session working context for later resume. Use when pausing work and needing a restoreable context snapshot."
---

# /stash-context
# stash-context Skill

**Usage:** `/stash-context` or `/stash-context <topic label>`

Save current session context to mem0 before switching topics so it can be recovered in a future session.

---

## When to use
- About to switch to a different task mid-session
- Wrapping up a subtask and want to preserve state
- Before a context compaction / thread reset

## Workflow

### 1. Capture current state
Summarize the active work in this structure:
- **Topic**: what were we working on?
- **Progress**: what was completed, what is in-flight?
- **Blockers**: anything unresolved or waiting on input?
- **Next step**: the exact next action to resume from
- **Key artifacts**: PR links, DAG names, BQ tables, JIRA tickets, branch names

### 2. Save to mem0
```
mcp__mem0__add_memory(
    text="[stash] <topic>: <progress summary> | next: <next step> | artifacts: <links>",
    user_id="operator",
    metadata={"type": "stash", "topic": "<label>", "stale_after": "<date+7d>"}
)
```

### 3. Confirm to user
Output a one-liner confirming what was stashed:
> "Stashed: <topic> — next step: <next step>"

---

## Recovery (next session)
```
mcp__mem0__search_memories(query="stash <topic>", user_id="operator", limit=3)
```
Or at session start, `session-start` will surface recent stashes automatically.

---

## Rules
- Keep stash entries concise — decisions and next step only, not raw transcript
- Tag with `type=stash` for easy filtering during cleanup
- Stash entries are ephemeral; set `stale_after` to 7 days
