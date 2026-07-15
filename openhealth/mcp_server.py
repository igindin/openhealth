"""MCP server scaffold — live queries against the OpenHealth engine (stdio).

This exposes the OpenHealth derived layer to an agent (Claude Code / Codex /
any MCP client) as a small set of **live-query tools** over stdio:

- ``today`` / ``recovery`` — today's (or a given day's) recovery score, strain
  and sleep debt, assembled from indexed WHOOP records.
- ``journal_checkin`` — log a daily behavior check-in (boolean or scalar) into
  the index, feeding the correlations loop.
- ``correlations`` — current behavior→recovery impact prompts (graded, capped).
- ``ask`` — natural-language question answered from the local context files.

Layout
------
1. ``Engine`` + the tool registry (``list_tools`` / ``call_tool``) — pure-stdlib
   tool implementations and their JSON Schemas, unit-test friendly and free of
   any transport concern.
2. ``dispatch`` + ``serve_stdio`` — a spec-compliant MCP transport speaking
   JSON-RPC 2.0 over stdio.

No SDK is required: the stdio transport is small enough to implement directly
against the wire format, so the core rule (zero runtime dependencies) holds.
See ``docs/mcp.md`` for registering it with an MCP host.
"""

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

SERVER_NAME = "openhealth"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Engine: pure-stdlib live-query logic. No MCP/SDK imports here on purpose.
# ---------------------------------------------------------------------------

class Engine:
    """Thin adapter from MCP tool calls to the OpenHealth engine.

    One instance is bound to a repository root. Every method returns a plain
    JSON-serializable dict, ready to become an MCP tool result. The engine only
    ever reads/writes through ``openhealth.index`` and the domain modules — it
    never touches raw source files.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._paths = None

    # -- lazy workspace wiring ------------------------------------------------
    def _ensure(self):
        from . import index
        from .storage import ensure_repo_structure

        if self._paths is None:
            self._paths = ensure_repo_structure(self.root)
            index.init_db(self._paths.db_path)
        return self._paths

    def _today(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).date().isoformat()

    # -- tools ---------------------------------------------------------------
    def recovery(self, day: Optional[str] = None, baseline_window_days: int = 60) -> Dict[str, Any]:
        """Today's (or ``day``'s) recovery score / strain / sleep debt.

        Computes from indexed WHOOP records without persisting (a read-only live
        query). Returns ``available: False`` with a reason when HRV is missing,
        rather than raising — the agent can relay that cleanly.
        """
        from . import modules as modpkg

        modpkg.load_builtin()
        from .modules import recovery

        paths = self._ensure()
        day = day or self._today()
        payload = recovery.from_index(paths.db_path, day, baseline_window_days=baseline_window_days)
        if payload.get("hrv_ms") is None or payload.get("baseline_hrv_ms") is None:
            return {
                "available": False,
                "date": day,
                "reason": "no HRV / baseline in the index for this day",
            }
        result = modpkg.get_module("recovery").compute(payload)
        return {
            "available": True,
            "date": day,
            "metrics": result.metrics,
            "notes": result.notes,
        }

    def journal_checkin(
        self,
        behavior_id: str,
        value: Any,
        day: Optional[str] = None,
        category: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a daily behavior check-in into the index (feeds correlations).

        ``value`` is typically a boolean (did the behavior happen) but a scalar
        is accepted too. The record is upserted with a deterministic id so the
        same behavior on the same day overwrites rather than duplicates.
        """
        from . import index

        paths = self._ensure()
        day = day or self._today()
        record = {
            "id": "obs-journal-%s-%s" % (day, behavior_id),
            "record_type": "Observation",
            "source_id": "journal",
            "title": "Journal check-in: %s" % behavior_id,
            "summary": (note or "Logged %s = %r on %s." % (behavior_id, value, day)),
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 0.9,
            "date": day,
            "tags": ["journal", "checkin"],
            "metadata": {"behavior_id": behavior_id, "category": category or "unknown"},
            "observation_kind": "journal_entry",
            "metric_name": behavior_id,
            "value": value,
        }
        index.upsert_record(paths.db_path, record)
        return {"saved": True, "record_id": record["id"], "date": day, "behavior_id": behavior_id}

    def correlations(self, window_days: int = 90, as_of: Optional[str] = None) -> Dict[str, Any]:
        """Current behavior→recovery impact prompts (graded, capped, read-only)."""
        from . import modules as modpkg

        modpkg.load_builtin()
        from .modules import correlations

        paths = self._ensure()
        behaviors = correlations.from_index(paths.db_path, window_days=window_days, as_of=as_of)
        result = modpkg.get_module("correlations").compute(
            {"behaviors": behaviors, "window_days": window_days}
        )
        return {
            "window_days": window_days,
            "behaviors_considered": len(behaviors),
            "insights": result.insights,
            "notes": result.notes,
        }

    def ask(self, question: str, max_tokens: int = 900) -> Dict[str, Any]:
        """Answer a natural-language question from local context files.

        Reuses ``openhealth.ask``: with an Anthropic key set it returns a cited
        answer; without one it returns the offline prompt bundle the agent can
        answer itself. Output is captured to a string (no terminal streaming).
        """
        import io

        from . import ask as ask_mod

        self._ensure()
        buffer = io.StringIO()
        err = io.StringIO()
        code = ask_mod.run_ask(
            self.root, question, stream=False, out=buffer, err=err, max_tokens=max_tokens
        )
        return {
            "ok": code == 0,
            "answer": buffer.getvalue().strip(),
            "error": err.getvalue().strip() or None,
        }


