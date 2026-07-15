"""Bot -> bridge real-time indexing (`--bridge-url`): plain intake is POSTed to
/api/intake so it lands in the health index immediately, degrading to disk-only
when the bridge is offline. urllib is mocked; no network."""

import json

import openhealth.telegram_bot as tb


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return 200


def _bot(tmp_path, bridge_url):
    cfg = tb.BotConfig(data_dir=tmp_path, bridge_url=bridge_url)
    return tb.Bot(api=object(), config=cfg)


def test_push_to_bridge_posts_when_url_set(tmp_path, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return _FakeResp()

    monkeypatch.setattr(tb.urllib.request, "urlopen", fake_urlopen)
    bot = _bot(tmp_path, "http://127.0.0.1:8770/")  # trailing slash trimmed
    env = {
        "submission_id": "tg-1", "submitted_at": "2026-07-07T00:00:00+00:00",
        "channel": "telegram", "author": "i", "type": "text", "text": "hi",
    }
    assert bot._push_to_bridge(env) is True
    assert captured["url"] == "http://127.0.0.1:8770/api/intake"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == env


def test_push_to_bridge_noop_without_url(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(tb.urllib.request, "urlopen",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    bot = _bot(tmp_path, None)
    assert bot._push_to_bridge({"submission_id": "x"}) is False
    assert called["n"] == 0  # nothing sent when the bridge is not configured


def test_push_to_bridge_swallows_errors(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise tb.urllib.error.URLError("down")

    monkeypatch.setattr(tb.urllib.request, "urlopen", boom)
    bot = _bot(tmp_path, "http://127.0.0.1:9999")
    # Bridge offline: never raises, disk copy remains the durable path.
    assert bot._push_to_bridge({"submission_id": "x"}) is False
