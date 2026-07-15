"""Unit tests for the Hermes-proxy LLM path (OpenAI-compatible over HTTP).

OpenHealth can use Hermes as an LLM backend via `hermes proxy start` instead of
the interactive `hermes -z` one-shot (which can block on gateway startup). When
a base_url is configured (agent.json or OPENHEALTH_LLM_BASE_URL), the `hermes`
agent goes over HTTP. No real network: urllib is mocked.
"""

import importlib.util
import io
import json
from pathlib import Path

import urllib.error
import urllib.request

_SERVER_PATH = Path(__file__).resolve().parent.parent / "ui" / "web" / "server.py"
_spec = importlib.util.spec_from_file_location("bridge_server_proxy", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


class _FakeResp:
    def __init__(self, text):
        self._d = text.encode("utf-8")

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_sanitize_base_url():
    assert server.sanitize_base_url("http://hermes:8645") == "http://hermes:8645"
    assert server.sanitize_base_url("https://x.example/v1") == "https://x.example/v1"
    assert server.sanitize_base_url("  http://h:8645  ") == "http://h:8645"
    assert server.sanitize_base_url("ftp://x") is None
    assert server.sanitize_base_url("not a url") is None
    assert server.sanitize_base_url(None) is None
    assert server.sanitize_base_url("") is None


def test_openai_chat_url_normalization():
    f = server._openai_chat_url
    assert f("http://h:8645") == "http://h:8645/v1/chat/completions"
    assert f("http://h:8645/") == "http://h:8645/v1/chat/completions"
    assert f("http://h:8645/v1") == "http://h:8645/v1/chat/completions"
    assert f("http://h:8645/v1/chat/completions") == "http://h:8645/v1/chat/completions"


def test_run_openai_chat_parses_content(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["auth"] = req.get_header("Authorization")
        return _FakeResp(json.dumps({"choices": [{"message": {"content": "Recovery looks moderate."}}]}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = server._run_openai_chat("analyze my recovery", "http://hermes:8645", model="glm-5.2")
    assert out["status"] == "ok"
    assert out["agent"] == "hermes"
    assert out["result"] == "Recovery looks moderate."
    assert captured["url"] == "http://hermes:8645/v1/chat/completions"
    assert captured["body"]["model"] == "glm-5.2"
    assert captured["body"]["messages"][0]["content"] == "analyze my recovery"
    assert captured["auth"].startswith("Bearer ")


def test_run_agent_hermes_uses_proxy_not_cli(monkeypatch, tmp_path):
    # base_url + agent=hermes -> HTTP path; the CLI must never run.
    def boom(*a, **k):
        raise AssertionError("CLI must not run when the proxy is configured")

    monkeypatch.setattr(server.subprocess, "run", boom)
    monkeypatch.setattr(server.shutil, "which", lambda b: None)  # hermes binary absent

    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps({"choices": [{"message": {"content": "ok via proxy"}}]}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = {"agent": "hermes", "model": None, "extra_args": [], "base_url": "http://hermes:8645"}
    out = server.run_agent("p", tmp_path, config=cfg)
    assert out["status"] == "ok"
    assert out["result"] == "ok via proxy"


def test_run_openai_chat_http_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"upstream down"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = server._run_openai_chat("p", "http://hermes:8645")
    assert out["status"] == "error"
    assert "502" in out["message"]


def test_run_openai_chat_empty_response(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps({"choices": [{"message": {"content": ""}}]}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = server._run_openai_chat("p", "http://hermes:8645")
    assert out["status"] == "error"
    assert "empty" in out["message"]


def test_base_url_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENHEALTH_HOME", str(tmp_path / "oh"))
    monkeypatch.setenv("OPENHEALTH_LLM_BASE_URL", "http://hermes:8645")
    cfg = server.load_agent_config()
    assert cfg["base_url"] == "http://hermes:8645"


def test_config_post_accepts_and_clears_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENHEALTH_HOME", str(tmp_path / "oh"))
    monkeypatch.delenv("OPENHEALTH_LLM_BASE_URL", raising=False)
    status, body = server.handle_config_request({"base_url": "http://hermes:8645"})
    assert status == 200
    assert body["config"]["base_url"] == "http://hermes:8645"
    status, body = server.handle_config_request({"base_url": "junk"})
    assert status == 400
    status, body = server.handle_config_request({"base_url": None})
    assert status == 200
    assert body["config"]["base_url"] is None