# ---------------------------------------------------------------------------
# Tool registry: declarative schemas + dispatch into the Engine.
# ---------------------------------------------------------------------------

def _tool_specs() -> List[Dict[str, Any]]:
    """MCP-style tool declarations (name, description, JSON Schema input)."""
    return [
        {
            "name": "today",
            "description": "Today's recovery score, strain and sleep debt from indexed WHOOP data.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "baseline_window_days": {"type": "integer", "default": 60},
                },
            },
            "handler": lambda engine, args: engine.recovery(
                day=None, baseline_window_days=int(args.get("baseline_window_days", 60))
            ),
        },
        {
            "name": "recovery",
            "description": "Recovery score / strain / sleep debt for a given day (default today).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "day": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                    "baseline_window_days": {"type": "integer", "default": 60},
                },
            },
            "handler": lambda engine, args: engine.recovery(
                day=args.get("day"), baseline_window_days=int(args.get("baseline_window_days", 60))
            ),
        },
        {
            "name": "journal_checkin",
            "description": "Log a daily behavior check-in (boolean/scalar) into the index.",
            "inputSchema": {
                "type": "object",
                "required": ["behavior_id", "value"],
                "properties": {
                    "behavior_id": {"type": "string"},
                    "value": {"description": "Usually a boolean; scalar accepted."},
                    "day": {"type": "string", "description": "ISO date YYYY-MM-DD (default today)."},
                    "category": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
            "handler": lambda engine, args: engine.journal_checkin(
                behavior_id=args["behavior_id"],
                value=args["value"],
                day=args.get("day"),
                category=args.get("category"),
                note=args.get("note"),
            ),
        },
        {
            "name": "correlations",
            "description": "Current behavior→recovery impact prompts (graded and capped).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "window_days": {"type": "integer", "default": 90},
                    "as_of": {"type": "string", "description": "ISO date to evaluate as of (default today)."},
                },
            },
            "handler": lambda engine, args: engine.correlations(
                window_days=int(args.get("window_days", 90)), as_of=args.get("as_of")
            ),
        },
        {
            "name": "ask",
            "description": "Answer a natural-language question from local context files.",
            "inputSchema": {
                "type": "object",
                "required": ["question"],
                "properties": {
                    "question": {"type": "string"},
                    "max_tokens": {"type": "integer", "default": 900},
                },
            },
            "handler": lambda engine, args: engine.ask(
                question=args["question"], max_tokens=int(args.get("max_tokens", 900))
            ),
        },
    ]


