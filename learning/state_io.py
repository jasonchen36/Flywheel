"""Durable, reusable I/O primitives for Flywheel state files.

State is frequently read by hooks while SessionEnd updates it. Rewrites therefore
use same-directory temporary files plus ``os.replace``. JSONL appenders and
rewriters coordinate through a portable ownership-directory protocol shared by
Python and Bun runtimes.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.01
LOCK_STALE_SECONDS = 300.0


@dataclass(frozen=True)
class JsonlLoadResult:
    """Parsed JSON-object rows and the one-based line numbers that were skipped."""

    records: list[dict[str, Any]]
    invalid_lines: tuple[int, ...]


def try_read_json_object(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read a JSON object, returning a diagnostic instead of raising on bad state."""
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"invalid JSON in {path.name}: {exc}"
    if not isinstance(data, dict):
        return {}, f"invalid state in {path.name}: expected a JSON object"
    return data, None


def load_jsonl_objects(path: Path) -> JsonlLoadResult:
    """Load JSON-object rows while isolating malformed or non-object lines."""
    records: list[dict[str, Any]] = []
    invalid_lines: list[int] = []
    if not path.exists():
        return JsonlLoadResult(records, ())
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return JsonlLoadResult(records, (0,))
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines.append(line_number)
            continue
        if not isinstance(value, dict):
            invalid_lines.append(line_number)
            continue
        records.append(value)
    return JsonlLoadResult(records, tuple(invalid_lines))


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace *path* with text, preserving its mode when it exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            temporary.chmod(existing_mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(
    path: Path,
    value: Mapping[str, Any] | list[Any],
    *,
    indent: int | None = 2,
) -> None:
    """Serialize JSON deterministically and replace *path* atomically."""
    atomic_write_text(path, json.dumps(value, indent=indent, sort_keys=True) + "\n")


def rewrite_jsonl_unlocked(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Replace JSONL atomically when the caller already owns its sidecar lock."""
    lines = [json.dumps(record, sort_keys=True) for record in records]
    atomic_write_text(path, "".join(f"{line}\n" for line in lines))


def lock_path_for(path: Path) -> Path:
    """Return the stable ownership directory used for a mutable state file."""
    return path.with_name(f".{path.name}.lock.d")


def _owner_path(lock_path: Path) -> Path:
    return lock_path / "owner.json"


def _read_lock_owner(lock_path: Path) -> tuple[int, str] | None:
    try:
        value = json.loads(_owner_path(lock_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    pid = value.get("pid")
    token = value.get("token")
    if not isinstance(pid, int) or not isinstance(token, str) or not token:
        return None
    return pid, token


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(lock_path: Path, stale_after: float) -> bool:
    owner = _read_lock_owner(lock_path)
    if owner is not None:
        return not _pid_alive(owner[0])
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return True
    return age > stale_after


def _remove_lock(lock_path: Path) -> None:
    if lock_path.is_dir():
        shutil.rmtree(lock_path, ignore_errors=True)
    else:
        lock_path.unlink(missing_ok=True)


@contextmanager
def exclusive_lock(
    path: Path,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
    poll_interval: float = LOCK_POLL_SECONDS,
    stale_after: float = LOCK_STALE_SECONDS,
) -> Iterator[None]:
    """Hold a portable exclusive lock shared by Python and Bun runtimes."""
    if timeout < 0 or poll_interval < 0 or stale_after < 0:
        raise ValueError("lock timing values must be non-negative")
    lock_path = lock_path_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_path.mkdir()
        except FileExistsError:
            if _lock_is_stale(lock_path, stale_after):
                _remove_lock(lock_path)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for state lock: {path}") from None
            time.sleep(poll_interval)
            continue
        try:
            atomic_write_json(
                _owner_path(lock_path),
                {"pid": os.getpid(), "token": token, "created_at": time.time()},
                indent=None,
            )
        except BaseException:
            _remove_lock(lock_path)
            raise
        break
    try:
        yield
    finally:
        owner = _read_lock_owner(lock_path)
        if owner is not None and owner == (os.getpid(), token):
            _remove_lock(lock_path)


@contextmanager
def exclusive_locks(paths: Iterable[Path]) -> Iterator[None]:
    """Acquire multiple sidecar locks in a stable order to prevent deadlocks."""
    unique_paths = sorted({path.resolve() for path in paths}, key=str)
    with ExitStack() as stack:
        for path in unique_paths:
            stack.enter_context(exclusive_lock(path))
        yield


def rewrite_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Atomically replace JSONL while excluding cooperating appenders and rewriters."""
    with exclusive_lock(path):
        rewrite_jsonl_unlocked(path, records)


def append_jsonl_many_unlocked(
    path: Path, records: Iterable[Mapping[str, Any]]
) -> None:
    """Append objects in one durable batch when the caller already owns the lock."""
    lines = [json.dumps(record, sort_keys=True) for record in records]
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(f"{line}\n" for line in lines))
        handle.flush()
        os.fsync(handle.fileno())


def append_jsonl_unlocked(path: Path, record: Mapping[str, Any]) -> None:
    """Append one object when the caller already owns the file's sidecar lock."""
    append_jsonl_many_unlocked(path, [record])


def append_jsonl_many(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Append JSON objects under the same sidecar lock used by rewriters."""
    with exclusive_lock(path):
        append_jsonl_many_unlocked(path, records)


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    """Append one JSON object under the same sidecar lock used by rewriters."""
    append_jsonl_many(path, [record])
