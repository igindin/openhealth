# MCP server

`openhealth.mcp_server` exposes the OpenHealth derived layer to an MCP client
(Claude Code, Codex, any MCP host) as a small set of **live-query tools** over
stdio.

## Status

Working and spec-compliant, split into two clean halves:

- **Engine + tool registry** (`Engine`, `list_tools()`, `call_tool()`) — pure
  stdlib, unit-test friendly, free of any transport concern.
- **Transport** (`dispatch()`, `serve_stdio()`) — MCP over stdio, JSON-RPC 2.0,
  also pure stdlib.

No SDK is needed: the stdio wire format is small enough to implement directly
against the spec, so OpenHealth's core rule (zero runtime dependencies) holds.
The `mcp` package is **not** required and is **not** a dependency.

## Tools

| Tool | What it does |
|------|--------------|
| `today` | Today's recovery score, strain and sleep debt from indexed WHOOP data. |
| `recovery` | Same, for a given `day` (ISO date). |
| `journal_checkin` | Log a daily behavior check-in (boolean/scalar) into the index — feeds the correlations loop. |
| `correlations` | Current behavior→recovery impact prompts (graded and capped by the evidence scale). |
| `ask` | Answer a natural-language question from the local context files. |

Inspect the catalog (no server needed):

```bash
python3 -m openhealth.mcp_server --list-tools
```

## Run it

```bash
python3 -m openhealth.mcp_server --repo-root /path/to/openhealth
```

The server speaks newline-delimited JSON-RPC 2.0 on stdio — one JSON object per
line in, one reply per line out:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"today","arguments":{}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"journal_checkin","arguments":{"behavior_id":"lifestyle.alcohol","value":true}}}
{"jsonrpc":"2.0","id":5,"method":"ping"}
```

Notifications (messages with no `id`) get no reply, as the spec requires.

Errors land in the right place: a failing **tool** comes back in-band as
`isError: true` with the message in `content`, so the agent can read it and
retry — only **protocol** faults use JSON-RPC `error` (`-32700` parse,
`-32600` invalid request, `-32601` unknown method, `-32603` internal). Neither
crashes the loop.

## Register with an MCP client

Point your MCP host at the stdio command. For a Claude Code / generic
`mcpServers` config:

```json
{
  "mcpServers": {
    "openhealth": {
      "command": "python3",
      "args": ["-m", "openhealth.mcp_server", "--repo-root", "/path/to/openhealth"]
    }
  }
}
```

## Design notes

- The engine reads and writes **only** through `openhealth.index` and the domain
  modules — never the raw source files.
- `recovery` / `today` are read-only live queries; they do not persist. They
  return `{"available": false, "reason": ...}` when HRV is missing for the day,
  so the agent can relay that cleanly instead of erroring.
- `correlations` stays graded and capped by `openhealth.evidence`; nothing here
  diagnoses or prescribes. Outputs are prompts for review.
