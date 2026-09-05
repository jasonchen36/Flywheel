from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import enforcement_promotion as promotion
from state_io import append_jsonl, atomic_write_json, load_jsonl_objects


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    state = tmp_path / "state"
    signals = tmp_path / "signals"
    state.mkdir()
    signals.mkdir()
    paths = {
        "config": state / "enforcement_config.json",
        "log": tmp_path / "enforcement_log.jsonl",
        "review": signals / "pending_human_review.jsonl",
    }
    monkeypatch.setattr(promotion, "CONFIG_JSON", paths["config"])
    monkeypatch.setattr(promotion, "ENFORCE_LOG", paths["log"])
    monkeypatch.setattr(promotion, "REVIEW_FILE", paths["review"])
    return paths


def _valid_config(path: Path, mode: str = "warn") -> None:
    atomic_write_json(
        path,
        {"enabled": True, "overrides": {"graphiti_bypassed": mode}},
    )


def test_log_loader_skips_malformed_and_non_object_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    paths["log"].write_text('{"pattern":"graphiti_bypassed"}\nbad\n[]\n')
    assert promotion.load_enforcement_log() == [{"pattern": "graphiti_bypassed"}]
    assert promotion.load_review_queue() == []


@pytest.mark.parametrize(
    "args",
    [["--window-days", "0"], ["--window-days", "-1"], ["--min-fires", "0"]],
)
def test_main_rejects_nonpositive_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
):
    _configure(tmp_path, monkeypatch)
    assert promotion.main(args) == 2
    assert "must be positive" in capsys.readouterr().err


def test_main_rejects_invalid_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    atomic_write_json(paths["config"], {"enabled": "yes", "overrides": {}})
    assert promotion.main([]) == 2
    assert "invalid config" in capsys.readouterr().err


def test_main_counts_only_valid_recent_pattern_fires_in_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _valid_config(paths["config"])
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    monkeypatch.setattr(
        promotion,
        "CONFIG_ONLY_PATTERNS",
        {"aaa_no_fires", "graphiti_bypassed", "second_config_only"},
    )
    rows = [
        {"pattern": "graphiti_bypassed", "ts": recent.isoformat()},
        {"pattern": "graphiti_bypassed", "ts": recent.replace(tzinfo=None).isoformat()},
        {"pattern": "graphiti_bypassed", "ts": old.isoformat()},
        {"pattern": "graphiti_bypassed", "ts": "bad"},
        {"pattern": "graphiti_bypassed", "ts": 1},
        {"pattern": "other", "ts": recent.isoformat()},
        {"pattern": "second_config_only", "ts": recent.isoformat()},
        {"pattern": "second_config_only", "ts": recent.isoformat()},
    ]
    for row in rows:
        append_jsonl(paths["log"], row)

    assert promotion.main(["--dry-run", "--min-fires", "2"]) == 0
    output = capsys.readouterr().out
    assert output.count("2 fires in last 14d") == 2
    assert "5 all-time" in output
    assert "no files written" in output
    assert not paths["review"].exists()


def test_main_enqueues_once_and_skips_nonwarn_or_existing_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    _valid_config(paths["config"])
    now = datetime.now(timezone.utc).isoformat()
    append_jsonl(paths["log"], {"pattern": "graphiti_bypassed", "ts": now})

    assert promotion.main(["--min-fires", "1"]) == 0
    assert len(load_jsonl_objects(paths["review"]).records) == 1
    assert "Queued 1 pattern" in capsys.readouterr().out
    assert promotion.main(["--min-fires", "1"]) == 0
    assert "No config-only patterns" in capsys.readouterr().out
    assert len(load_jsonl_objects(paths["review"]).records) == 1

    paths["review"].write_text("")
    _valid_config(paths["config"], mode="block")
    assert promotion.main(["--min-fires", "1"]) == 0
    assert "No config-only patterns" in capsys.readouterr().out
