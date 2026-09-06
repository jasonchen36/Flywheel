from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import harness_changelog
from state_io import atomic_write_json


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    roots = {
        "learning": tmp_path / "learning",
        "state": tmp_path / "state",
        "lessons": tmp_path / "lessons",
        "commands": tmp_path / "commands",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    snapshot = roots["state"] / "harness_changelog_last.json"
    changelog = roots["state"] / "harness_changelog.md"
    monkeypatch.setattr(harness_changelog, "WATCH_ROOTS", roots)
    monkeypatch.setattr(harness_changelog, "STATE", roots["state"])
    monkeypatch.setattr(harness_changelog, "SNAPSHOT", snapshot)
    monkeypatch.setattr(harness_changelog, "CHANGELOG", changelog)
    monkeypatch.setattr(harness_changelog, "IGNORED_NAMES", {snapshot.name, changelog.name})
    monkeypatch.setattr(harness_changelog, "now_iso", lambda: "2026-09-06T12:00:00+00:00")
    return {**roots, "snapshot": snapshot, "changelog": changelog}


def test_file_digest_safe_integer_and_classification(tmp_path: Path):
    path = tmp_path / "file.txt"
    path.write_text("content")
    assert harness_changelog.file_digest(path) == hashlib.sha256(b"content").hexdigest()
    assert harness_changelog.safe_int("4") == 4
    assert harness_changelog.safe_int(None, -1) == -1
    assert harness_changelog.safe_int("bad", -1) == -1
    assert harness_changelog.safe_int(float("inf"), -1) == -1

    assert harness_changelog.classify("lessons/a.md") == "lessons"
    assert harness_changelog.classify("commands/a.md") == "skills"
    assert harness_changelog.classify("state/a.json") == "state"
    assert harness_changelog.classify("learning/DIAGNOSTICS/a.md") == "diagnostics"
    assert harness_changelog.classify("learning/a.jsonl") == "ledgers"
    assert harness_changelog.classify("learning/held_out_suite/fixtures/a.json") == "fixtures"
    assert harness_changelog.classify("learning/a.py") == "scripts"
    assert harness_changelog.classify("learning/a.txt") == "other"


def test_snapshot_hashes_files_and_excludes_symlinks_locks_temps_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    tracked = paths["learning"] / "worker.py"
    tracked.write_text("print('ok')\n")
    (paths["state"] / "data.json").write_text("{}")
    paths["snapshot"].write_text("self")
    paths["changelog"].write_text("self")
    lock = paths["state"] / ".data.json.lock.d"
    lock.mkdir()
    (lock / "owner.json").write_text("{}")
    (paths["learning"] / ".worker.py.token.tmp").write_text("temp")
    cache = paths["learning"] / "__pycache__"
    cache.mkdir()
    (cache / "worker.pyc").write_bytes(b"cache")
    external = tmp_path / "external.txt"
    external.write_text("secret")
    (paths["learning"] / "external-link").symlink_to(external)

    result = harness_changelog.snapshot()
    assert set(result) == {"learning/worker.py", "state/data.json"}
    fingerprint = result["learning/worker.py"]
    assert fingerprint["sha256"] == hashlib.sha256(tracked.read_bytes()).hexdigest()
    assert fingerprint["size"] == tracked.stat().st_size
    assert isinstance(fingerprint["mtime_ns"], int)


def test_snapshot_skips_missing_roots_and_unreadable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        harness_changelog,
        "WATCH_ROOTS",
        {"missing": missing, "learning": paths["learning"]},
    )
    good = paths["learning"] / "good.py"
    bad = paths["learning"] / "bad.py"
    good.write_text("good")
    bad.write_text("bad")
    real_digest = harness_changelog.file_digest

    def digest(path: Path) -> str:
        if path == bad:
            raise OSError("unreadable")
        return real_digest(path)

    monkeypatch.setattr(harness_changelog, "file_digest", digest)
    assert set(harness_changelog.snapshot()) == {"learning/good.py"}


def test_fingerprint_comparison_supports_hashes_and_legacy_snapshots():
    current = {"sha256": "new", "size": 4, "mtime_ns": 2_000_000_000}
    assert harness_changelog.fingerprint_changed({"sha256": "new", "size": 4}, current) is False
    assert harness_changelog.fingerprint_changed({"sha256": "old", "size": 4}, current) is True
    assert harness_changelog.fingerprint_changed([2.0, 4], current) is False
    assert harness_changelog.fingerprint_changed([0.0, 4], current) is True
    assert harness_changelog.fingerprint_changed([2.0, 5], current) is True
    assert harness_changelog.fingerprint_changed(["bad", 4], current) is True
    assert harness_changelog.fingerprint_changed("bad", current) is True


