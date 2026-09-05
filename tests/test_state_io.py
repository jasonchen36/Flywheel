from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import state_io
from state_io import (
    append_jsonl,
    append_jsonl_many,
    append_jsonl_many_unlocked,
    append_jsonl_unlocked,
    atomic_write_json,
    atomic_write_text,
    exclusive_lock,
    exclusive_locks,
    load_jsonl_objects,
    lock_path_for,
    rewrite_jsonl,
    try_read_json_object,
)


def test_try_read_json_object_handles_missing_valid_invalid_and_non_object(
    tmp_path: Path,
):
    path = tmp_path / "state.json"
    assert try_read_json_object(path) == ({}, None)

    path.write_text('{"enabled": true}')
    assert try_read_json_object(path) == ({"enabled": True}, None)

    path.write_text("{")
    value, error = try_read_json_object(path)
    assert value == {}
    assert error and "invalid JSON" in error

    path.write_text("[]")
    value, error = try_read_json_object(path)
    assert value == {}
    assert error == "invalid state in state.json: expected a JSON object"


def test_try_read_json_object_reports_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "state.json"
    path.write_text("{}")

    def fail_read(_self: Path) -> str:
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", fail_read)
    value, error = try_read_json_object(path)
    assert value == {}
    assert error and "unreadable" in error


def test_load_jsonl_objects_isolates_bad_rows_and_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "events.jsonl"
    assert load_jsonl_objects(path).records == []

    path.write_text('\n{"id": 1}\nnot-json\n[]\n{"id": 2}\n')
    result = load_jsonl_objects(path)
    assert result.records == [{"id": 1}, {"id": 2}]
    assert result.invalid_lines == (3, 4)

    def fail_read(_self: Path) -> str:
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", fail_read)
    failed = load_jsonl_objects(path)
    assert failed.records == []
    assert failed.invalid_lines == (0,)


def test_atomic_writes_create_parents_preserve_mode_and_sort_json(tmp_path: Path):
    text_path = tmp_path / "nested" / "state.txt"
    atomic_write_text(text_path, "first")
    assert text_path.read_text() == "first"

    text_path.chmod(0o640)
    atomic_write_text(text_path, "second")
    assert text_path.read_text() == "second"
    assert text_path.stat().st_mode & 0o777 == 0o640

    json_path = tmp_path / "state.json"
    atomic_write_json(json_path, {"z": 1, "a": 2}, indent=None)
    assert json_path.read_text() == '{"a": 2, "z": 1}\n'


def test_atomic_write_removes_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "state.txt"

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(state_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(path, "value")
    assert not path.exists()
    assert list(tmp_path.glob(".state.txt.*.tmp")) == []


def test_rewrite_and_append_jsonl_round_trip(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    rewrite_jsonl(path, [{"b": 2, "a": 1}, {"id": 2}])
    append_jsonl(path, {"id": 3})
    assert load_jsonl_objects(path).records == [
        {"a": 1, "b": 2},
        {"id": 2},
        {"id": 3},
    ]
    rewrite_jsonl(path, [])
    assert path.read_text() == ""


def test_batch_append_round_trip_and_empty_batch(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    append_jsonl_many(path, [{"id": 1}, {"id": 2}])
    append_jsonl_many_unlocked(path, [{"id": 3}, {"id": 4}])
    append_jsonl_many(path, [])
    append_jsonl_many_unlocked(path, [])
    assert [row["id"] for row in load_jsonl_objects(path).records] == [1, 2, 3, 4]


def test_append_without_fcntl_uses_portable_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(state_io, "fcntl", None)
    append_jsonl(path, {"id": 1})
    append_jsonl_unlocked(path, {"id": 2})
    assert [row["id"] for row in load_jsonl_objects(path).records] == [1, 2]


def test_lock_helpers_use_stable_sorted_unique_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    first = tmp_path / "b.json"
    second = tmp_path / "a.json"
    assert lock_path_for(first) == tmp_path / ".b.json.lock"

    entered: list[str] = []

    @contextmanager
    def fake_lock(path: Path):
        entered.append(path.name)
        yield
        entered.append(f"/{path.name}")

    monkeypatch.setattr(state_io, "exclusive_lock", fake_lock)
    with exclusive_locks((first, second, first)):
        entered.append("body")
    assert entered == ["a.json", "b.json", "body", "/b.json", "/a.json"]

    entered.clear()
    with exclusive_locks(()):
        entered.append("empty")
    assert entered == ["empty"]


def test_real_exclusive_lock_creates_sidecar(tmp_path: Path):
    target = tmp_path / "state.json"
    with exclusive_lock(target):
        assert lock_path_for(target).exists()
    assert lock_path_for(target).exists()
