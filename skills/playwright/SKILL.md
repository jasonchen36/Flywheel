---
name: playwright
description: "Automates browser navigation, forms, screenshots, and UI debugging with Playwright. Use when browser automation or UI verification is needed."
---

# /playwright
# playwright Skill

**Usage:** `/playwright <task description>`

Automate a real browser for navigation, form filling, screenshots, data extraction, and UI-flow debugging.
Uses a navigate → snapshot → interact → verify loop. Recover from element-not-found and load failures before escalating.

---

## Tool selection

```
MCP tools available? → Use mcp__plugin_playwright_playwright__* (preferred)
MCP unavailable?     → Use npx playwright-cli via terminal
```

Verify MCP is available before starting:
```
mcp__plugin_playwright_playwright__browser_snapshot()
```
If this returns an error, fall back to terminal.

---

## Core loop: navigate → snapshot → interact → verify

**Always follow this order. Never interact without a fresh snapshot.**

```
1. browser_navigate(url)          → loads the page
2. browser_wait_for(condition)    → wait for "load" or specific element
3. browser_snapshot()             → get element refs (required before any click/type)
4. <interact>                     → click / fill / type / select using refs from snapshot
5. browser_snapshot()             → refresh refs after navigation or dynamic content change
6. browser_take_screenshot()      → capture state for verification
```

If step 4 returns "element not found": re-run `browser_snapshot()` and retry once with updated refs. If it fails again, check `browser_console_messages()` for JS errors before assuming the element doesn't exist.

---

## Recovery paths

| Failure | Recovery |
|---|---|
| Element not found | Re-snapshot, retry once. If still missing: check console for JS errors, check if page loaded correctly |
| Page didn't load | `browser_wait_for(condition="networkidle")` then retry once. If timeout: screenshot + report |
| Form submit doesn't navigate | Check `browser_network_requests()` for the POST — may be AJAX. Use `browser_wait_for` for response |
| Dialog blocking interaction | `browser_handle_dialog(action="accept")` then retry |
| Wrong page loaded (redirect) | Screenshot current URL, check `browser_snapshot()` for actual content |

Maximum 2 retries per interaction. If still failing after 2 retries, take a screenshot, report what's visible, and ask the user.

---

## Common patterns

### Screenshot a page
```
browser_navigate(url="<url>")
browser_wait_for(condition="load")
browser_take_screenshot()
```

### Fill and submit a form
```
browser_navigate(url="<form_url>")
browser_wait_for(condition="load")
browser_snapshot()                                          # get field refs
browser_fill_form(fields={"#field1": "val1", "#field2": "val2"})
browser_snapshot()                                          # verify fields filled
browser_click(element="Submit button")
browser_wait_for(condition="networkidle")                   # wait for submission
browser_take_screenshot()                                   # confirm result page
```

### Extract structured data
```
browser_navigate(url="<target>")
browser_wait_for(condition="load")
browser_snapshot()                                          # read from accessibility tree
# If data is in the DOM but not in the tree:
browser_evaluate(script="return document.querySelector('<selector>').innerText")
```

### Debug a failing UI flow
```
browser_navigate(url="<start>")
browser_wait_for(condition="load")
browser_snapshot()
# At each step that might fail:
browser_console_messages()   # JS errors
browser_network_requests()   # failed API calls (4xx/5xx)
browser_take_screenshot()    # visual state at failure point
```

### Multi-tab workflow
```
browser_tabs()                                              # list open tabs
# Navigate in current tab, then check others
```

---

## Terminal fallback

```bash
command -v npx >/dev/null 2>&1 || { echo "Install Node.js first"; exit 1; }

npx playwright-cli open <url> --headed
npx playwright-cli snapshot
npx playwright-cli screenshot --output /tmp/screenshot.png
```

Use terminal for repeatable scripts; use MCP for interactive debugging.

---

## Rules
- Always `browser_close()` when done
- Never store credentials in screenshots, logs, or browser storage
- Snapshot after every navigation before interacting — refs go stale
- Max 2 retries per interaction before stopping and reporting
- Never use `browser_evaluate` to extract passwords or auth tokens
