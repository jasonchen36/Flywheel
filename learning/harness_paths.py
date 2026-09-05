"""Central path resolution for the self-learning harness.

Every runtime path must derive from this module so ``HARNESS_HOME`` works for
both installation and execution. Optional directory overrides are useful when
migrating an existing installation or sharing selected state across agents.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    """Return an expanded path from *name*, or *default* when it is unset."""
    return Path(os.environ.get(name, str(default))).expanduser()


HOME = Path.home()
HARNESS_HOME = _env_path("HARNESS_HOME", HOME / ".claude")
MEMORY = HARNESS_HOME / "MEMORY"
LEARNING = MEMORY / "LEARNING"
STATE = MEMORY / "STATE"
SIGNALS = LEARNING / "SIGNALS"
DIAGNOSTICS = LEARNING / "DIAGNOSTICS"
FAILURES = LEARNING / "FAILURES"
LESSONS_DIR = _env_path("HARNESS_LESSONS_DIR", MEMORY / "lessons")
MEETING_DIR = _env_path("HARNESS_MEETING_DIR", HARNESS_HOME / "meeting-summaries")
SCRUM_DIR = _env_path("HARNESS_SCRUM_DIR", HARNESS_HOME / "scrum-recordings")
PROJECTS_DIR = _env_path("HARNESS_PROJECTS_DIR", HARNESS_HOME / "projects")
HOOKS = HARNESS_HOME / "hooks"
COMMANDS = HARNESS_HOME / "commands"
PI_SKILLS = _env_path("HARNESS_PI_SKILLS", HOME / ".pi" / "agent" / "skills")
BUNGRAPH_DB = _env_path("BUNGRAPH_DB", HOME / ".bungraph.db")
GRAPHITI_GROUP_ID = os.environ.get("GRAPHITI_GROUP_ID", "main")
GRAPHITI_MCP_URL = os.environ.get("GRAPHITI_MCP_URL", "http://127.0.0.1:8000/mcp")


def ensure_layout() -> None:
    """Create the mutable directory layout required by the learning loop."""
    for directory in (
        LEARNING,
        STATE,
        SIGNALS,
        DIAGNOSTICS,
        FAILURES,
        LESSONS_DIR,
        MEETING_DIR,
        SCRUM_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
