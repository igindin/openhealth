"""Local agent memory for OpenHealth — stdlib only, no external licenses.

Persistent memory of past agent runs so the next run does not repeat itself
and can see the dynamics ("what we already found").

Storage layout (private, outside the repo):
    ~/.openhealth/memory/entries.jsonl   append-only log, one JSON object per line
    ~/.openhealth/memory/MEMORY.md       human-readable digest, regenerated on write

Entry shape:
    {"ts": "2026-06-10T12:00:00Z", "task": "insight", "summary": "...", "tags": []}

The home directory is resolved per call: explicit ``home`` argument first, then
the ``OPENHEALTH_HOME`` environment variable, then ``~/.openhealth``.

PRIVACY: entries hold condensed personal health conclusions. Files are written
with mode 0600 (dirs 0700) and their contents must never be logged.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

ENTRIES_FILE = "entries.jsonl"
DIGEST_FILE = "MEMORY.md"

MAX_SUMMARY_CHARS = 500
MAX_SUMMARY_SENTENCES = 3
DIGEST_ENTRIES = 30
MAX_ENTRIES_READ = 1000
MAX_MEMORY_BLOCK_CHARS = 1200

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
# Word token for recall scoring: 3+ chars, Latin or Cyrillic. The \u escapes
# cover the Cyrillic block (U+0410-U+042F uppercase, U+0430-U+044F lowercase,
# plus the out-of-range letters YO U+0401 and yo U+0451) - memory summaries may
# be written in Russian, so tokenization must cover both alphabets.
_WORD_RE = re.compile(
    "[0-9A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u0451]"
    "[0-9A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u0451-]{2,}"
)

MEMORY_BLOCK_HEADER = (
    "Memory of past analyses (what has already been found) — do not repeat "
    "yourself, note the change relative to earlier conclusions:"
)


def memory_home(home: "Path | str | None" = None) -> Path:
    """Resolve the memory directory (``<home>/memory``)."""
    if home is None:
        home = os.environ.get("OPENHEALTH_HOME") or "~/.openhealth"
    return Path(home).expanduser() / "memory"


def _ensure_private_dir(mem_dir: Path) -> None:
    mem_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(mem_dir.parent, 0o700)
        os.chmod(mem_dir, 0o700)
    except OSError:
        pass  # exotic FS without chmod — keep going, files still local


def _write_private(path: Path, text: str) -> None:
    """Atomic-ish private write: temp file in the same dir, then replace."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def summarize_result(text: str) -> str:
    """Condense an agent answer: first sentences, capped at 500 chars."""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    sentences = _SENTENCE_SPLIT_RE.split(flat)
    summary = " ".join(sentences[:MAX_SUMMARY_SENTENCES]).strip()
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return summary


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokens(text: str) -> set:
    return {w.lower() for w in _WORD_RE.findall(text or "")}


def load_entries(home: "Path | str | None" = None, limit: "int | None" = None) -> list:
    """Read entries (oldest first), skipping corrupt lines. ``limit`` keeps the tail."""
    path = memory_home(home) / ENTRIES_FILE
    if not path.is_file():
        return []
    entries = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # corrupt line — skip, do not crash
                if isinstance(obj, dict) and isinstance(obj.get("summary"), str):
                    entries.append(obj)
                if len(entries) > MAX_ENTRIES_READ:
                    entries = entries[-MAX_ENTRIES_READ:]
    except OSError:
        return []
    if limit is not None:
        return entries[-limit:]
    return entries


def _render_digest(entries: list) -> str:
    """Human-readable MEMORY.md: the last DIGEST_ENTRIES entries grouped by task."""
    recent = entries[-DIGEST_ENTRIES:]
    lines = [
        "# OpenHealth — agent memory",
        "",
        "Auto-generated from entries.jsonl, do not edit by hand.",
        "Last {} entries (of {} total), grouped by task.".format(len(recent), len(entries)),
        "",
    ]
    by_task: dict = {}
    for entry in recent:
        by_task.setdefault(str(entry.get("task", "?")), []).append(entry)
    for task in sorted(by_task):
        lines.append("## {}".format(task))
        lines.append("")
        for entry in reversed(by_task[task]):  # newest first inside a group
            date = str(entry.get("ts", ""))[:16].replace("T", " ")
            tags = entry.get("tags") or []
            tag_str = " [{}]".format(", ".join(str(t) for t in tags)) if tags else ""
            lines.append("- {}{} — {}".format(date, tag_str, entry.get("summary", "")))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _rewrite(entries: list, mem_dir: Path) -> None:
    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    _write_private(mem_dir / ENTRIES_FILE, payload)
    _write_private(mem_dir / DIGEST_FILE, _render_digest(entries))


def remember(
    task: str,
    result_text: str,
    tags: "list | None" = None,
    home: "Path | str | None" = None,
) -> dict:
    """Store the essence of a finished agent run and refresh MEMORY.md."""
    summary = summarize_result(result_text)
    entry = {
        "ts": _now_ts(),
        "task": str(task),
        "summary": summary,
        "tags": [str(t) for t in (tags or []) if str(t).strip()],
    }
    mem_dir = memory_home(home)
    _ensure_private_dir(mem_dir)
    path = mem_dir / ENTRIES_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)
    _write_private(mem_dir / DIGEST_FILE, _render_digest(load_entries(home)))
    return entry


def recall(
    task: str,
    query: str = "",
    limit: int = 5,
    home: "Path | str | None" = None,
) -> list:
    """Relevant past entries for a task: term-overlap scoring, recency as tiebreak.

    Returns newest-relevant-first, at most ``limit`` entries.
    """
    entries = [e for e in load_entries(home) if e.get("task") == task]
    if not entries:
        return []
    query_words = _tokens(query)
    scored = []
    for idx, entry in enumerate(entries):  # idx grows with recency
        words = _tokens(entry.get("summary", "")) | _tokens(" ".join(entry.get("tags") or []))
        score = len(query_words & words)
        scored.append((score, idx, entry))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [entry for _, _, entry in scored[: max(0, limit)]]


def format_memory_block(entries: list, max_chars: int = MAX_MEMORY_BLOCK_CHARS) -> str:
    """Prompt block with past findings, hard-capped at ``max_chars``."""
    if not entries:
        return ""
    lines = [MEMORY_BLOCK_HEADER]
    for entry in entries:
        date = str(entry.get("ts", ""))[:10]
        line = "- [{} {}] {}".format(date, entry.get("task", "?"), entry.get("summary", ""))
        lines.append(line)
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[: max_chars - 1].rstrip() + "…"
    return block


def forget(older_than_days: float, home: "Path | str | None" = None) -> int:
    """Drop entries older than the cutoff; returns how many were removed."""
    entries = load_entries(home)
    if not entries:
        return 0
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - older_than_days * 86400)
    )
    kept = [e for e in entries if str(e.get("ts", "")) >= cutoff]
    removed = len(entries) - len(kept)
    if removed:
        mem_dir = memory_home(home)
        _ensure_private_dir(mem_dir)
        _rewrite(kept, mem_dir)
    return removed


def clear(home: "Path | str | None" = None) -> int:
    """Wipe the whole memory; returns how many entries were dropped."""
    entries = load_entries(home)
    mem_dir = memory_home(home)
    for name in (ENTRIES_FILE, DIGEST_FILE):
        try:
            (mem_dir / name).unlink()
        except OSError:
            pass
    return len(entries)
