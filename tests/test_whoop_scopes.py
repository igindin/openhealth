"""Tests for configurable WHOOP OAuth scopes.

No network is touched: these cover scope parsing, environment-driven
credential loading, and the scope string baked into the authorization URL.

Run directly:  PYTHONPATH=$PWD python3 tests/test_whoop_scopes.py
"""

import os
import unittest
from urllib.parse import parse_qs, urlparse

from openhealth import whoop


class ParseScopesTests(unittest.TestCase):
    def test_empty_returns_empty_tuple(self):
        self.assertEqual(whoop.parse_scopes(None), ())
        self.assertEqual(whoop.parse_scopes(""), ())
        self.assertEqual(whoop.parse_scopes("   "), ())

    def test_space_separated(self):
        self.assertEqual(
            whoop.parse_scopes("read:cycles read:sleep offline"),
            ("read:cycles", "read:sleep", "offline"),
        )

    def test_comma_separated_and_mixed_whitespace(self):
        self.assertEqual(
            whoop.parse_scopes("read:cycles, read:sleep ,offline"),
            ("read:cycles", "read:sleep", "offline"),
        )


class LoadCredentialsScopeTests(unittest.TestCase):
    ENV_KEYS = (
        "OPENHEALTH_WHOOP_CLIENT_ID",
        "OPENHEALTH_WHOOP_CLIENT_SECRET",
        "OPENHEALTH_WHOOP_REDIRECT_URI",
        "OPENHEALTH_WHOOP_SCOPES",
    )

    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in self.ENV_KEYS}
        os.environ["OPENHEALTH_WHOOP_CLIENT_ID"] = "cid"
        os.environ["OPENHEALTH_WHOOP_CLIENT_SECRET"] = "secret"
        os.environ["OPENHEALTH_WHOOP_REDIRECT_URI"] = "http://localhost:8765/callback"
        os.environ.pop("OPENHEALTH_WHOOP_SCOPES", None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_when_scopes_env_unset(self):
        creds = whoop.load_credentials_from_env()
        self.assertEqual(creds.scopes, whoop.DEFAULT_SCOPES)

    def test_env_override_narrows_scopes(self):
        os.environ["OPENHEALTH_WHOOP_SCOPES"] = "read:cycles read:recovery offline"
        creds = whoop.load_credentials_from_env()
        self.assertEqual(creds.scopes, ("read:cycles", "read:recovery", "offline"))
        self.assertNotIn("read:profile", creds.scopes)

    def test_blank_env_falls_back_to_defaults(self):
        os.environ["OPENHEALTH_WHOOP_SCOPES"] = "   "
        creds = whoop.load_credentials_from_env()
        self.assertEqual(creds.scopes, whoop.DEFAULT_SCOPES)

    def test_authorization_url_uses_requested_scopes(self):
        os.environ["OPENHEALTH_WHOOP_SCOPES"] = "read:cycles read:sleep offline"
        creds = whoop.load_credentials_from_env()
        url = whoop.build_authorization_url(creds, state="state42")
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertEqual(query["scope"], "read:cycles read:sleep offline")
        self.assertEqual(query["state"], "state42")
        self.assertEqual(query["client_id"], "cid")


if __name__ == "__main__":
    unittest.main()
