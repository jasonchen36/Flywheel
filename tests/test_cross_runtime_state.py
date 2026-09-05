from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
WORKER = ROOT / "tests" / "fixtures" / "state_io_worker.ts"
sys.path.insert(0, str(LEARNING))

from state_io import (
    append_jsonl,
    exclusive_lock,
    load_jsonl_objects,
    lock_path_for,
    rewrite_jsonl_unlocked,
)
from test_hardening import run_installer

pytestmark = pytest.mark.skipif(shutil.which("bun") is None, reason="Bun is required")


def _run_bun(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bun", str(WORKER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_bun_append_waits_for_python_rewrite_and_preserves_both_rows(tmp_path: Path):
    target = tmp_path / "events.jsonl"
    with exclusive_lock(target):
        process = subprocess.Popen(
            ["bun", str(WORKER), "append", str(target), "after-rewrite"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.05)
        assert process.poll() is None
        rewrite_jsonl_unlocked(target, [{"runtime": "python", "value": "rewrite"}])
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, f"{stdout}\n{stderr}"
    assert load_jsonl_objects(target).records == [
        {"runtime": "python", "value": "rewrite"},
        {"runtime": "bun", "value": "after-rewrite"},
    ]


def test_python_append_waits_for_bun_owner(tmp_path: Path):
    target = tmp_path / "events.jsonl"
    marker = tmp_path / "locked"
    process = subprocess.Popen(
        ["bun", str(WORKER), "hold", str(target), str(marker)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    started = time.monotonic()
    append_jsonl(target, {"runtime": "python", "value": "after-bun"})
    elapsed = time.monotonic() - started
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, f"{stdout}\n{stderr}"
    assert elapsed >= 0.10
    assert load_jsonl_objects(target).records == [
        {"runtime": "python", "value": "after-bun"}
    ]


def test_bun_append_recovers_dead_python_owner(tmp_path: Path):
    target = tmp_path / "events.jsonl"
    lock_path = lock_path_for(target)
    lock_path.mkdir()
    (lock_path / "owner.json").write_text(
        json.dumps({"pid": 99999999, "token": "dead", "created_at": 0}) + "\n"
    )
    result = _run_bun("append", str(target), "recovered")
    assert result.returncode == 0, result.stderr
    assert load_jsonl_objects(target).records == [
        {"runtime": "bun", "value": "recovered"}
    ]
    assert not lock_path.exists()


def test_installer_places_shared_runtime_for_hooks_and_pi(
    tmp_path: Path,
):
    harness, result = run_installer(tmp_path, force_tar=True)
    assert result.returncode == 0, result.stderr
    assert (harness / "runtime" / "state-io.ts").is_file()
    assert (
        tmp_path / "missing-parent" / "runtime" / "state-io.ts"
    ).is_file()
