"""Validated configuration models shared by Flywheel's Python runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from state_io import atomic_write_json

EnforcementMode = Literal["off", "warn", "block"]
VALID_ENFORCEMENT_MODES = frozenset({"off", "warn", "block"})
KNOWN_ENFORCEMENT_OVERRIDES = frozenset(
    {
        "unverified_completion",
        "incomplete_analysis",
        "unverified_claims",
        "duplicate_approval",
        "blind_retry",
        "tool_misuse",
        "guardrail_bypass",
        "silent_completion",
        "graphiti_bypassed",
        "graphiti_writeback_skipped",
        "claim_evidence",
    }
)


@dataclass(frozen=True)
class EnforcementConfig:
    enabled: bool = True
    overrides: dict[str, EnforcementMode] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "overrides": dict(sorted(self.overrides.items()))}


@dataclass(frozen=True)
class ConfigLoadResult:
    config: EnforcementConfig
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_enforcement_config(value: object) -> ConfigLoadResult:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ConfigLoadResult(EnforcementConfig(), ("expected a JSON object",))

    enabled_value = value.get("enabled", True)
    if isinstance(enabled_value, bool):
        enabled = enabled_value
    else:
        enabled = True
        errors.append("enabled must be a boolean")

    overrides_value = value.get("overrides", {})
    normalized: dict[str, EnforcementMode] = {}
    if not isinstance(overrides_value, dict):
        errors.append("overrides must be a JSON object")
    else:
        for key, mode in overrides_value.items():
            if not isinstance(key, str) or key not in KNOWN_ENFORCEMENT_OVERRIDES:
                errors.append(f"unknown enforcement override: {key!r}")
                continue
            if not isinstance(mode, str) or mode not in VALID_ENFORCEMENT_MODES:
                errors.append(
                    f"invalid mode for {key}: {mode!r}; expected off, warn, or block"
                )
                continue
            normalized[key] = mode  # type: ignore[assignment]

    return ConfigLoadResult(EnforcementConfig(enabled, normalized), tuple(errors))


def load_enforcement_config(path: Path) -> ConfigLoadResult:
    if not path.exists():
        return ConfigLoadResult(EnforcementConfig())
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return ConfigLoadResult(
            EnforcementConfig(),
            (f"invalid enforcement config in {path.name}: {exc}",),
        )
    return validate_enforcement_config(value)


def save_enforcement_config(path: Path, config: EnforcementConfig) -> None:
    atomic_write_json(path, config.to_json())