def list_tools() -> List[Dict[str, Any]]:
    """Public tool catalog (no handlers), suitable for an MCP ``list_tools``."""
    return [{k: spec[k] for k in ("name", "description", "inputSchema")} for spec in _tool_specs()]


def _dispatch_table(engine: Engine) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    return {spec["name"]: (lambda args, _h=spec["handler"]: _h(engine, args)) for spec in _tool_specs()}


def call_tool(engine: Engine, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Invoke a tool by name. Raises ``KeyError`` for an unknown tool."""
    table = _dispatch_table(engine)
    if name not in table:
        raise KeyError("unknown tool %r; known: %s" % (name, ", ".join(sorted(table))))
    return table[name](arguments or {})


# ---------------------------------------------------------------------------
# Transport: MCP over stdio (JSON-RPC 2.0), stdlib only.
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2025-06-18"


def dispatch(engine: Engine, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message.

    Returns the reply object, or ``None`` when the message is a notification
    (no ``id``) — per JSON-RPC those must never be answered. Sending a reply to
    ``notifications/initialized`` is the classic way to break a handshake.
    """
    method = message.get("method")
    has_id = "id" in message
    mid = message.get("id")

    if method == "initialize":
        # Echo back the client's protocol version when it offers one: clients
        # negotiate, and answering with a different version fails the handshake.
        requested = (message.get("params") or {}).get("protocolVersion")
        result: Dict[str, Any] = {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": list_tools()}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            data = call_tool(engine, params.get("name"), params.get("arguments") or {})
            result = {
                "content": [
                    {"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}
                ],
                "isError": False,
            }
        except Exception as exc:
            # A failing tool is an in-band result, not a protocol error — the
            # model is meant to read the message and decide what to do next.
            result = {
                "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
                "isError": True,
            }
    elif not has_id:
        return None  # unrecognized notification — nothing to answer
    else:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method},
        }

    return {"jsonrpc": "2.0", "id": mid, "result": result} if has_id else None


def _write(out: TextIO, payload: Dict[str, Any]) -> bool:
    """Write one JSON-RPC line. ``False`` means the client's pipe is gone."""
    try:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()
        return True
    except BrokenPipeError:
        return False


def serve_stdio(root: Path, *, out: Optional[TextIO] = None, in_: Optional[TextIO] = None) -> int:
    """Serve the engine over stdio as a spec-compliant MCP server.

    Newline-delimited JSON-RPC 2.0 covering ``initialize`` / ``tools/list`` /
    ``tools/call`` / ``ping``, with notifications correctly left unanswered and
    tool failures returned in-band. Pure stdlib: no SDK, so the zero-runtime-
    dependency rule holds.
    """
    engine = Engine(root)
    out = out or sys.stdout
    in_ = in_ or sys.stdin

    for raw in in_:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            if not _write(out, {"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": "parse error: %s" % exc}}):
                break
            continue
        if not isinstance(message, dict):
            if not _write(out, {"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32600, "message": "invalid request"}}):
                break
            continue
        try:
            reply = dispatch(engine, message)
        except Exception as exc:  # one bad message must never kill the server
            if "id" not in message:
                continue
            reply = {"jsonrpc": "2.0", "id": message.get("id"),
                     "error": {"code": -32603, "message": "internal error: %s" % exc}}
        if reply is not None and not _write(out, reply):
            break
    return 0


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """``python3 -m openhealth.mcp_server [--repo-root .] [--list-tools]``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="openhealth.mcp_server",
        description="MCP server scaffold (stdio) for live OpenHealth queries.",
    )
    parser.add_argument("--repo-root", default=".", help="Path to the OpenHealth repository root.")
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the tool catalog as JSON and exit (no server).",
    )
    args = parser.parse_args(argv)

    if args.list_tools:
        print(json.dumps({"server": SERVER_NAME, "version": SERVER_VERSION, "tools": list_tools()},
                         indent=2, ensure_ascii=False))
        return 0

    return serve_stdio(Path(args.repo_root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
