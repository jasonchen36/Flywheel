from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SESSION_END = ROOT / "templates" / "session-end.sh"

EXPECTED_STAGES = [
    "ratings_hygiene.py",
    "meeting_summary_ingest.py",
    "intent_how_audit.py",
    "self_improve.py",
    "evals.py",
    "judge_outcomes.py",
    "pattern_promotion.py",
    "measure_effectiveness.py",
    "skill_autofix.py",
    "enforcement_promotion.py",
    "held_out_regression.py",
    "lesson_dedup.py",
    "lesson_evolve.py",
    "review_queue.py",
    "held_out_suite.py",
    "agent_rollouts.py",
    "self_harness.py",
    "consolidate_memory.py",
    "session_graphiti_autoseed.py",
    "sync_graph_memory.py",
    "flush_graphiti_pending.py",
    "harness_changelog.py",
    "surface_gate.py",
]


def _wait_for(path: Path, needle: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = path.read_text() if path.exists() else ""
        if needle in text:
            return text
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {needle!r} in {path}")


def _make_fake_python(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-python"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "script=$1\n"
        "printf '%s\\n' \"$script\" >> \"$TRACE_FILE\"\n"
        "if [ \"$script\" = \"ratings_hygiene.py\" ]; then sleep \"${STAGE_SLEEP:-0}\"; fi\n"
        "if [ \"$script\" = \"surface_gate.py\" ]; then exit 1; fi\n"
        "exit 0\n"
    )
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize("force_directory_lock", [False, True])
def test_session_end_serializes_stages_and_skips_overlap(
    tmp_path: Path, force_directory_lock: bool
):
    harness = tmp_path / "harness"
    learning = harness / "MEMORY" / "LEARNING"
    learning.mkdir(parents=True)
    trace = tmp_path / "trace.txt"
    fake_python = _make_fake_python(tmp_path)
    env = {
        **os.environ,
        "HARNESS_HOME": str(harness),
        "HARNESS_PYTHON": str(fake_python),
        "TRACE_FILE": str(trace),
        "STAGE_SLEEP": "0.6",
        "HARNESS_FORCE_DIRECTORY_LOCK": "1" if force_directory_lock else "0",
    }

    first = subprocess.run(
        ["bash", str(SESSION_END)], env=env, text=True, capture_output=True, check=False
    )
    assert first.returncode == 0, first.stderr

    status = learning / "DIAGNOSTICS" / "session-end" / "latest.tsv"
    _wait_for(status, "pipeline\tstarted")

    second = subprocess.run(
        ["bash", str(SESSION_END)], env=env, text=True, capture_output=True, check=False
    )
    assert second.returncode == 0, second.stderr

    completed = _wait_for(status, "pipeline\tcompleted")
    skipped = status.parent / "skipped.tsv"
    _wait_for(skipped, "already-running")

    assert trace.read_text().splitlines() == EXPECTED_STAGES
    assert "pipeline\tfailed" not in completed
    assert completed.count("\tpipeline\tcompleted\t0") == 1


def test_session_end_disabled_does_not_start_pipeline(tmp_path: Path):
    harness = tmp_path / "harness"
    (harness / "MEMORY" / "LEARNING").mkdir(parents=True)
    env = {
        **os.environ,
        "HARNESS_HOME": str(harness),
        "PAI_SELF_IMPROVE_DISABLED": "1",
    }
    result = subprocess.run(
        ["bash", str(SESSION_END)], env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0
    status = harness / "MEMORY" / "LEARNING" / "DIAGNOSTICS" / "session-end" / "latest.tsv"
    assert "\tdisabled\t0" in status.read_text()


def test_session_end_recovers_stale_directory_lock(tmp_path: Path):
    harness = tmp_path / "harness"
    learning = harness / "MEMORY" / "LEARNING"
    log_dir = learning / "DIAGNOSTICS" / "session-end"
    lock_dir = log_dir / "pipeline.lock.d"
    lock_dir.mkdir(parents=True)
    (lock_dir / "pid").write_text("99999999\n")
    trace = tmp_path / "trace.txt"
    env = {
        **os.environ,
        "HARNESS_HOME": str(harness),
        "HARNESS_PYTHON": str(_make_fake_python(tmp_path)),
        "TRACE_FILE": str(trace),
        "HARNESS_FORCE_DIRECTORY_LOCK": "1",
    }

    result = subprocess.run(
        ["bash", str(SESSION_END)], env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    status = log_dir / "latest.tsv"
    _wait_for(status, "pipeline\tcompleted")
    assert trace.read_text().splitlines() == EXPECTED_STAGES
    assert not lock_dir.exists()
