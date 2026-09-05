#!/usr/bin/env python3
"""
session_graphiti_autoseed.py — SessionEnd auto-write of durable session insights to Graphiti.

Complements agent-driven add_memory:
  - Agents may forget to write back after research.
  - This script scans the most recent Claude session transcript (+ last-response cache)
    for high-signal durable content and queues Graphiti episodes.
  - Does NOT replace agent writes; caps volume; skips trivial/ack sessions.

Wired after consolidate_memory / before sync_graph_memory so flush picks them up.

Usage:
  pyenv exec python3 session_graphiti_autoseed.py
  pyenv exec python3 session_graphiti_autoseed.py --dry-run
  pyenv exec python3 session_graphiti_autoseed.py --session /path/to/session.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from harness_paths import DIAGNOSTICS, PROJECTS_DIR, STATE
from state_io import (
    append_jsonl_unlocked,
    atomic_write_json,
    exclusive_locks,
    load_jsonl_objects,
)

PENDING = STATE / "graphiti_pending_episodes.jsonl"
LAST_RESPONSE = STATE / "last-response.txt"
PROJECTS = PROJECTS_DIR
DIAG = DIAGNOSTICS

# Durable-signal language (decisions, schema, root cause, deploys, contracts)
DURABLE_RE = re.compile(
    r"\b("
    r"decided|decision|root cause|fixed by|schema|partition|cluster|"
    r"deployed|merged|infra-before-app|deploy order|data-infra|dataform|"
    r"bronze|silver|gold|datastream|scd2|scd1|test_client_id|"
    r"do not|never |must |mandatory|always |prefer approved cli|"
    r"error \d+|regression|on-call|promotion|PRD|UAT|"
    r"graphiti|bungraph|enforcement|lesson_autogen"
    r")\b",
    re.I,
)

# Skip pure chitchat / harness noise alone
SKIP_ONLY_RE = re.compile(
    r"^(ok|thanks|done\.?|yes\.?|no\.?|lgtm|sgtm)\s*$",
    re.I,
)

WRITE_TOOLS = re.compile(
    r"mcp__(graphiti-memory__add_memory|bungraph__add_episode|bungraph__add_triplet|"
    r"bungraph__add_episode_bulk)",
    re.I,
)
RESEARCH_TOOLS = re.compile(
    r"mcp__(docs-search|graphiti-memory|bungraph|bq-schema|company-context|mem0)|"
    r"^(WebSearch|WebFetch|Grep|Glob|Bash|Task|Agent)$",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def find_latest_session(explicit: Path | None = None) -> Path | None:
    if explicit and explicit.exists():
        return explicit
    candidates: list[Path] = []
    if not PROJECTS.exists():
        return None
    # Prefer home workspace project (exact name) then other project dirs
    preferred = PROJECTS / "-USER-"

    def collect(dirs: list[Path], min_size: int) -> list[Path]:
        out: list[Path] = []
        for d in dirs:
            if not d.is_dir():
                continue
            for f in d.glob("*.jsonl"):
                try:
                    if f.stat().st_size >= min_size:
                        out.append(f)
                except OSError:
                    continue
        return out

    # Prefer home workspace sessions only when available (substantial)
    if preferred.is_dir():
        candidates = collect([preferred], 50_000)
        if not candidates:
            candidates = collect([preferred], 2000)
    if not candidates:
        others = [d for d in PROJECTS.iterdir() if d.is_dir() and d != preferred]
        candidates = collect(others, 50_000) or collect(others, 2000)
    if not candidates:
        return None

    # Score: recency heavily, size lightly
    def score(p: Path) -> float:
        st = p.stat()
        size_bonus = min(st.st_size, 5_000_000) / 5_000_000.0
        return st.st_mtime + size_bonus * 3600

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def extract_session_signals(path: Path, max_assistant_chars: int = 12000) -> dict:
    """Pull tools + trailing assistant text from a Claude session jsonl."""
    tool_groups: list[list[str]] = []
    text_groups: list[list[str]] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as e:
        return {"error": str(e), "tools": [], "text": "", "had_write": False}

    # Stream from end for recent content
    for line in reversed(lines[-4000:]):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        message_tools: list[str] = []
        message_texts: list[str] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and isinstance(b.get("name"), str):
                message_tools.append(b["name"])
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                t = b["text"].strip()
                if t:
                    message_texts.append(t)
        if message_tools:
            tool_groups.append(message_tools)
        if message_texts:
            text_groups.append(message_texts)
        if sum(len(text) for group in text_groups for text in group) >= max_assistant_chars:
            break

    tools = [tool for group in reversed(tool_groups) for tool in group]
    texts = [text for group in reversed(text_groups) for text in group]
    text = "\n\n".join(texts)
    if len(text) > max_assistant_chars:
        text = text[-max_assistant_chars:]
    had_write = any(WRITE_TOOLS.search(n or "") for n in tools)
    research_n = sum(1 for n in tools if RESEARCH_TOOLS.search(n or ""))
    return {
        "tools": tools,
        "text": text,
        "had_write": had_write,
        "research_n": research_n,
        "session": str(path),
        "mtime": path.stat().st_mtime,
    }


def is_durable(text: str) -> bool:
    if not text or len(text.strip()) < 280:
        return False
    if SKIP_ONLY_RE.match(text.strip()):
        return False
    hits = len(DURABLE_RE.findall(text))
    # Need enough signal density
    return hits >= 3


def compress_episode(text: str, max_chars: int = 3500) -> str:
    """Keep high-signal sentences rather than full transcript dump."""
    # Prefer paragraphs with durable keywords
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    scored: list[tuple[int, str]] = []
    for p in paras:
        score = len(DURABLE_RE.findall(p))
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    out: list[str] = []
    n = 0
    for _, p in scored:
        if n + len(p) > max_chars:
            continue
        out.append(p)
        n += len(p)
        if n >= max_chars * 0.7:
            break
    if not out:
        # fallback: tail of text
        return text[-max_chars:]
    body = "\n\n".join(out)
    if len(body) > max_chars:
        body = body[:max_chars]
    return body


def content_hash(body: str) -> str:
    return hashlib.sha256(body[:800].encode()).hexdigest()[:16]


def queue_episode(
    name: str,
    body: str,
    source_description: str,
    digest: str,
    dry: bool,
) -> bool:
    rec = {
        "ts": now_iso(),
        "name": name,
        "episode_body": body,
        "source": "text",
        "source_description": source_description,
        "group_id": "main",
        "status": "pending",
        "origin": "session_graphiti_autoseed",
        "content_hash": digest,
    }
    if dry:
        print(f"[dry-run] would queue {name} ({len(body)} chars)")
        return True
    archive = STATE / "graphiti_flushed_archive.jsonl"
    with exclusive_locks((PENDING, archive)):
        if already_queued_similar(digest):
            return False
        append_jsonl_unlocked(PENDING, rec)
    return True


def already_queued_similar(digest: str) -> bool:
    """Avoid re-queueing content already present in recent archive or pending state."""
    footer = f"content_hash:{digest}"
    for path in (PENDING, STATE / "graphiti_flushed_archive.jsonl"):
        for record in load_jsonl_objects(path).records[-200:]:
            if record.get("content_hash") == digest:
                return True
            body = record.get("episode_body")
            if isinstance(body, str) and footer in body:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session", type=Path, default=None)
    ap.add_argument("--max-episodes", type=int, default=2)
    args = ap.parse_args(argv)

    report: dict = {
        "ts": now_iso(),
        "queued": [],
        "skipped": [],
        "session": None,
    }

    sess = find_latest_session(args.session)
    candidates: list[tuple[str, str, str, str]] = []  # name, body, desc, hash

    if sess:
        report["session"] = str(sess)
        sig = extract_session_signals(sess)
        if sig.get("error"):
            report["skipped"].append(f"session_error:{sig['error']}")
        else:
            text = sig.get("text") or ""
            # Prefer sessions that did research or have durable prose
            if sig.get("had_write"):
                report["skipped"].append("session_already_wrote_graphiti")
            elif is_durable(text) and (
                sig.get("research_n", 0) >= 1 or len(DURABLE_RE.findall(text)) >= 5
            ):
                excerpt = compress_episode(text)
                digest = content_hash(excerpt)
                sid = sess.stem[:8]
                name = f"session-autoseed-{today()}-{sid}"
                header = (
                    f"Auto-seeded from Claude session {sess.name} at {now_iso()}.\n"
                    f"Research tools≈{sig.get('research_n')}. "
                    f"Agent did not call add_memory/add_episode this session "
                    f"(or not detected in tail). Durable excerpts:\n\n"
                )
                candidates.append(
                    (
                        name,
                        header + excerpt,
                        f"session_graphiti_autoseed {sess.name}",
                        digest,
                    )
                )
            else:
                report["skipped"].append("session_not_durable_or_low_signal")

    # Always consider last-response cache as secondary candidate
    if LAST_RESPONSE.exists():
        try:
            lr = LAST_RESPONSE.read_text(errors="replace").strip()
        except OSError:
            lr = ""
        if is_durable(lr):
            excerpt = compress_episode(lr, max_chars=2500)
            digest = content_hash(excerpt)
            name = f"last-response-autoseed-{today()}"
            header = (
                f"Auto-seeded from last-response.txt cache at {now_iso()}.\n\n"
            )
            candidates.append(
                (
                    name,
                    header + excerpt,
                    "session_graphiti_autoseed last-response",
                    digest,
                )
            )
        else:
            report["skipped"].append("last_response_not_durable")

    # Cap + dedupe
    n = 0
    for name, body, desc, digest in candidates:
        if n >= args.max_episodes:
            break
        if queue_episode(name, body, desc, digest, args.dry_run):
            report["queued"].append(name)
            n += 1
        else:
            report["skipped"].append(f"dup:{name}")

    if not candidates:
        report["skipped"].append("no_candidates")

    if not args.dry_run:
        atomic_write_json(DIAG / f"session_graphiti_autoseed_{today()}.json", report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by install smoke tests
    raise SystemExit(main())
