#!/usr/bin/env python3
"""
flush_graphiti_pending.py — drain STATE/graphiti_pending_episodes.jsonl into Graphiti Neo4j.

Uses the always-on Graphiti MCP streamable-HTTP server (default http://127.0.0.1:8000/mcp)
so SessionEnd can push harness episodes without spawning a second Neo4j client/LLM stack.

Flow:
  1. Read pending queue (jsonl)
  2. Dedupe by episode name (keep latest)
  3. MCP initialize → tools/call add_memory for each pending item
  4. Rewrite queue: only failures remain as status=pending; successes → archive jsonl

Usage:
  pyenv exec python3 flush_graphiti_pending.py
  pyenv exec python3 flush_graphiti_pending.py --dry-run
  GRAPHITI_MCP_URL=http://127.0.0.1:8000/mcp pyenv exec python3 flush_graphiti_pending.py
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from harness_paths import GRAPHITI_MCP_URL, HARNESS_HOME
from state_io import (
    append_jsonl,
    atomic_write_json,
    exclusive_lock,
    load_jsonl_objects,
    rewrite_jsonl_unlocked,
)

STATE = HARNESS_HOME / "MEMORY/STATE"
PENDING = STATE / "graphiti_pending_episodes.jsonl"
ARCHIVE = STATE / "graphiti_flushed_archive.jsonl"
DIAG = HARNESS_HOME / "MEMORY/LEARNING/DIAGNOSTICS"
DEFAULT_URL = GRAPHITI_MCP_URL


def normalize_mcp_url(url: str) -> str:
    """Validate an HTTP(S) Graphiti endpoint and ensure it targets ``/mcp``."""
    value = url.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("GRAPHITI_MCP_URL must use http or https")
    if not parsed.hostname:
        raise ValueError("GRAPHITI_MCP_URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("GRAPHITI_MCP_URL must not contain embedded credentials")
    if parsed.fragment:
        raise ValueError("GRAPHITI_MCP_URL must not contain a URL fragment")

    path = parsed.path.rstrip("/")
    if not path:
        path = "/mcp"
    elif path != "/mcp":
        raise ValueError("GRAPHITI_MCP_URL path must be /mcp or omitted")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class GraphitiMCPHttp:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 60.0):
        self.url = normalize_mcp_url(url)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.session_id: str | None = None
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, body: dict, with_session: bool = True) -> tuple[int, list[dict]]:
        data = json.dumps(body).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if with_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.url, data=data, headers=headers, method="POST"
        )
        try:
            # URL is constrained to an absolute HTTP(S) /mcp endpoint in __init__.
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                if not self.session_id:
                    self.session_id = (
                        resp.headers.get("mcp-session-id")
                        or resp.headers.get("Mcp-Session-Id")
                    )
                raw = resp.read().decode("utf-8", errors="replace")
                msgs: list[dict] = []
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        try:
                            msgs.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
                # some servers return plain JSON
                if not msgs and raw.strip().startswith("{"):
                    try:
                        msgs.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
                return resp.status, msgs
        except urllib.error.HTTPError as e:
            body_b = e.read() if hasattr(e, "read") else b""
            raise RuntimeError(f"HTTP {e.code}: {body_b[:300]!r}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Graphiti MCP unreachable at {self.url}: {e}") from e

    def connect(self) -> None:
        status, msgs = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "flush_graphiti_pending",
                        "version": "1.0",
                    },
                },
            },
            with_session=False,
        )
        if status != 200 or not self.session_id:
            raise RuntimeError(
                f"initialize failed status={status} session={self.session_id} msgs={msgs[:1]}"
            )
        # required notification
        try:
            self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                with_session=True,
            )
        except Exception:
            # some servers return 202 with empty body — ok
            pass

    def add_memory(
        self,
        name: str,
        episode_body: str,
        group_id: str = "main",
        source: str = "text",
        source_description: str = "",
    ) -> dict:
        status, msgs = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": "add_memory",
                    "arguments": {
                        "name": name,
                        "episode_body": episode_body,
                        "group_id": group_id or "main",
                        "source": source or "text",
                        "source_description": source_description or "",
                    },
                },
            }
        )
        if status != 200:
            raise RuntimeError(f"tools/call HTTP {status}")
        if not msgs:
            raise RuntimeError("empty MCP response")
        msg = msgs[-1]
        if "error" in msg:
            raise RuntimeError(str(msg["error"]))
        result = msg.get("result") or {}
        if result.get("isError"):
            raise RuntimeError(str(result.get("content")))
        return result


def load_pending() -> list[dict]:
    return load_jsonl_objects(PENDING).records


def dedupe_latest(rows: list[dict]) -> list[dict]:
    """Keep latest row per name; only pending-ish statuses."""
    by_name: dict[str, dict] = {}
    for r in rows:
        status = (r.get("status") or "pending").lower()
        if status in ("flushed", "done", "ok"):
            continue
        name = r.get("name") or ""
        if not name or not r.get("episode_body"):
            continue
        by_name[name] = r  # last wins
    return list(by_name.values())


def append_archive(rows: list[dict]) -> None:
    for row in rows:
        append_jsonl(ARCHIVE, row)


def rewrite_pending(remaining: list[dict]) -> None:
    rewrite_jsonl_unlocked(PENDING, remaining)


def flush_pending(args: argparse.Namespace) -> int:
    """Drain the queue while excluding cooperating producers and rewriters."""
    with exclusive_lock(PENDING):
        rows = load_pending()
        todo = dedupe_latest(rows)[: args.limit]
        report = {
            "ts": now_iso(),
            "url": args.url,
            "queued": len(rows),
            "to_flush": len(todo),
            "flushed": [],
            "failed": [],
            "skipped": 0,
        }

        if not todo:
            print(json.dumps({**report, "message": "queue empty"}, indent=2))
            return 0

        if args.dry_run:
            report["dry_run_names"] = [row.get("name") for row in todo]
            print(json.dumps(report, indent=2))
            return 0

        try:
            client = GraphitiMCPHttp(url=args.url)
            client.connect()
        except Exception as exc:
            report["error"] = f"connect_failed: {exc}"
            print(json.dumps(report, indent=2))
            return 2

        ok_rows: list[dict] = []
        failed_rows: list[dict] = []
        for row in todo:
            name = row.get("name") or "unnamed"
            try:
                client.add_memory(
                    name=name,
                    episode_body=row.get("episode_body") or "",
                    group_id=row.get("group_id") or "main",
                    source=row.get("source") or "text",
                    source_description=row.get("source_description")
                    or f"flush_graphiti_pending {today()}",
                )
                ok_rows.append(
                    {
                        **row,
                        "status": "flushed",
                        "flushed_at": now_iso(),
                        "flush_url": args.url,
                    }
                )
                report["flushed"].append(name)
            except Exception as exc:
                failed_rows.append(
                    {
                        **row,
                        "status": "pending",
                        "last_error": str(exc),
                        "last_attempt": now_iso(),
                    }
                )
                report["failed"].append({"name": name, "error": str(exc)[:200]})

        append_archive(ok_rows)
        todo_names = {(row.get("name") or "") for row in todo}
        unprocessed = [
            row
            for row in dedupe_latest(rows)
            if (row.get("name") or "") not in todo_names
        ]
        rewrite_pending(failed_rows + unprocessed)

    atomic_write_json(DIAG / f"flush_graphiti_pending_{today()}.json", report)
    print(json.dumps(report, indent=2))
    return 1 if report["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Flush pending Graphiti episodes via MCP HTTP")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url", default=DEFAULT_URL, help="Graphiti MCP streamable HTTP URL")
    parser.add_argument("--limit", type=int, default=20, help="max episodes per run")
    return flush_pending(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
