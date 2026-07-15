"""Tests for the live Oura Cloud API v2 OAuth2 connector.

All network is mocked (``oura_live.urlopen`` patched); fixtures are synthetic and
model the public Oura API v2 shapes. Coverage:

  * auth-URL building (authorize endpoint + OAuth params + space-joined scopes),
  * token exchange (mocked token response -> normalized tokens),
  * token refresh (refresh_token grant; rotated refresh token kept),
  * ensure_valid_tokens (fresh token left alone; near-expiry refreshed + saved),
  * an oura-sync-style mapping of synthetic daily_readiness / daily_sleep / sleep
    payloads into canonical Observations with the dashboard metric_names
    (recovery_score, sleep_performance_percentage, hrv_rmssd_milli,
    resting_heart_rate, sleep_duration_h).

Run directly:  PYTHONPATH=$PWD python3 tests/test_oura_live.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from openhealth import index
from openhealth.connectors import oura_live

# --------------------------------------------------------------------------- #
# Network fakes
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(responses, calls):
    """Sequential fake: records (url, method, parsed form body, headers) per call."""
    queue = list(responses)

    def opener(request, timeout=0):
        calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": parse_qs((request.data or b"").decode("utf-8")),
                "headers": dict(request.header_items()),
            }
        )
        return _FakeResponse(queue.pop(0))

    return opener


def _creds(scopes=oura_live.DEFAULT_SCOPES):
    return oura_live.OuraCredentials(
        client_id="cid", client_secret="csec",
        redirect_uri="http://localhost:8765/callback", scopes=scopes,
    )


TOKEN_RESPONSE = {
    "access_token": "acc-1",
    "refresh_token": "ref-1",
    "expires_in": 86400,
    "token_type": "bearer",
    "scope": "personal daily heartrate workout",
}


def _by(records, metric, date=None):
    for r in records:
        if r["metric_name"] == metric and (date is None or r["date"] == date):
            return r
    return None


# --------------------------------------------------------------------------- #
# OAuth: auth URL, exchange, refresh
# --------------------------------------------------------------------------- #


class AuthUrlTests(unittest.TestCase):
    def test_auth_url_contains_oauth_params(self):
        url = oura_live.build_authorization_url(_creds(), state="state42")
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme + "://" + parsed.netloc + parsed.path, oura_live.AUTHORIZATION_URL)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["client_id"], "cid")
        self.assertEqual(query["redirect_uri"], "http://localhost:8765/callback")
        self.assertEqual(query["scope"], "personal daily heartrate workout")
        self.assertEqual(query["state"], "state42")

    def test_load_credentials_from_env(self):
        env = {
            oura_live.ENV_CLIENT_ID: "env-id",
            oura_live.ENV_CLIENT_SECRET: "env-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop(oura_live.ENV_REDIRECT_URI, None)
            os.environ.pop(oura_live.ENV_SCOPES, None)
            creds = oura_live.load_credentials_from_env()
        self.assertEqual(creds.client_id, "env-id")
        self.assertEqual(creds.client_secret, "env-secret")
        # Default redirect + default scopes when env not set.
        self.assertEqual(creds.redirect_uri, oura_live.DEFAULT_REDIRECT_URI)
        self.assertEqual(creds.scopes, oura_live.DEFAULT_SCOPES)

    def test_missing_credentials_raise(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(oura_live.ENV_CLIENT_ID, None)
            os.environ.pop(oura_live.ENV_CLIENT_SECRET, None)
            with self.assertRaises(oura_live.OuraApiError):
                oura_live.load_credentials_from_env()


class ExchangeTests(unittest.TestCase):
    def test_exchange_code_posts_form_and_normalizes(self):
        calls = []
        with patch.object(oura_live, "urlopen", _fake_urlopen([TOKEN_RESPONSE], calls)):
            tokens = oura_live.exchange_code_for_tokens(_creds(), "the-code")

        self.assertEqual(calls[0]["url"], oura_live.TOKEN_URL)
        self.assertEqual(calls[0]["method"], "POST")
        body = {k: v[0] for k, v in calls[0]["body"].items()}
        self.assertEqual(body["grant_type"], "authorization_code")
        self.assertEqual(body["code"], "the-code")
        self.assertEqual(body["redirect_uri"], "http://localhost:8765/callback")
        self.assertEqual(body["client_id"], "cid")
        self.assertEqual(body["client_secret"], "csec")

        self.assertEqual(tokens["access_token"], "acc-1")
        self.assertEqual(tokens["refresh_token"], "ref-1")
        self.assertEqual(tokens["scope"], ["personal", "daily", "heartrate", "workout"])
        expires_at = datetime.fromisoformat(tokens["expires_at"])
        self.assertGreater(expires_at, datetime.now(timezone.utc))

    def test_exchange_missing_access_token_raises(self):
        with patch.object(oura_live, "urlopen", _fake_urlopen([{"error": "invalid_grant"}], [])):
            with self.assertRaises(oura_live.OuraApiError):
                oura_live.exchange_code_for_tokens(_creds(), "bad-code")

    def test_extract_code_from_redirect_url(self):
        url = "http://localhost:8765/callback?code=abc123&state=xyz"
        parsed = oura_live.extract_code_from_redirect_url(url, expected_state="xyz")
        self.assertEqual(parsed["code"], "abc123")
        self.assertEqual(parsed["state"], "xyz")

    def test_extract_code_state_mismatch_raises(self):
        url = "http://localhost:8765/callback?code=abc123&state=wrong"
        with self.assertRaises(oura_live.OuraApiError):
            oura_live.extract_code_from_redirect_url(url, expected_state="expected")


class RefreshTests(unittest.TestCase):
    def test_refresh_uses_refresh_grant_and_keeps_old_token_if_absent(self):
        calls = []
        rotated = dict(TOKEN_RESPONSE, access_token="acc-2")
        rotated.pop("refresh_token")  # Oura may omit; we keep the old one
        with patch.object(oura_live, "urlopen", _fake_urlopen([rotated], calls)):
            tokens = oura_live.refresh_tokens(_creds(), "ref-old")

        body = {k: v[0] for k, v in calls[0]["body"].items()}
        self.assertEqual(body["grant_type"], "refresh_token")
        self.assertEqual(body["refresh_token"], "ref-old")
        self.assertEqual(tokens["access_token"], "acc-2")
        self.assertEqual(tokens["refresh_token"], "ref-old")

    def test_ensure_valid_tokens_leaves_fresh_token_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oura_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            oura_live.save_tokens(path, {"access_token": "fresh", "refresh_token": "r", "expires_at": future})
            calls = []
            with patch.object(oura_live, "urlopen", _fake_urlopen([], calls)):
                tokens = oura_live.ensure_valid_tokens(path, _creds())
            self.assertEqual(tokens["access_token"], "fresh")
            self.assertEqual(calls, [])  # no refresh call

    def test_ensure_valid_tokens_refreshes_near_expiry_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oura_tokens.json"
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            oura_live.save_tokens(path, {"access_token": "old", "refresh_token": "ref-old", "expires_at": past})
            refreshed = dict(TOKEN_RESPONSE, access_token="acc-new", refresh_token="ref-new")
            calls = []
            with patch.object(oura_live, "urlopen", _fake_urlopen([refreshed], calls)):
                tokens = oura_live.ensure_valid_tokens(path, _creds())
            self.assertEqual(tokens["access_token"], "acc-new")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["access_token"], "acc-new")
            self.assertEqual(saved["refresh_token"], "ref-new")


# --------------------------------------------------------------------------- #
# Sync mapping (synthetic payloads -> canonical records)
# --------------------------------------------------------------------------- #

# One page per collection, keyed by endpoint path, in the shape the API returns:
# {"data": [...], "next_token": null}.
READINESS_PAGE = {
    "data": [
        {
            "id": "rdy-1",
            "day": "2026-06-01",
            "score": 71,
            "contributors": {"resting_heart_rate": 90, "hrv_balance": 70},  # must NOT shadow score
        }
    ],
    "next_token": None,
}
DAILY_SLEEP_PAGE = {
    "data": [{"id": "ds-1", "day": "2026-06-01", "score": 82}],
    "next_token": None,
}
SLEEP_PERIOD_PAGE = {
    "data": [
        {
            "id": "sl-1",
            "day": "2026-06-01",
            "bedtime_start": "2026-05-31T23:10:00+02:00",
            "total_sleep_duration": 27000,  # 7.5 h
            "deep_sleep_duration": 6000,
            "rem_sleep_duration": 5400,
            "average_hrv": 65,
            "lowest_heart_rate": 48,
            "average_heart_rate": 54,
            "average_breath": 13.5,
        }
    ],
    "next_token": None,
}
ACTIVITY_PAGE = {
    "data": [{"id": "act-1", "day": "2026-06-01", "score": 90, "steps": 9000, "active_calories": 420}],
    "next_token": None,
}
SPO2_PAGE = {
    "data": [{"id": "spo2-1", "day": "2026-06-01", "spo2_percentage": {"average": 97.0}}],
    "next_token": None,
}


class _StubClient:
    """OuraClient stand-in: returns a canned page per endpoint, no network."""

    PAGES = {
        "/daily_readiness": READINESS_PAGE,
        "/daily_sleep": DAILY_SLEEP_PAGE,
        "/daily_activity": ACTIVITY_PAGE,
        "/sleep": SLEEP_PERIOD_PAGE,
        "/daily_spo2": SPO2_PAGE,
    }

    def __init__(self):
        self.seen = []

    def list_collection(self, path, start, end):
        self.seen.append((path, start, end))
        return [self.PAGES[path]]


class SyncMappingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sync_maps_dashboard_metric_names(self):
        client = _StubClient()
        result = oura_live.sync_oura(
            self.root, start="2026-06-01", end="2026-06-01", client=client
        )

        self.assertEqual(result["source_id"], "oura-live")
        self.assertEqual(result["coverage_start"], "2026-06-01")
        self.assertEqual(result["coverage_end"], "2026-06-01")
        self.assertGreater(result["records_imported"], 0)

        records = index.list_records_by_source(self.root / "data" / "index" / "health_os.sqlite3", "oura-live")

        # daily_readiness score -> recovery_score (the dashboard recovery tile).
        recovery = _by(records, "recovery_score", "2026-06-01")
        self.assertIsNotNone(recovery, "readiness score must map to recovery_score")
        self.assertEqual(recovery["value"], 71)
        self.assertEqual(recovery["source_id"], "oura-live")
        self.assertEqual(recovery["unit"], "%")

        # daily_sleep score -> sleep_performance_percentage (dashboard sleep tile).
        sleep_perf = _by(records, "sleep_performance_percentage", "2026-06-01")
        self.assertIsNotNone(sleep_perf)
        self.assertEqual(sleep_perf["value"], 82)

        # /sleep rmssd -> hrv_rmssd_milli (same name WHOOP/dashboard read).
        hrv = _by(records, "hrv_rmssd_milli", "2026-06-01")
        self.assertIsNotNone(hrv)
        self.assertEqual(hrv["value"], 65)

        # /sleep lowest_heart_rate -> resting_heart_rate (dashboard RHR tile).
        rhr = _by(records, "resting_heart_rate", "2026-06-01")
        self.assertIsNotNone(rhr)
        self.assertEqual(rhr["value"], 48)

        # total_sleep_duration seconds -> sleep_duration_h hours.
        dur = _by(records, "sleep_duration_h", "2026-06-01")
        self.assertIsNotNone(dur)
        self.assertEqual(dur["value"], 7.5)

        # Oura-only metrics are still stored and tagged.
        spo2 = _by(records, "spo2_pct", "2026-06-01")
        self.assertIsNotNone(spo2)
        self.assertEqual(spo2["value"], 97.0)
        self.assertIn("oura-live", spo2["tags"])

        # Per-collection counts present in the summary.
        self.assertIn("daily_readiness", result["collections"])
        self.assertIn("sleep", result["collections"])

    def test_contributors_do_not_shadow_score(self):
        # The readiness payload has contributors.resting_heart_rate=90, but the
        # headline score (71) must be what maps to recovery_score, not 90.
        client = _StubClient()
        oura_live.sync_oura(self.root, start="2026-06-01", end="2026-06-01", client=client)
        records = index.list_records_by_source(self.root / "data" / "index" / "health_os.sqlite3", "oura-live")
        recovery = _by(records, "recovery_score", "2026-06-01")
        self.assertEqual(recovery["value"], 71)

    def test_resync_is_idempotent(self):
        db = self.root / "data" / "index" / "health_os.sqlite3"
        first = oura_live.sync_oura(self.root, start="2026-06-01", end="2026-06-01", client=_StubClient())
        count_first = len(index.list_records_by_source(db, "oura-live"))
        second = oura_live.sync_oura(self.root, start="2026-06-01", end="2026-06-01", client=_StubClient())
        count_second = len(index.list_records_by_source(db, "oura-live"))
        self.assertEqual(count_first, count_second)
        self.assertEqual(first["records_imported"], second["records_imported"])

    def test_collections_filter(self):
        client = _StubClient()
        oura_live.sync_oura(
            self.root, start="2026-06-01", end="2026-06-01",
            collections=["daily_readiness"], client=client,
        )
        self.assertEqual(client.seen, [("/daily_readiness", "2026-06-01", "2026-06-01")])

    def test_default_window_uses_days_back(self):
        start, end = oura_live.resolve_sync_window(None, "2026-06-30", days_back=30)
        self.assertEqual(end, "2026-06-30")
        self.assertEqual(start, "2026-05-31")


class ClientPaginationTests(unittest.TestCase):
    def test_client_follows_next_token(self):
        page1 = {"data": [{"id": "a", "day": "2026-06-01", "score": 70}], "next_token": "TOK"}
        page2 = {"data": [{"id": "b", "day": "2026-06-02", "score": 72}], "next_token": None}
        calls = []
        client = oura_live.OuraClient(_creds(), {"access_token": "acc"})
        with patch.object(oura_live, "urlopen", _fake_urlopen([page1, page2], calls)):
            pages = client.list_collection("/daily_readiness", "2026-06-01", "2026-06-02")
        self.assertEqual(len(pages), 2)
        # First call has no next_token; second carries it; Bearer header set.
        self.assertNotIn("next_token", calls[0]["url"])
        self.assertIn("next_token=TOK", calls[1]["url"])
        self.assertEqual(calls[0]["headers"].get("Authorization"), "Bearer acc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
