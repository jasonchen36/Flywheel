from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

from harness_config import (
    ConfigLoadResult,
    EnforcementConfig,
    KNOWN_ENFORCEMENT_OVERRIDES,
    load_enforcement_config,
    save_enforcement_config,
    validate_enforcement_config,
)


def test_default_and_valid_configuration_are_normalized(tmp_path: Path):
    missing = load_enforcement_config(tmp_path / "missing.json")
    assert missing == ConfigLoadResult(EnforcementConfig())
    assert missing.ok is True

    result = validate_enforcement_config(
        {
            "enabled": False,
            "overrides": {
                "tool_misuse": "warn",
                "blind_retry": "off",
                "claim_evidence": "block",
            },
        }
    )
    assert result.ok is True
    assert result.config.enabled is False
    assert result.config.to_json() == {
        "enabled": False,
        "overrides": {
            "blind_retry": "off",
            "claim_evidence": "block",
            "tool_misuse": "warn",
        },
    }


def test_non_object_and_wrong_container_types_are_rejected():
    non_object = validate_enforcement_config([])
    assert non_object.ok is False
    assert non_object.errors == ("expected a JSON object",)

    wrong_types = validate_enforcement_config({"enabled": "yes", "overrides": []})
    assert wrong_types.config == EnforcementConfig()
    assert wrong_types.errors == (
        "enabled must be a boolean",
        "overrides must be a JSON object",
    )


def test_unknown_keys_and_invalid_modes_are_rejected_without_entering_config():
    result = validate_enforcement_config(
        {
            "overrides": {
                "typo_pattern": "block",
                "blind_retry": "explode",
                "tool_misuse": 1,
                5: "warn",
            }
        }
    )
    assert result.config.overrides == {}
    assert result.errors == (
        "unknown enforcement override: 'typo_pattern'",
        "invalid mode for blind_retry: 'explode'; expected off, warn, or block",
        "invalid mode for tool_misuse: 1; expected off, warn, or block",
        "unknown enforcement override: 5",
    )


def test_load_reports_invalid_json_and_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "enforcement_config.json"
    path.write_text("{")
    malformed = load_enforcement_config(path)
    assert malformed.ok is False
    assert malformed.errors[0].startswith("invalid enforcement config")

    def fail_read(_self: Path) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", fail_read)
    unreadable = load_enforcement_config(path)
    assert unreadable.ok is False
    assert "permission denied" in unreadable.errors[0]


def test_python_and_typescript_override_keys_stay_in_sync():
    detector_pattern = re.compile(r"^  ([a-z][a-z0-9_]+): \{$", re.MULTILINE)
    special_keys = {
        "silent_completion",
        "graphiti_bypassed",
        "graphiti_writeback_skipped",
        "claim_evidence",
    }
    runtime_key_sets = []
    for runtime in (
        ROOT / "hooks" / "EnforcementGate.hook.ts",
        ROOT / "pi" / "pai-enforcement-gate.ts",
    ):
        text = runtime.read_text()
        detector_block = text.split("const DETECTORS", 1)[1].split("interface Config", 1)[0]
        detector_keys = set(detector_pattern.findall(detector_block))
        assert detector_keys
        runtime_key_sets.append(detector_keys | special_keys)
    assert runtime_key_sets[0] == runtime_key_sets[1] == KNOWN_ENFORCEMENT_OVERRIDES


def test_save_round_trip_is_atomic_and_valid(tmp_path: Path):
    path = tmp_path / "nested" / "enforcement_config.json"
    expected = EnforcementConfig(True, {"silent_completion": "block"})
    save_enforcement_config(path, expected)
    loaded = load_enforcement_config(path)
    assert loaded.ok is True
    assert loaded.config == expected
