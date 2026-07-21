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
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".claude/MEMORY/STATE"
PENDING = STATE / "graphiti_pending_episodes.jsonl"
ARCHIVE = STATE / "graphiti_flushed_archive.jsonl"
DIAG = HOME / ".claude/MEMORY/LEARNING/DIAGNOSTICS"
DEFAULT_URL = os.environ.get("GRAPHITI_MCP_URL", "http://127.0.0.1:8000/mcp")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class GraphitiMCPHttp:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 60.0):
        self.url = url.rstrip("/") if url.endswith("/") and not url.endswith("/mcp/") else url
        if not self.url.endswith("/mcp") and not self.url.endswith("/mcp/"):
            # allow base host:port
            if self.url.count("/") <= 2:
                self.url = self.url.rstrip("/") + "/mcp"
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
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
    if not PENDING.exists():
        return []
    out: list[dict] = []
    for line in PENDING.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


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
    if not rows:
        return
    STATE.mkdir(parents=True, exist_ok=True)
    with ARCHIVE.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def rewrite_pending(remaining: list[dict]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if not remaining:
        if PENDING.exists():
            PENDING.write_text("")
        return
    PENDING.write_text("\n".join(json.dumps(r) for r in remaining) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Flush pending Graphiti episodes via MCP HTTP")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--url", default=DEFAULT_URL, help="Graphiti MCP streamable HTTP URL")
    ap.add_argument("--limit", type=int, default=20, help="max episodes per run")
    args = ap.parse_args()

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
        report["dry_run_names"] = [r.get("name") for r in todo]
        print(json.dumps(report, indent=2))
        return 0

    try:
        client = GraphitiMCPHttp(url=args.url)
        client.connect()
    except Exception as e:
        report["error"] = f"connect_failed: {e}"
        print(json.dumps(report, indent=2))
        # leave queue intact
        return 2

    ok_rows: list[dict] = []
    fail_rows: list[dict] = []

    for r in todo:
        name = r.get("name") or "unnamed"
        try:
            client.add_memory(
                name=name,
                episode_body=r.get("episode_body") or "",
                group_id=r.get("group_id") or "main",
                source=r.get("source") or "text",
                source_description=r.get("source_description")
                or f"flush_graphiti_pending {today()}",
            )
            flushed = {
                **r,
                "status": "flushed",
                "flushed_at": now_iso(),
                "flush_url": args.url,
            }
            ok_rows.append(flushed)
            report["flushed"].append(name)
        except Exception as e:
            failed = {**r, "status": "pending", "last_error": str(e), "last_attempt": now_iso()}
            fail_rows.append(failed)
            report["failed"].append({"name": name, "error": str(e)[:200]})

    append_archive(ok_rows)
    # Keep unprocessed pending when --limit truncates the queue (do not drop them).
    todo_names = {(r.get("name") or "") for r in todo}
    unprocessed = [
        r
        for r in dedupe_latest(rows)
        if (r.get("name") or "") not in todo_names
    ]
    rewrite_pending(fail_rows + unprocessed)

    DIAG.mkdir(parents=True, exist_ok=True)
    (DIAG / f"flush_graphiti_pending_{today()}.json").write_text(
        json.dumps(report, indent=2)
    )
    print(json.dumps(report, indent=2))
    # 0 if all ok, 1 if partial, 2 if connect fail (handled above)
    if report["failed"] and not report["flushed"]:
        return 1
    if report["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
