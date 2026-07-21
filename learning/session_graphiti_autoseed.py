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
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".claude/MEMORY/STATE"
LEARNING = HOME / ".claude/MEMORY/LEARNING"
PENDING = STATE / "graphiti_pending_episodes.jsonl"
LAST_RESPONSE = STATE / "last-response.txt"
PROJECTS = HOME / ".claude/projects"
DIAG = LEARNING / "DIAGNOSTICS"

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
    tools: list[str] = []
    texts: list[str] = []
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
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and isinstance(b.get("name"), str):
                tools.append(b["name"])
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                t = b["text"].strip()
                if t:
                    texts.append(t)
        if sum(len(t) for t in texts) >= max_assistant_chars:
            break

    texts.reverse()  # chronological among collected
    tools.reverse()
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


def queue_episode(name: str, body: str, source_description: str, dry: bool) -> None:
    rec = {
        "ts": now_iso(),
        "name": name,
        "episode_body": body,
        "source": "text",
        "source_description": source_description,
        "group_id": "main",
        "status": "pending",
        "origin": "session_graphiti_autoseed",
    }
    if dry:
        print(f"[dry-run] would queue {name} ({len(body)} chars)")
        return
    STATE.mkdir(parents=True, exist_ok=True)
    with PENDING.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def already_queued_similar(body: str) -> bool:
    """Avoid re-queueing same content in last archive/pending."""
    h = hashlib.sha256(body[:800].encode()).hexdigest()[:16]
    for path in (PENDING, STATE / "graphiti_flushed_archive.jsonl"):
        if not path.exists():
            continue
        try:
            for line in path.read_text().splitlines()[-200:]:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                b = d.get("episode_body") or ""
                if hashlib.sha256(b[:800].encode()).hexdigest()[:16] == h:
                    return True
                if d.get("content_hash") == h:
                    return True
        except OSError:
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session", type=Path, default=None)
    ap.add_argument("--max-episodes", type=int, default=2)
    args = ap.parse_args()

    report: dict = {
        "ts": now_iso(),
        "queued": [],
        "skipped": [],
        "session": None,
    }

    sess = find_latest_session(args.session)
    candidates: list[tuple[str, str, str]] = []  # name, body, desc

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
                body = compress_episode(text)
                sid = sess.stem[:8]
                name = f"session-autoseed-{today()}-{sid}"
                header = (
                    f"Auto-seeded from Claude session {sess.name} at {now_iso()}.\n"
                    f"Research tools≈{sig.get('research_n')}. "
                    f"Agent did not call add_memory/add_episode this session "
                    f"(or not detected in tail). Durable excerpts:\n\n"
                )
                candidates.append(
                    (name, header + body, f"session_graphiti_autoseed {sess.name}")
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
            body = compress_episode(lr, max_chars=2500)
            name = f"last-response-autoseed-{today()}"
            header = (
                f"Auto-seeded from last-response.txt cache at {now_iso()}.\n\n"
            )
            candidates.append(
                (name, header + body, "session_graphiti_autoseed last-response")
            )
        else:
            report["skipped"].append("last_response_not_durable")

    # Cap + dedupe
    n = 0
    for name, body, desc in candidates:
        if n >= args.max_episodes:
            break
        if already_queued_similar(body):
            report["skipped"].append(f"dup:{name}")
            continue
        # attach content hash for future dedupe
        if not args.dry_run:
            # queue_episode doesn't store hash; embed in body footer
            h = hashlib.sha256(body[:800].encode()).hexdigest()[:16]
            body = body + f"\n\n<!-- content_hash:{h} -->"
        queue_episode(name, body, desc, args.dry_run)
        report["queued"].append(name)
        n += 1

    if not candidates:
        report["skipped"].append("no_candidates")

    DIAG.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        (DIAG / f"session_graphiti_autoseed_{today()}.json").write_text(
            json.dumps(report, indent=2)
        )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
