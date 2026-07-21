"""Central path resolution for the self-learning harness.

Override with env:
  HARNESS_HOME     default: ~/.claude
  HARNESS_LESSONS_DIR  default: $HARNESS_HOME/MEMORY/lessons
  HARNESS_MEETING_DIR  default: $HARNESS_HOME/meeting-summaries
  GRAPHITI_GROUP_ID    default: main
  GCP_PROJECT / VERTEX project for background LLM
"""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
HARNESS_HOME = Path(os.environ.get("HARNESS_HOME", HOME / ".claude")).expanduser()
LEARNING = HARNESS_HOME / "MEMORY" / "LEARNING"
STATE = HARNESS_HOME / "MEMORY" / "STATE"
SIGNALS = LEARNING / "SIGNALS"
DIAGNOSTICS = LEARNING / "DIAGNOSTICS"
FAILURES = LEARNING / "FAILURES"
LESSONS_DIR = Path(
    os.environ.get("HARNESS_LESSONS_DIR", HARNESS_HOME / "MEMORY" / "lessons")
).expanduser()
MEETING_DIR = Path(
    os.environ.get("HARNESS_MEETING_DIR", HARNESS_HOME / "meeting-summaries")
).expanduser()
HOOKS = HARNESS_HOME / "hooks"
COMMANDS = HARNESS_HOME / "commands"
PI_SKILLS = Path(os.environ.get("HARNESS_PI_SKILLS", HOME / ".pi" / "agent" / "skills")).expanduser()
GRAPHITI_GROUP_ID = os.environ.get("GRAPHITI_GROUP_ID", "main")
GRAPHITI_MCP_URL = os.environ.get("GRAPHITI_MCP_URL", "http://127.0.0.1:8000/mcp")


def ensure_layout() -> None:
    for d in (LEARNING, STATE, SIGNALS, DIAGNOSTICS, FAILURES, LESSONS_DIR, MEETING_DIR):
        d.mkdir(parents=True, exist_ok=True)
