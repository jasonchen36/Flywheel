"""Durable, reusable I/O primitives for Flywheel state files.

State is frequently read by hooks while SessionEnd updates it. Rewrites therefore
use same-directory temporary files plus ``os.replace``; JSONL appends use an
advisory lock when the platform provides ``fcntl``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]


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
    """Return the stable sidecar lock path used for a mutable state file."""
    return path.with_name(f".{path.name}.lock")


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an advisory exclusive lock on a stable sidecar file."""
    lock_path = lock_path_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def append_jsonl_unlocked(path: Path, record: Mapping[str, Any]) -> None:
    """Append one object when the caller already owns the file's sidecar lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    """Append one JSON object under the same sidecar lock used by rewriters."""
    with exclusive_lock(path):
        append_jsonl_unlocked(path, record)