def test_load_previous_handles_missing_malformed_and_nonobject_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    assert harness_changelog.load_previous() is None
    paths["snapshot"].write_text("bad")
    assert harness_changelog.load_previous() is None
    paths["snapshot"].write_text("[]")
    assert harness_changelog.load_previous() is None
    atomic_write_json(paths["snapshot"], {"files": {}})
    assert harness_changelog.load_previous() == {"files": {}}


def test_main_dry_baseline_and_real_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    (paths["learning"] / "worker.py").write_text("one")
    assert harness_changelog.main(["--dry-run"]) == 0
    assert "baseline snapshot would be written (1 files)" in capsys.readouterr().out
    assert not paths["snapshot"].exists()

    assert harness_changelog.main([]) == 0
    assert "Baseline snapshot written (1 files)" in capsys.readouterr().out
    state = json.loads(paths["snapshot"].read_text())
    assert state["files"]["learning/worker.py"]["sha256"]
    assert "First baseline snapshot" in paths["changelog"].read_text()


def test_main_detects_same_size_content_changes_additions_and_removals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    paths = _configure(tmp_path, monkeypatch)
    changed = paths["learning"] / "worker.py"
    removed = paths["commands"] / "old.md"
    changed.write_text("aaaa")
    removed.write_text("old")
    assert harness_changelog.main([]) == 0
    capsys.readouterr()
    original_stat = changed.stat()

    changed.write_text("bbbb")
    os.utime(changed, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    removed.unlink()
    (paths["lessons"] / "new.md").write_text("lesson")

    snapshot_before = paths["snapshot"].read_text()
    assert harness_changelog.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "added=1, changed=1, removed=1" in output
    assert "~ learning/worker.py" in output
    assert paths["snapshot"].read_text() == snapshot_before

    assert harness_changelog.main([]) == 0
    output = capsys.readouterr().out
    assert "added=1, changed=1, removed=1" in output
    report = paths["changelog"].read_text()
    assert "learning/worker.py [scripts] (4 -> 4 bytes)" in report
    assert "lessons/new.md [lessons]" in report
    assert "commands/old.md" in report

    assert harness_changelog.main([]) == 0
    assert "added=0, changed=0, removed=0" in capsys.readouterr().out
    assert "No harness mutations" in paths["changelog"].read_text()


def test_main_accepts_legacy_snapshot_and_bounds_report_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    original = paths["learning"] / "legacy.py"
    original.write_text("legacy")
    stat = original.stat()
    atomic_write_json(
        paths["snapshot"],
        {
            "taken_at": "old",
            "files": {
                "learning/legacy.py": [stat.st_mtime, stat.st_size],
                "state/removed-a.json": [0, 1],
                "state/removed-b.json": [0, 1],
            },
        },
    )
    (paths["lessons"] / "a.md").write_text("a")
    (paths["lessons"] / "b.md").write_text("b")
    monkeypatch.setattr(harness_changelog, "MAX_FILES", 1)

    assert harness_changelog.main([]) == 0
    report = paths["changelog"].read_text()
    assert "## Added (1 shown of 2)" in report
    assert "## Removed (1 shown of 2)" in report
    assert "learning/legacy.py" not in report
    state = json.loads(paths["snapshot"].read_text())
    assert isinstance(state["files"]["learning/legacy.py"], dict)


def test_main_normalizes_invalid_previous_file_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    (paths["learning"] / "worker.py").write_text("one")
    atomic_write_json(paths["snapshot"], {"files": ["bad"]})
    assert harness_changelog.main([]) == 0
    assert "learning/worker.py" in paths["changelog"].read_text()


def test_now_iso_is_timezone_aware():
    value = harness_changelog.now_iso()
    assert value.endswith("+00:00")


def test_main_normalizes_malformed_prior_file_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _configure(tmp_path, monkeypatch)
    worker = paths["learning"] / "worker.py"
    worker.write_text("current")
    monkeypatch.setattr(
        harness_changelog,
        "load_previous",
        lambda: {"files": {"learning/worker.py": {"sha256": "old", "size": {}}}},
    )
    assert harness_changelog.main([]) == 0
    assert "(0 -> 7 bytes)" in paths["changelog"].read_text()
