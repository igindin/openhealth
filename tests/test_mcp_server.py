"""Transport tests for openhealth.mcp_server (MCP over stdio, JSON-RPC 2.0).

These pin the wire contract an MCP host actually depends on: the initialize
handshake, tools/list, the tools/call result envelope, notifications getting no
reply, and errors landing in the right place (protocol errors as JSON-RPC
`error`, tool failures in-band as `isError`).

The engine half is exercised only through `journal_checkin`, which needs no
wearable data — every repo root here is a throwaway tmp dir.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from openhealth import mcp_server


def _engine(root):
    return mcp_server.Engine(Path(root))


class TestDispatchHandshake(unittest.TestCase):
    def test_initialize_echoes_the_clients_protocol_version(self):
        """Clients negotiate; answering with a different version fails the handshake."""
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            })
        self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(reply["result"]["serverInfo"]["name"], mcp_server.SERVER_NAME)
        self.assertIn("tools", reply["result"]["capabilities"])

    def test_initialize_without_a_version_uses_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
            })
        self.assertEqual(reply["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)

    def test_ping(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(reply, {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_notifications_are_never_answered(self):
        """A reply to notifications/initialized is a classic handshake breaker."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            self.assertIsNone(mcp_server.dispatch(
                engine, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
            self.assertIsNone(mcp_server.dispatch(
                engine, {"jsonrpc": "2.0", "method": "notifications/cancelled"}))


class TestDispatchTools(unittest.TestCase):
    def test_tools_list_exposes_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = reply["result"]["tools"]
        self.assertTrue(tools)
        for tool in tools:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertNotIn("handler", tool)  # handlers must never cross the wire

    def test_tools_call_returns_the_mcp_content_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "journal_checkin",
                           "arguments": {"behavior_id": "lifestyle.alcohol", "value": False}},
            })
        result = reply["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["type"], "text")
        payload = json.loads(result["content"][0]["text"])  # text must be JSON
        self.assertTrue(payload["saved"])

    def test_failing_tool_is_in_band_not_a_protocol_error(self):
        """The model should read the failure and retry, not see a transport fault."""
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "no-such-tool", "arguments": {}},
            })
        self.assertNotIn("error", reply)
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("no-such-tool", reply["result"]["content"][0]["text"])

    def test_unknown_method_is_a_protocol_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = mcp_server.dispatch(_engine(tmp), {"jsonrpc": "2.0", "id": 5, "method": "nope/nope"})
        self.assertEqual(reply["error"]["code"], -32601)

    def test_empty_capability_lists(self):
        """Hosts probe these; a method-not-found would look like a broken server."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            for method, key in (("resources/list", "resources"), ("prompts/list", "prompts")):
                reply = mcp_server.dispatch(engine, {"jsonrpc": "2.0", "id": 6, "method": method})
                self.assertEqual(reply["result"][key], [])


class TestServeStdio(unittest.TestCase):
    def _run(self, lines):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            mcp_server.serve_stdio(Path(tmp), out=out, in_=io.StringIO("".join(lines)))
        return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]

    def test_full_handshake_over_stdio(self):
        replies = self._run([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n",
        ])
        # exactly two replies: the notification must not produce one
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[0]["jsonrpc"], "2.0")
        self.assertTrue(replies[1]["result"]["tools"])

    def test_malformed_json_reports_parse_error_and_keeps_serving(self):
        replies = self._run([
            "{not json\n",
            json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n",
        ])
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 9)  # loop survived

    def test_non_object_message_is_an_invalid_request(self):
        replies = self._run(["[1, 2, 3]\n"])
        self.assertEqual(replies[0]["error"]["code"], -32600)

    def test_blank_lines_are_ignored(self):
        replies = self._run(["\n", "  \n", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"])
        self.assertEqual(len(replies), 1)


if __name__ == "__main__":
    unittest.main()
