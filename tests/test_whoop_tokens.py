import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

from openhealth import whoop

FULL_SCOPES = (
    "read:profile",
    "read:cycles",
    "read:recovery",
    "read:sleep",
    "read:workout",
    "read:body_measurement",
    "offline",
)


def credentials() -> whoop.WhoopCredentials:
    return whoop.WhoopCredentials(
        client_id="synthetic-client",
        client_secret="synthetic-secret",
        redirect_uri="http://localhost:8765/callback",
        scopes=FULL_SCOPES,
    )


def token_payload(
    access_token: str,
    refresh_token: str,
    expires_at: str,
    scopes=FULL_SCOPES,
):
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "scope": list(scopes),
        "token_type": "bearer",
    }


class WhoopTokenRefreshTests(unittest.TestCase):
    def test_refresh_payload_includes_offline_scope(self):
        calls = []

        def fake_post(url, payload):
            calls.append((url, payload))
            return {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
                "scope": " ".join(FULL_SCOPES),
                "token_type": "bearer",
            }

        with patch.object(whoop, "_post_form", side_effect=fake_post):
            refreshed = whoop.refresh_tokens(credentials(), "refresh-old")

        self.assertEqual(calls[0][1]["grant_type"], "refresh_token")
        self.assertEqual(calls[0][1]["refresh_token"], "refresh-old")
        self.assertEqual(calls[0][1]["scope"], "offline")
        self.assertEqual(refreshed["refresh_token"], "refresh-new")

    def test_refresh_5xx_is_classified_as_outcome_uncertain(self):
        failure = whoop.WhoopRequestError(
            "synthetic sanitized 502",
            http_status=502,
            outcome_uncertain=True,
        )
        with patch.object(whoop, "_post_form", side_effect=failure):
            with self.assertRaises(whoop.WhoopRefreshOutcomeUncertain):
                whoop.refresh_tokens(credentials(), "refresh-old")

    def test_refresh_400_is_classified_as_rejected(self):
        failure = whoop.WhoopRequestError(
            "synthetic sanitized 400",
            http_status=400,
            provider_error_code="invalid_request",
        )
        with patch.object(whoop, "_post_form", side_effect=failure):
            with self.assertRaisesRegex(
                whoop.WhoopRefreshRejected,
                "invalid_request",
            ):
                whoop.refresh_tokens(credentials(), "refresh-old")

    def test_refresh_failure_marker_contains_only_allowlisted_diagnostics(self):
        secret_markers = (
            "refresh-secret-marker",
            "https://provider.test/token?secret=url-marker",
            "provider-body-marker",
            "exception-text-marker",
        )
        failure = whoop.WhoopRequestError(
            " ".join(secret_markers),
            http_status=502,
            provider_error_code="server_error",
            outcome_uncertain=True,
            cause_code="http_uncertain",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-secret-marker", secret_markers[0], expired),
            )
            with patch.object(whoop, "_post_form", side_effect=failure):
                with self.assertRaisesRegex(whoop.WhoopApiError, "uncertain"):
                    whoop.ensure_valid_tokens(path, credentials())

            state_path = whoop._refresh_state_path(path)
            state_text = state_path.read_text(encoding="utf-8")
            state = json.loads(state_text)
            self.assertEqual(
                set(state),
                {
                    "format",
                    "state",
                    "base_fingerprint",
                    "recorded_at",
                    "cause_code",
                    "http_status",
                    "provider_error_code",
                },
            )
            self.assertEqual(state["cause_code"], "http_uncertain")
            self.assertEqual(state["http_status"], 502)
            self.assertEqual(state["provider_error_code"], "server_error")
            for marker in secret_markers:
                self.assertNotIn(marker, state_text)

    def test_regular_refresh_scope_reduction_saves_successor_and_blocks_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-a", "refresh-a", expired),
            )
            reduced = token_payload(
                "access-b",
                "refresh-b",
                future,
                scopes=("read:body_measurement", "offline"),
            )

            with patch.object(whoop, "refresh_tokens", return_value=reduced):
                with self.assertRaisesRegex(whoop.WhoopApiError, "read:cycles"):
                    whoop.ensure_valid_tokens(
                        path,
                        credentials(),
                        required_scopes={"read:cycles", "offline"},
                        operation="full sync",
                    )

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["refresh_token"],
                "refresh-b",
            )
            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["state"], "reauthorization_required")
            self.assertEqual(state["cause_code"], "unusable_successor")
            with patch.object(whoop, "refresh_tokens") as second_refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "blocked"):
                    whoop.ensure_valid_tokens(
                        path,
                        credentials(),
                        required_scopes={"read:cycles", "offline"},
                        operation="full sync",
                    )
            second_refresh.assert_not_called()

    def test_nonexpired_missing_scope_writes_terminal_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload(
                    "access-a",
                    "refresh-a",
                    future,
                    scopes=("read:body_measurement", "offline"),
                ),
            )

            with patch.object(whoop, "refresh_tokens") as refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "read:cycles"):
                    whoop.ensure_valid_tokens(
                        path,
                        credentials(),
                        required_scopes={"read:cycles", "offline"},
                        operation="full sync",
                    )
            refresh.assert_not_called()
            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["state"], "reauthorization_required")
            self.assertEqual(state["cause_code"], "unusable_successor")
            with self.assertRaisesRegex(whoop.WhoopApiError, "blocked"):
                whoop.ensure_valid_tokens(
                    path,
                    credentials(),
                    required_scopes={"read:cycles", "offline"},
                    operation="full sync",
                )

    def test_rejected_refresh_marker_has_safe_provider_diagnostics(self):
        failure = whoop.WhoopRequestError(
            "synthetic body must not be persisted",
            http_status=400,
            provider_error_code="invalid_grant",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))
            with patch.object(whoop, "_post_form", side_effect=failure):
                with self.assertRaisesRegex(whoop.WhoopApiError, "rejected"):
                    whoop.ensure_valid_tokens(path, credentials())

            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["state"], "reauthorization_required")
            self.assertEqual(state["cause_code"], "provider_rejected")
            self.assertEqual(state["http_status"], 400)
            self.assertEqual(state["provider_error_code"], "invalid_grant")

    def test_missing_successor_has_specific_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))
            response = {
                "access_token": "access-new",
                "expires_in": 3600,
                "scope": " ".join(FULL_SCOPES),
            }
            with patch.object(whoop, "_post_form", return_value=response):
                with self.assertRaisesRegex(whoop.WhoopApiError, "uncertain"):
                    whoop.ensure_valid_tokens(path, credentials())

            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["cause_code"], "missing_successor")
            self.assertNotIn("http_status", state)

    def test_hostile_diagnostic_metadata_is_not_persisted(self):
        marker = "hostile-token-url-body-exception-marker"
        failure = whoop.WhoopRefreshOutcomeUncertain(
            marker,
            cause_code=marker,
            http_status=999,
            provider_error_code=marker,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))
            with patch.object(whoop, "refresh_tokens", side_effect=failure):
                with self.assertRaises(whoop.WhoopApiError):
                    whoop.ensure_valid_tokens(path, credentials())

            state_text = whoop._refresh_state_path(path).read_text(encoding="utf-8")
            self.assertEqual(
                set(json.loads(state_text)),
                {"format", "state", "base_fingerprint", "recorded_at"},
            )
            self.assertNotIn(marker, state_text)

    def test_refresh_requires_a_new_nonempty_refresh_token(self):
        for successor in (None, "", "   "):
            with self.subTest(successor=repr(successor)):
                response = {
                    "access_token": "access-new",
                    "expires_in": 3600,
                    "scope": " ".join(FULL_SCOPES),
                    "token_type": "bearer",
                }
                if successor is not None:
                    response["refresh_token"] = successor
                with patch.object(whoop, "_post_form", return_value=response):
                    with self.assertRaises(whoop.WhoopRefreshOutcomeUncertain):
                        whoop.refresh_tokens(credentials(), "refresh-old")

    def test_refresh_rejects_an_unrotated_successor(self):
        response = {
            "access_token": "access-new",
            "refresh_token": "refresh-old",
            "expires_in": 3600,
            "scope": " ".join(FULL_SCOPES),
        }
        with patch.object(whoop, "_post_form", return_value=response):
            with self.assertRaises(whoop.WhoopRefreshOutcomeUncertain) as caught:
                whoop.refresh_tokens(credentials(), "refresh-old")
        self.assertEqual(caught.exception.cause_code, "unusable_successor")

    def test_uncertain_refresh_is_persisted_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            original = token_payload("access-old", "refresh-old", expired)
            whoop.save_tokens(path, original)

            with patch.object(
                whoop,
                "refresh_tokens",
                side_effect=whoop.WhoopRefreshOutcomeUncertain("synthetic"),
            ) as refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "will not reuse"):
                    whoop.ensure_valid_tokens(path, credentials())

            refresh.assert_called_once()
            state_path = whoop._refresh_state_path(path)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["format"], whoop.REFRESH_STATE_FORMAT)
            self.assertEqual(state["state"], "outcome_uncertain")
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            state_bytes = state_path.read_bytes()
            self.assertNotIn(b"access-old", state_bytes)
            self.assertNotIn(b"refresh-old", state_bytes)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

            with patch.object(whoop, "refresh_tokens") as second_refresh:
                with self.assertRaisesRegex(
                    whoop.WhoopApiError,
                    "automatic refresh is blocked",
                ):
                    whoop.ensure_valid_tokens(path, credentials())
            second_refresh.assert_not_called()

    def test_refresh_is_marked_in_flight_before_network_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", expired),
            )

            def fake_refresh(_credentials, _refresh_token):
                state_path = whoop._refresh_state_path(path)
                self.assertTrue(state_path.exists())
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["state"], "refresh_in_flight")
                return token_payload("access-new", "refresh-new", future)

            with patch.object(whoop, "refresh_tokens", side_effect=fake_refresh):
                result = whoop.ensure_valid_tokens(path, credentials())

            self.assertEqual(result["refresh_token"], "refresh-new")
            self.assertFalse(whoop._refresh_state_path(path).exists())

    def test_process_exit_during_refresh_leaves_a_durable_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", expired),
            )

            with patch.object(whoop, "refresh_tokens", side_effect=SystemExit(9)):
                with self.assertRaises(SystemExit):
                    whoop.ensure_valid_tokens(path, credentials())

            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["state"], "refresh_in_flight")
            with patch.object(whoop, "refresh_tokens") as second_refresh:
                with self.assertRaisesRegex(
                    whoop.WhoopApiError,
                    "did not finish locally",
                ):
                    whoop.ensure_valid_tokens(path, credentials())
            second_refresh.assert_not_called()

    def test_failed_in_flight_marker_promotion_prevents_network_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            state_path = whoop._refresh_state_path(path)
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", expired),
            )
            real_replace = whoop.os.replace

            def fail_state_promotion(source, destination):
                if Path(destination) == state_path:
                    raise OSError("synthetic marker promotion failure")
                return real_replace(source, destination)

            with (
                patch.object(whoop.os, "replace", side_effect=fail_state_promotion),
                patch.object(whoop, "refresh_tokens") as refresh,
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "preserved"):
                    whoop.ensure_valid_tokens(path, credentials())

            refresh.assert_not_called()
            staged = list(
                path.parent.glob(path.name + ".refresh-state.pending.*.tmp")
            )
            self.assertEqual(len(staged), 1)
            self.assertEqual(stat.S_IMODE(staged[0].stat().st_mode), 0o600)

    def test_rejected_refresh_is_persisted_and_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", expired),
            )

            with patch.object(
                whoop,
                "refresh_tokens",
                side_effect=whoop.WhoopRefreshRejected("synthetic"),
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "rejected"):
                    whoop.ensure_valid_tokens(path, credentials())

            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["state"], "reauthorization_required")
            with patch.object(whoop, "refresh_tokens") as second_refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "blocked"):
                    whoop.ensure_valid_tokens(path, credentials())
            second_refresh.assert_not_called()

    def test_pre_dispatch_dns_failure_remains_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", expired),
            )
            failure = whoop.WhoopRequestError(
                "synthetic pre-dispatch failure",
                outcome_uncertain=False,
            )

            with patch.object(whoop, "refresh_tokens", side_effect=failure):
                with self.assertRaises(whoop.WhoopRequestError):
                    whoop.ensure_valid_tokens(path, credentials())

            self.assertFalse(whoop._refresh_state_path(path).exists())

    def test_concurrent_refresh_rotates_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))

            refresh_started = threading.Event()
            release_refresh = threading.Event()
            call_count = 0
            call_count_lock = threading.Lock()

            def fake_refresh(_credentials, refresh_token):
                nonlocal call_count
                with call_count_lock:
                    call_count += 1
                self.assertEqual(refresh_token, "refresh-old")
                refresh_started.set()
                self.assertTrue(release_refresh.wait(timeout=2))
                return token_payload("access-new", "refresh-new", future)

            results = []
            errors = []

            def worker():
                try:
                    results.append(whoop.ensure_valid_tokens(path, credentials()))
                except Exception as exc:  # pragma: no cover - assertion reports details
                    errors.append(exc)

            with patch.object(whoop, "refresh_tokens", side_effect=fake_refresh):
                first = threading.Thread(target=worker)
                second = threading.Thread(target=worker)
                first.start()
                self.assertTrue(refresh_started.wait(timeout=2))
                second.start()
                time.sleep(0.05)
                release_refresh.set()
                first.join(timeout=2)
                second.join(timeout=2)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(call_count, 1)
            self.assertEqual(len(results), 2)
            self.assertEqual({result["access_token"] for result in results}, {"access-new"})

    def test_concurrent_subprocess_refresh_rotates_only_once(self):
        worker = r"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from openhealth import whoop

token_path, calls_path, ready_path, gate_path, release_path = map(Path, sys.argv[1:])
scopes = (
    "read:profile", "read:cycles", "read:recovery", "read:sleep",
    "read:workout", "read:body_measurement", "offline",
)
credentials = whoop.WhoopCredentials("client", "secret", "http://localhost/callback", scopes)
ready_path.write_text("ready", encoding="utf-8")
while not gate_path.exists():
    time.sleep(0.01)

def fake_refresh(_credentials, refresh_token):
    if refresh_token != "refresh-old":
        raise AssertionError(refresh_token)
    with calls_path.open("a", encoding="utf-8") as handle:
        handle.write(str(os.getpid()) + "\n")
        handle.flush()
    deadline = time.monotonic() + 5
    while not release_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("refresh release was not signalled")
        time.sleep(0.01)
    return {
        "access_token": "access-new",
        "refresh_token": "refresh-new",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "scope": list(scopes),
        "token_type": "bearer",
    }

whoop.refresh_tokens = fake_refresh
result = whoop.ensure_valid_tokens(
    token_path,
    credentials,
    required_scopes=scopes,
    operation="subprocess test",
)
if result["access_token"] != "access-new":
    raise AssertionError(json.dumps(result, sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_path = root / "whoop_tokens.json"
            calls_path = root / "refresh-calls.txt"
            gate_path = root / "gate"
            release_path = root / "release"
            ready_paths = [root / "ready-1", root / "ready-2"]
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(token_path, token_payload("access-old", "refresh-old", expired))

            environment = os.environ.copy()
            repo_root = str(Path(whoop.__file__).resolve().parents[1])
            environment["PYTHONPATH"] = os.pathsep.join(
                part for part in (repo_root, environment.get("PYTHONPATH")) if part
            )
            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        worker,
                        str(token_path),
                        str(calls_path),
                        str(ready_path),
                        str(gate_path),
                        str(release_path),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                )
                for ready_path in ready_paths
            ]
            ready = False
            call_started = False
            calls_before_release = []
            try:
                ready = _wait_for(lambda: all(path.exists() for path in ready_paths))
                if ready:
                    gate_path.write_text("go", encoding="utf-8")
                    call_started = _wait_for(
                        lambda: calls_path.exists()
                        and bool(calls_path.read_text(encoding="utf-8").splitlines())
                    )
                    if call_started:
                        time.sleep(0.25)
                        calls_before_release = calls_path.read_text(encoding="utf-8").splitlines()
            finally:
                release_path.write_text("go", encoding="utf-8")

            outputs = [process.communicate(timeout=10) for process in processes]
            self.assertTrue(ready)
            self.assertTrue(call_started)
            self.assertEqual(len(calls_before_release), 1)
            for process, (stdout, stderr) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(len(calls_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_rotated_pair_is_saved_before_reduced_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))
            reduced = token_payload(
                "access-new",
                "refresh-new",
                future,
                scopes=("read:body_measurement", "offline"),
            )

            with patch.object(whoop, "refresh_tokens", return_value=reduced):
                with self.assertRaisesRegex(whoop.WhoopApiError, "read:cycles"):
                    whoop.ensure_valid_tokens(
                        path,
                        credentials(),
                        required_scopes=FULL_SCOPES,
                        operation="full sync",
                    )

            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["access_token"], "access-new")
            self.assertEqual(stored["refresh_token"], "refresh-new")

    def test_rotated_pair_recovers_after_final_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            pending_path = path.with_name(path.name + ".pending")
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))
            refreshed = token_payload("access-new", "refresh-new", future)
            real_replace = whoop.os.replace

            def fail_final_promotion(source, destination):
                if Path(destination) == path:
                    raise OSError("synthetic final promotion failure")
                return real_replace(source, destination)

            with (
                patch.object(whoop, "refresh_tokens", return_value=refreshed) as refresh,
                patch.object(whoop.os, "replace", side_effect=fail_final_promotion),
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "preserved"):
                    whoop.ensure_valid_tokens(
                        path,
                        credentials(),
                        required_scopes=FULL_SCOPES,
                        operation="full sync",
                    )

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refresh_token"], "refresh-old")
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            self.assertEqual(pending["token"]["refresh_token"], "refresh-new")
            self.assertEqual(stat.S_IMODE(pending_path.stat().st_mode), 0o600)

            recovered = whoop.ensure_valid_tokens(
                path,
                credentials(),
                required_scopes=FULL_SCOPES,
                operation="full sync",
            )
            self.assertEqual(recovered["refresh_token"], "refresh-new")
            self.assertFalse(pending_path.exists())
            refresh.assert_called_once()

    def test_refresh_without_scope_inherits_normalized_raw_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            original = token_payload("access-old", "refresh-old", expired)
            original["scope"] = None
            original["raw"] = {"scope": " ".join(reversed(FULL_SCOPES))}
            whoop.save_tokens(path, original)
            refreshed = token_payload("access-new", "refresh-new", future, scopes=())
            refreshed["scope"] = None

            with patch.object(whoop, "refresh_tokens", return_value=refreshed):
                result = whoop.ensure_valid_tokens(
                    path,
                    credentials(),
                    required_scopes=FULL_SCOPES,
                    operation="full sync",
                )

            self.assertEqual(result["scope"], sorted(FULL_SCOPES))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["scope"], sorted(FULL_SCOPES))


class WhoopAuthenticatedProbeTests(unittest.TestCase):
    def test_probe_is_cycle_first(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        client = whoop.WhoopClient(
            credentials(),
            token_payload("access-b", "refresh-b", future),
        )
        with patch.object(whoop, "_request_json", return_value={}) as request:
            scope = client.verify_authenticated_access()

        self.assertEqual(scope, "read:cycles")
        request.assert_called_once()
        self.assertIn("/cycle?limit=1", request.call_args.args[1])

    def test_probe_falls_back_on_definitive_endpoint_4xx(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        client = whoop.WhoopClient(
            credentials(),
            token_payload("access-b", "refresh-b", future),
        )
        unavailable = whoop.WhoopRequestError(
            "synthetic endpoint unavailable",
            http_status=404,
        )
        with patch.object(
            whoop,
            "_request_json",
            side_effect=[unavailable, {}],
        ) as request:
            scope = client.verify_authenticated_access()

        self.assertEqual(scope, "read:body_measurement")
        self.assertEqual(request.call_count, 2)
        self.assertIn("/cycle?limit=1", request.call_args_list[0].args[1])
        self.assertIn("/user/measurement/body", request.call_args_list[1].args[1])

    def test_probe_never_falls_back_on_auth_rate_limit_or_uncertain_failure(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for status in (401, 408, 429, 500, None):
            with self.subTest(status=status):
                client = whoop.WhoopClient(
                    credentials(),
                    token_payload("access-b", "refresh-b", future),
                )
                failure = whoop.WhoopRequestError(
                    "synthetic non-fallback failure",
                    http_status=status,
                    outcome_uncertain=status in {408, 500},
                )
                with patch.object(
                    whoop,
                    "_request_json",
                    side_effect=failure,
                ) as request:
                    with self.assertRaises(whoop.WhoopRequestError):
                        client.verify_authenticated_access()
                request.assert_called_once()

        client = whoop.WhoopClient(
            credentials(),
            token_payload("access-b", "refresh-b", future),
        )
        auth_failure = whoop.WhoopRequestError(
            "synthetic provider auth failure",
            http_status=403,
            provider_error_code="invalid_token",
        )
        with patch.object(
            whoop,
            "_request_json",
            side_effect=auth_failure,
        ) as request:
            with self.assertRaises(whoop.WhoopRequestError):
                client.verify_authenticated_access()
        request.assert_called_once()


class WhoopRefreshGateTests(unittest.TestCase):
    def test_gate_blocks_candidate_missing_configured_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                paths.whoop_tokens_path,
                token_payload(
                    "access-a",
                    "refresh-a",
                    future,
                    scopes=("read:body_measurement", "offline"),
                ),
                fresh_authorization=True,
            )

            with (
                patch.object(whoop, "refresh_tokens") as refresh,
                patch.object(whoop, "_request_json") as request,
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "read:cycles"):
                    whoop.verify_whoop_refresh_rotation(
                        root,
                        credentials=credentials(),
                    )
            refresh.assert_not_called()
            request.assert_not_called()
            self.assertFalse(
                whoop.whoop_refresh_gate_path(paths.whoop_tokens_path).exists()
            )
            state = json.loads(
                whoop._refresh_state_path(paths.whoop_tokens_path).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["state"], "reauthorization_required")
            self.assertEqual(state["cause_code"], "unusable_successor")

    def test_gate_does_not_overwrite_existing_terminal_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            candidate = token_payload(
                "access-a",
                "refresh-a",
                future,
                scopes=("read:body_measurement", "offline"),
            )
            whoop.save_tokens(paths.whoop_tokens_path, candidate)
            whoop._write_refresh_state_unlocked(
                paths.whoop_tokens_path,
                "reauthorization_required",
                candidate,
                failure=whoop.WhoopRefreshRejected(
                    "synthetic",
                    cause_code="provider_rejected",
                ),
            )
            original = whoop._refresh_state_path(
                paths.whoop_tokens_path
            ).read_bytes()

            with patch.object(whoop, "refresh_tokens") as refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "blocked"):
                    whoop.verify_whoop_refresh_rotation(
                        root,
                        credentials=credentials(),
                    )

            refresh.assert_not_called()
            self.assertEqual(
                whoop._refresh_state_path(paths.whoop_tokens_path).read_bytes(),
                original,
            )

    def test_gate_accepts_explicit_reduced_scope_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            reduced_scopes = ("read:body_measurement", "offline")
            reduced_credentials = whoop.WhoopCredentials(
                client_id="synthetic-client",
                client_secret="synthetic-secret",
                redirect_uri="http://localhost:8765/callback",
                scopes=reduced_scopes,
            )
            whoop.save_tokens(
                paths.whoop_tokens_path,
                token_payload(
                    "access-a",
                    "refresh-a",
                    future,
                    scopes=reduced_scopes,
                ),
                fresh_authorization=True,
            )
            successor = token_payload(
                "access-b",
                "refresh-b",
                future,
                scopes=reduced_scopes,
            )

            with (
                patch.object(whoop, "refresh_tokens", return_value=successor),
                patch.object(whoop, "_request_json", return_value={}) as request,
            ):
                result = whoop.verify_whoop_refresh_rotation(
                    root,
                    credentials=reduced_credentials,
                )

            self.assertTrue(result["rotation_verified"])
            self.assertEqual(result["probe_scope"], "read:body_measurement")
            request.assert_called_once()
            self.assertFalse(
                whoop._refresh_state_path(paths.whoop_tokens_path).exists()
            )

    def test_gate_forces_rotation_cycle_get_and_writes_safe_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            candidate = token_payload("access-a", "refresh-a", future)
            successor = token_payload("access-b", "refresh-b", future)
            whoop.save_tokens(
                paths.whoop_tokens_path,
                candidate,
                fresh_authorization=True,
            )

            def fake_get(method, url, *, headers, **_kwargs):
                self.assertEqual(method, "GET")
                self.assertIn("/cycle?limit=1", url)
                persisted = json.loads(
                    paths.whoop_tokens_path.read_text(encoding="utf-8")
                )
                self.assertEqual(persisted["refresh_token"], "refresh-b")
                self.assertEqual(headers["Authorization"], "Bearer access-b")
                state = json.loads(
                    whoop._refresh_state_path(paths.whoop_tokens_path).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(state["state"], "successor_verification_pending")
                return {"health-body-secret": "must-not-persist"}

            with (
                patch.object(whoop, "refresh_tokens", return_value=successor) as refresh,
                patch.object(whoop, "_request_json", side_effect=fake_get) as request,
            ):
                result = whoop.verify_whoop_refresh_rotation(
                    root,
                    credentials=credentials(),
                )

            refresh.assert_called_once()
            request.assert_called_once()
            self.assertTrue(result["rotation_verified"])
            self.assertTrue(result["rotation_performed"])
            self.assertEqual(result["probe_scope"], "read:cycles")
            self.assertFalse(whoop._refresh_state_path(paths.whoop_tokens_path).exists())
            proof_path = whoop.whoop_refresh_gate_path(paths.whoop_tokens_path)
            self.assertEqual(stat.S_IMODE(proof_path.stat().st_mode), 0o600)
            proof = whoop.load_whoop_refresh_gate_proof(paths.whoop_tokens_path)
            self.assertEqual(
                proof,
                {
                    "format": whoop.REFRESH_GATE_FORMAT,
                    "verified_at": result["verified_at"],
                },
            )
            proof_text = proof_path.read_text(encoding="utf-8")
            for marker in (
                "access-a",
                "refresh-a",
                "access-b",
                "refresh-b",
                "health-body-secret",
                "must-not-persist",
            ):
                self.assertNotIn(marker, proof_text)

    def test_gate_get_failure_keeps_successor_and_hides_exception_text(self):
        marker = "secret-body-url-token-exception-marker"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                paths.whoop_tokens_path,
                token_payload("access-a", "refresh-a", future),
                fresh_authorization=True,
            )
            successor = token_payload("access-b", "refresh-b", future)
            failure = whoop.WhoopRequestError(
                marker,
                http_status=401,
                provider_error_code="invalid_token",
            )
            with (
                patch.object(whoop, "refresh_tokens", return_value=successor),
                patch.object(whoop, "_request_json", side_effect=failure),
            ):
                with self.assertRaises(whoop.WhoopApiError) as caught:
                    whoop.verify_whoop_refresh_rotation(root, credentials=credentials())

            rendered = "".join(
                traceback.format_exception(
                    type(caught.exception), caught.exception, caught.exception.__traceback__
                )
            )
            self.assertIn("authenticated GET failed HTTP 401 (invalid_token)", rendered)
            self.assertNotIn(marker, rendered)
            stored = json.loads(paths.whoop_tokens_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["refresh_token"], "refresh-b")
            state_text = whoop._refresh_state_path(paths.whoop_tokens_path).read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                json.loads(state_text)["state"],
                "successor_verification_pending",
            )
            self.assertNotIn(marker, state_text)

    def test_gate_retry_repeats_only_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                paths.whoop_tokens_path,
                token_payload("access-a", "refresh-a", future),
                fresh_authorization=True,
            )
            successor = token_payload("access-b", "refresh-b", future)
            failure = whoop.WhoopRequestError("synthetic", http_status=503)
            with (
                patch.object(whoop, "refresh_tokens", return_value=successor) as refresh,
                patch.object(whoop, "_request_json", side_effect=failure),
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "retry only the GET"):
                    whoop.verify_whoop_refresh_rotation(root, credentials=credentials())

            with patch.object(whoop, "refresh_tokens") as blocked_refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "retry only the GET"):
                    whoop.ensure_valid_tokens(paths.whoop_tokens_path, credentials())
            blocked_refresh.assert_not_called()

            with (
                patch.object(whoop, "refresh_tokens") as second_refresh,
                patch.object(whoop, "_request_json", return_value={}) as request,
            ):
                result = whoop.verify_whoop_refresh_rotation(
                    root,
                    credentials=credentials(),
                )
            refresh.assert_called_once()
            second_refresh.assert_not_called()
            request.assert_called_once()
            self.assertFalse(result["rotation_performed"])
            self.assertFalse(whoop._refresh_state_path(paths.whoop_tokens_path).exists())

    def test_gate_recovers_crash_after_durable_successor_without_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            candidate = token_payload("access-a", "refresh-a", future)
            successor = token_payload("access-b", "refresh-b", future)
            whoop.save_tokens(
                paths.whoop_tokens_path,
                candidate,
                fresh_authorization=True,
            )
            real_write = whoop._write_refresh_state_unlocked

            def crash_before_pending(path, state, base, failure=None, verification_required=False):
                if state == "successor_verification_pending":
                    raise SystemExit(91)
                return real_write(
                    path,
                    state,
                    base,
                    failure=failure,
                    verification_required=verification_required,
                )

            with (
                patch.object(whoop, "refresh_tokens", return_value=successor),
                patch.object(
                    whoop,
                    "_write_refresh_state_unlocked",
                    side_effect=crash_before_pending,
                ),
            ):
                with self.assertRaises(SystemExit):
                    whoop.verify_whoop_refresh_rotation(root, credentials=credentials())

            crashed = json.loads(
                whoop._refresh_state_path(paths.whoop_tokens_path).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(crashed["state"], "refresh_in_flight")
            self.assertIs(crashed["verification_required"], True)
            with (
                patch.object(whoop, "refresh_tokens") as second_refresh,
                patch.object(whoop, "_request_json", return_value={}) as get,
            ):
                result = whoop.verify_whoop_refresh_rotation(
                    root,
                    credentials=credentials(),
                )
            second_refresh.assert_not_called()
            get.assert_called_once()
            self.assertFalse(result["rotation_performed"])

    def test_regular_sync_preserves_gate_requirement_after_crash_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            candidate = token_payload("access-a", "refresh-a", future)
            successor = token_payload("access-b", "refresh-b", future)
            whoop.save_tokens(path, candidate)
            whoop._write_refresh_state_unlocked(
                path,
                "refresh_in_flight",
                candidate,
                verification_required=True,
            )
            whoop.save_tokens(path, successor)
            with patch.object(whoop, "refresh_tokens") as refresh:
                with self.assertRaisesRegex(whoop.WhoopApiError, "retry only the GET"):
                    whoop.ensure_valid_tokens(path, credentials())
            refresh.assert_not_called()
            state = json.loads(
                whoop._refresh_state_path(path).read_text(encoding="utf-8")
            )
            self.assertEqual(state["state"], "successor_verification_pending")
            self.assertNotIn("verification_required", state)

    def test_gate_proof_is_durable_before_marker_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                paths.whoop_tokens_path,
                token_payload("access-a", "refresh-a", future),
                fresh_authorization=True,
            )
            successor = token_payload("access-b", "refresh-b", future)

            def crash_during_clear(token_path):
                proof_path = whoop.whoop_refresh_gate_path(token_path)
                self.assertTrue(proof_path.exists())
                self.assertEqual(stat.S_IMODE(proof_path.stat().st_mode), 0o600)
                self.assertEqual(
                    json.loads(
                        whoop._refresh_state_path(token_path).read_text(encoding="utf-8")
                    )["state"],
                    "successor_verification_pending",
                )
                raise SystemExit(92)

            with (
                patch.object(whoop, "refresh_tokens", return_value=successor),
                patch.object(whoop, "_request_json", return_value={}),
                patch.object(
                    whoop,
                    "_clear_refresh_state_unlocked",
                    side_effect=crash_during_clear,
                ),
            ):
                with self.assertRaises(SystemExit):
                    whoop.verify_whoop_refresh_rotation(root, credentials=credentials())

            self.assertTrue(whoop.whoop_refresh_gate_path(paths.whoop_tokens_path).exists())
            self.assertTrue(whoop._refresh_state_path(paths.whoop_tokens_path).exists())

    def test_gate_scope_reduction_saves_successor_and_requires_reauthorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = whoop.ensure_repo_structure(root)
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                paths.whoop_tokens_path,
                token_payload("access-a", "refresh-a", future),
                fresh_authorization=True,
            )
            reduced = token_payload(
                "access-b",
                "refresh-b",
                future,
                scopes=("read:body_measurement", "offline"),
            )
            with (
                patch.object(whoop, "refresh_tokens", return_value=reduced),
                patch.object(whoop, "_request_json") as request,
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "read:cycles"):
                    whoop.verify_whoop_refresh_rotation(root, credentials=credentials())
            request.assert_not_called()
            self.assertEqual(
                json.loads(paths.whoop_tokens_path.read_text(encoding="utf-8"))[
                    "refresh_token"
                ],
                "refresh-b",
            )
            state = json.loads(
                whoop._refresh_state_path(paths.whoop_tokens_path).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["state"], "reauthorization_required")
            self.assertEqual(state["cause_code"], "unusable_successor")


class WhoopTokenStorageTests(unittest.TestCase):
    def test_save_is_owner_only_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(path, token_payload("access", "refresh", future))

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(path.with_name(path.name + ".lock").stat().st_mode),
                0o600,
            )
            self.assertEqual(json.loads(path.read_text())["access_token"], "access")

    def test_failed_stage_replace_keeps_recoverable_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            original = token_payload("access-old", "refresh-old", future)
            whoop.save_tokens(path, original)

            replacement = token_payload("access-new", "refresh-new", future)
            with patch.object(whoop.os, "replace", side_effect=OSError("synthetic failure")):
                with self.assertRaisesRegex(whoop.WhoopApiError, "preserved"):
                    whoop.save_tokens(path, replacement)

            self.assertEqual(json.loads(path.read_text()), original)
            staged = list(path.parent.glob(path.name + ".pending.*.tmp"))
            self.assertEqual(len(staged), 1)
            self.assertEqual(json.loads(staged[0].read_text())["token"], replacement)
            self.assertEqual(stat.S_IMODE(staged[0].stat().st_mode), 0o600)

            recovered = whoop.ensure_valid_tokens(
                path,
                credentials(),
                required_scopes=FULL_SCOPES,
                operation="full sync",
            )
            self.assertEqual(recovered["refresh_token"], "refresh-new")
            self.assertEqual(list(path.parent.glob(path.name + ".pending.*.tmp")), [])

    def test_stale_pending_transaction_never_overwrites_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            pending_path = path.with_name(path.name + ".pending")
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            old_base = token_payload("access-old", "refresh-old", future)
            stale_successor = token_payload("access-stale", "refresh-stale", future)
            canonical = token_payload("access-current", "refresh-current", future)
            whoop.save_tokens(path, canonical)
            pending_path.write_text(
                json.dumps(whoop._token_transaction(old_base, stale_successor)),
                encoding="utf-8",
            )
            pending_path.chmod(0o600)

            with self.assertRaisesRegex(whoop.WhoopApiError, "conflicts"):
                whoop.ensure_valid_tokens(
                    path,
                    credentials(),
                    required_scopes=FULL_SCOPES,
                    operation="full sync",
                )

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), canonical)
            self.assertTrue(pending_path.exists())

    def test_duplicate_successor_pending_is_cleaned_without_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            pending_path = path.with_name(path.name + ".pending")
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            old_base = token_payload("access-old", "refresh-old", future)
            canonical = token_payload("access-current", "refresh-current", future)
            whoop.save_tokens(path, canonical)
            canonical_stat = path.stat()
            pending_path.write_text(
                json.dumps(whoop._token_transaction(old_base, canonical)),
                encoding="utf-8",
            )
            pending_path.chmod(0o600)

            result = whoop.ensure_valid_tokens(
                path,
                credentials(),
                required_scopes=FULL_SCOPES,
                operation="full sync",
            )

            self.assertEqual(result, canonical)
            self.assertFalse(pending_path.exists())
            self.assertEqual(path.stat().st_ino, canonical_stat.st_ino)

    def test_scope_reduction_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            original = token_payload("access-full", "refresh-full", future)
            whoop.save_tokens(path, original)
            body_only = token_payload(
                "access-body",
                "refresh-body",
                future,
                scopes=("read:body_measurement", "offline"),
            )

            with self.assertRaisesRegex(whoop.WhoopApiError, "narrower scopes"):
                whoop.save_tokens(path, body_only)
            self.assertEqual(json.loads(path.read_text()), original)

            whoop.save_tokens(path, body_only, allow_scope_reduction=True)
            self.assertEqual(json.loads(path.read_text())["scope"], list(body_only["scope"]))

    def test_fresh_authorization_quarantines_incomplete_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            original = token_payload("access-old", "refresh-old", future)
            replacement = token_payload("access-new", "refresh-new", future)
            whoop.save_tokens(path, original)
            incomplete = path.with_name(path.name + ".pending.partial.tmp")
            incomplete_bytes = b'{"format":"truncated'
            incomplete.write_bytes(incomplete_bytes)
            incomplete.chmod(0o600)

            with self.assertRaisesRegex(whoop.WhoopApiError, "incomplete"):
                whoop.ensure_valid_tokens(path, credentials())

            whoop.save_tokens(
                path,
                replacement,
                fresh_authorization=True,
            )

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                replacement,
            )
            self.assertEqual(
                list(path.parent.glob(path.name + ".pending*")),
                [],
            )
            self.assertEqual(
                list(path.parent.glob(path.name + ".promote.*.tmp")),
                [],
            )
            quarantine = path.with_name(
                path.name + ".recovery-quarantine"
            )
            preserved = list(quarantine.iterdir())
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0].read_bytes(), incomplete_bytes)
            self.assertEqual(
                stat.S_IMODE(quarantine.stat().st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(preserved[0].stat().st_mode),
                0o600,
            )

    def test_fresh_authorization_quarantines_refresh_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", expired),
            )
            with patch.object(
                whoop,
                "refresh_tokens",
                side_effect=whoop.WhoopRefreshOutcomeUncertain("synthetic"),
            ):
                with self.assertRaises(whoop.WhoopApiError):
                    whoop.ensure_valid_tokens(path, credentials())

            whoop.save_tokens(
                path,
                token_payload("access-new", "refresh-new", future),
                fresh_authorization=True,
            )

            self.assertFalse(whoop._refresh_state_path(path).exists())
            result = whoop.ensure_valid_tokens(path, credentials())
            self.assertEqual(result["refresh_token"], "refresh-new")
            quarantine = path.with_name(path.name + ".recovery-quarantine")
            preserved = list(quarantine.iterdir())
            self.assertEqual(len(preserved), 1)
            self.assertIn("refresh-state", preserved[0].name)
            self.assertEqual(stat.S_IMODE(preserved[0].stat().st_mode), 0o600)

    def test_fresh_authorization_invalidates_prior_gate_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", future))
            prior_proof = whoop._write_refresh_gate_proof_unlocked(path)
            proof_path = whoop.whoop_refresh_gate_path(path)

            whoop.save_tokens(
                path,
                token_payload("access-new", "refresh-new", future),
                fresh_authorization=True,
            )

            self.assertFalse(proof_path.exists())
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(path))
            quarantine = path.with_name(path.name + ".recovery-quarantine")
            preserved = [
                candidate
                for candidate in quarantine.iterdir()
                if "refresh-gate" in candidate.name
            ]
            self.assertEqual(len(preserved), 1)
            self.assertEqual(stat.S_IMODE(preserved[0].stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(preserved[0].read_text(encoding="utf-8")),
                prior_proof,
            )

    def test_fresh_authorization_recovery_invalidates_stale_proof_before_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            original = token_payload("access-old", "refresh-old", future)
            replacement = token_payload("access-new", "refresh-new", future)
            whoop.save_tokens(path, original)
            whoop._write_refresh_state_unlocked(
                path,
                "outcome_uncertain",
                original,
            )
            whoop._write_refresh_gate_proof_unlocked(path)
            proof_path = whoop.whoop_refresh_gate_path(path)

            # Simulate a process crash after the fresh OAuth transaction is
            # durable, but before stale refresh state and gate proof are moved.
            with patch.object(
                whoop,
                "_quarantine_token_recovery_files",
                side_effect=SystemExit(93),
            ):
                with self.assertRaises(SystemExit):
                    whoop.save_tokens(
                        path,
                        replacement,
                        fresh_authorization=True,
                    )

            staged = list(path.parent.glob(path.name + ".pending.*.tmp"))
            self.assertEqual(len(staged), 1)
            transaction = json.loads(staged[0].read_text(encoding="utf-8"))
            self.assertIs(transaction.get("fresh_authorization"), True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            self.assertTrue(proof_path.exists())
            self.assertTrue(whoop._refresh_state_path(path).exists())

            recovered = whoop.ensure_valid_tokens(path, credentials())

            self.assertEqual(recovered, replacement)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), replacement)
            self.assertFalse(proof_path.exists())
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(path))
            self.assertFalse(whoop._refresh_state_path(path).exists())
            quarantine = path.with_name(path.name + ".recovery-quarantine")
            preserved_names = [candidate.name for candidate in quarantine.iterdir()]
            self.assertTrue(any("refresh-gate" in name for name in preserved_names))
            self.assertTrue(any("refresh-state" in name for name in preserved_names))

    def test_fresh_authorization_recovery_fails_closed_if_invalidation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            original = token_payload("access-old", "refresh-old", future)
            replacement = token_payload("access-new", "refresh-new", future)
            whoop.save_tokens(path, original)
            whoop._write_refresh_state_unlocked(
                path,
                "outcome_uncertain",
                original,
            )
            whoop._write_refresh_gate_proof_unlocked(path)
            proof_path = whoop.whoop_refresh_gate_path(path)
            state_path = whoop._refresh_state_path(path)

            with patch.object(
                whoop,
                "_quarantine_token_recovery_files",
                side_effect=SystemExit(93),
            ):
                with self.assertRaises(SystemExit):
                    whoop.save_tokens(
                        path,
                        replacement,
                        fresh_authorization=True,
                    )

            real_replace = os.replace

            def fail_after_proof_invalidation(source, destination):
                if Path(source) == state_path:
                    raise OSError("synthetic state quarantine failure")
                return real_replace(source, destination)

            with patch.object(
                whoop.os,
                "replace",
                side_effect=fail_after_proof_invalidation,
            ):
                with self.assertRaisesRegex(
                    whoop.WhoopApiError,
                    "Could not preserve stale",
                ):
                    whoop.ensure_valid_tokens(path, credentials())

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            self.assertFalse(proof_path.exists())
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(path))
            self.assertTrue(state_path.exists())
            pending_path = path.with_name(path.name + ".pending")
            self.assertTrue(pending_path.exists())
            self.assertIs(
                json.loads(pending_path.read_text(encoding="utf-8")).get(
                    "fresh_authorization"
                ),
                True,
            )

            recovered = whoop.ensure_valid_tokens(path, credentials())
            self.assertEqual(recovered, replacement)
            self.assertFalse(proof_path.exists())
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(path))

    def test_gate_proof_reader_rejects_symlink_hardlink_and_unsafe_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_path = root / "whoop_tokens.json"
            proof_path = whoop.whoop_refresh_gate_path(token_path)
            target = root / "untrusted-proof.json"
            target.write_text(
                json.dumps(
                    {
                        "format": whoop.REFRESH_GATE_FORMAT,
                        "verified_at": "2026-08-18T12:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            target.chmod(0o600)

            proof_path.symlink_to(target)
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(token_path))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            proof_path.unlink()

            os.link(target, proof_path)
            self.assertEqual(target.stat().st_nlink, 2)
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(token_path))
            proof_path.unlink()

            target.replace(proof_path)
            proof_path.chmod(0o644)
            self.assertIsNone(whoop.load_whoop_refresh_gate_proof(token_path))
            self.assertEqual(stat.S_IMODE(proof_path.stat().st_mode), 0o644)

    def test_incomplete_staged_refresh_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            whoop.save_tokens(
                path,
                token_payload("access-old", "refresh-old", future),
            )
            staged = path.with_name(path.name + ".refresh-state.pending.partial.tmp")
            staged.write_text("{truncated", encoding="utf-8")
            staged.chmod(0o600)

            with patch.object(whoop, "refresh_tokens") as refresh:
                with self.assertRaisesRegex(
                    whoop.WhoopApiError,
                    "state is incomplete",
                ):
                    whoop.ensure_valid_tokens(path, credentials())
            refresh.assert_not_called()


class WhoopScopeValidationTests(unittest.TestCase):
    def test_offline_scope_and_refresh_token_are_required(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        without_offline = token_payload(
            "access",
            "refresh",
            future,
            scopes=tuple(scope for scope in FULL_SCOPES if scope != "offline"),
        )
        with self.assertRaisesRegex(whoop.WhoopApiError, "offline"):
            whoop.require_token_scopes(without_offline, FULL_SCOPES, "full sync")

        without_refresh = token_payload("access", "", future)
        with self.assertRaisesRegex(whoop.WhoopApiError, "no refresh token"):
            whoop.require_token_scopes(without_refresh, FULL_SCOPES, "full sync")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            whoop.save_tokens(path, without_refresh)
            with self.assertRaisesRegex(whoop.WhoopApiError, "no refresh token"):
                whoop.ensure_valid_tokens(
                    path,
                    credentials(),
                    required_scopes=FULL_SCOPES,
                    operation="full sync",
                )

    def test_full_sync_rejects_body_only_token_before_api_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_path = root / "data/index/whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(
                token_path,
                token_payload(
                    "access-body",
                    "refresh-body",
                    expired,
                    scopes=("read:body_measurement", "offline"),
                ),
            )

            with (
                patch.object(whoop, "load_credentials_from_env", return_value=credentials()),
                patch.object(whoop, "WhoopClient") as client_class,
                patch.object(whoop, "refresh_tokens") as refresh,
            ):
                with self.assertRaisesRegex(whoop.WhoopApiError, "read:cycles"):
                    whoop.sync_whoop(root, include_profile=False)

            client_class.assert_not_called()
            refresh.assert_not_called()


class WhoopCurlFallbackTests(unittest.TestCase):
    def test_dns_failure_is_known_to_precede_request_dispatch(self):
        failure = whoop.URLError(whoop.socket.gaierror(-2, "synthetic DNS"))
        with patch.object(whoop, "urlopen", side_effect=failure):
            with self.assertRaises(whoop.WhoopRequestError) as caught:
                whoop._request_json(
                    "POST",
                    "https://example.test/token",
                    headers={},
                    data=b"synthetic=true",
                    path_hint="token exchange",
                )

        self.assertFalse(caught.exception.outcome_uncertain)
        self.assertEqual(caught.exception.cause_code, "transport_failure")

    def test_http_5xx_post_has_safe_uncertain_cause(self):
        failure = whoop.HTTPError(
            "https://example.test/token",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b'{"error":"server_error"}'),
        )
        with patch.object(whoop, "urlopen", side_effect=failure):
            with self.assertRaises(whoop.WhoopRequestError) as caught:
                whoop._request_json(
                    "POST",
                    "https://example.test/token",
                    headers={},
                    data=b"synthetic=true",
                    path_hint="token exchange",
                )
        self.assertTrue(caught.exception.outcome_uncertain)
        self.assertEqual(caught.exception.cause_code, "http_uncertain")
        self.assertEqual(caught.exception.http_status, 502)

    def test_partial_http_error_body_keeps_status_and_never_leaks(self):
        marker = b"refresh_token=provider-echoed-secret"

        class PartialErrorBody:
            def read(self):
                raise IncompleteRead(marker, len(marker) + 100)

            def close(self):
                return None

        failure = whoop.HTTPError(
            "https://example.test/token?secret=url-marker",
            502,
            "Bad Gateway",
            {},
            PartialErrorBody(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whoop_tokens.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            whoop.save_tokens(path, token_payload("access-old", "refresh-old", expired))
            with patch.object(whoop, "urlopen", side_effect=failure):
                with self.assertRaises(whoop.WhoopApiError) as caught:
                    whoop.ensure_valid_tokens(path, credentials())
            rendered = "".join(
                traceback.format_exception(
                    type(caught.exception), caught.exception, caught.exception.__traceback__
                )
            )
            state_text = whoop._refresh_state_path(path).read_text(encoding="utf-8")
            state = json.loads(state_text)
        self.assertEqual(state["cause_code"], "http_uncertain")
        self.assertEqual(state["http_status"], 502)
        self.assertNotIn(marker.decode("utf-8"), rendered)
        self.assertNotIn(marker.decode("utf-8"), state_text)

    def test_safe_http_error_code_ignores_non_string_values(self):
        for value in ([], {}, 42, True, None):
            with self.subTest(value=value):
                self.assertIsNone(
                    whoop._safe_http_error_code(json.dumps({"error": value}))
                )

    def test_partial_http_response_is_sanitized_and_uncertain(self):
        marker = b"refresh_token=provider-echoed-secret"
        failure = IncompleteRead(marker, len(marker) + 100)
        with patch.object(whoop, "urlopen", side_effect=failure):
            try:
                whoop._request_json(
                    "POST",
                    "https://example.test/token",
                    headers={},
                    data=b"synthetic=true",
                    path_hint="token exchange",
                )
            except whoop.WhoopRequestError as exc:
                rendered = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                self.assertTrue(exc.outcome_uncertain)
                self.assertEqual(exc.cause_code, "protocol_incomplete")
            else:  # pragma: no cover - the mocked response must fail
                self.fail("expected WhoopRequestError")

        self.assertNotIn(marker.decode("utf-8"), rendered)

    def test_curl_fallback_has_network_and_process_timeouts(self):
        completed = subprocess.CompletedProcess(args=["curl"], returncode=0, stdout='{"ok": true}', stderr="")
        with patch.object(whoop.subprocess, "run", return_value=completed) as run:
            payload = whoop._request_json_with_curl(
                "GET",
                "https://example.test/whoop",
                headers={"Accept": "application/json"},
                data=None,
                path_hint="synthetic",
            )

        self.assertEqual(payload, {"ok": True})
        command = run.call_args.args[0]
        self.assertEqual(command, ["curl", "--config", "-"])
        config = run.call_args.kwargs["input"]
        self.assertIn('connect-timeout = "%s"' % whoop.CURL_CONNECT_TIMEOUT_SECONDS, config)
        self.assertIn('max-time = "%s"' % whoop.CURL_MAX_TIME_SECONDS, config)
        self.assertEqual(run.call_args.kwargs["timeout"], whoop.CURL_PROCESS_TIMEOUT_SECONDS)

    def test_curl_process_timeout_is_wrapped(self):
        with patch.object(
            whoop.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("curl", whoop.CURL_PROCESS_TIMEOUT_SECONDS),
        ):
            with self.assertRaisesRegex(whoop.WhoopApiError, "timed out") as caught:
                whoop._request_json_with_curl(
                    "GET",
                    "https://example.test/whoop",
                    headers={},
                    data=None,
                    path_hint="synthetic",
                )
        self.assertEqual(caught.exception.cause_code, "transport_timeout")

    def test_curl_http_failure_is_conservatively_uncertain(self):
        failure = subprocess.CalledProcessError(
            22,
            ["curl", "--config", "-"],
            stderr="synthetic HTTP failure",
        )
        with patch.object(whoop.subprocess, "run", side_effect=failure):
            with self.assertRaises(whoop.WhoopRequestError) as caught:
                whoop._request_json_with_curl(
                    "POST",
                    "https://example.test/token",
                    headers={"Accept": "application/json"},
                    data=b"synthetic=true",
                    path_hint="token exchange",
                )
        self.assertTrue(caught.exception.outcome_uncertain)
        self.assertEqual(caught.exception.cause_code, "http_uncertain")

    def test_curl_failure_traceback_never_contains_oauth_secrets(self):
        markers = (
            "Bearer bearer-secret-marker",
            "client_secret=client-secret-marker",
            "refresh_token=refresh-secret-marker",
            "code=oauth-code-marker",
        )
        leaked_stderr = "server echoed " + " ".join(markers)
        failure = subprocess.CalledProcessError(
            22,
            ["curl", "--config", "-"],
            stderr=leaked_stderr,
        )
        with patch.object(whoop.subprocess, "run", side_effect=failure) as run:
            try:
                whoop._request_json_with_curl(
                    "POST",
                    "https://example.test/token?code=oauth-code-marker",
                    headers={"Authorization": "Bearer bearer-secret-marker"},
                    data=(
                        b"client_secret=client-secret-marker&"
                        b"refresh_token=refresh-secret-marker&code=oauth-code-marker"
                    ),
                    path_hint="token exchange",
                )
            except whoop.WhoopApiError as exc:
                rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            else:  # pragma: no cover - the mocked curl call must fail
                self.fail("expected WhoopApiError")

        argv = repr(run.call_args.args[0])
        self.assertEqual(run.call_args.args[0], ["curl", "--config", "-"])
        for marker in markers:
            self.assertNotIn(marker, argv)
            self.assertNotIn(marker, rendered)

    def test_urllib_failure_traceback_never_contains_oauth_secrets(self):
        markers = (
            "bearer-secret-marker",
            "client-secret-marker",
            "refresh-secret-marker",
            "oauth-code-marker",
        )
        response_body = json.dumps(
            {
                "error": "invalid_request",
                "error_description": "server echoed " + " ".join(markers),
                "request": {
                    "client_secret": markers[1],
                    "refresh_token": markers[2],
                    "code": markers[3],
                },
            }
        ).encode("utf-8")
        failure = whoop.HTTPError(
            "https://example.test/token?code=oauth-code-marker",
            400,
            "Bad Request",
            {},
            io.BytesIO(response_body),
        )

        with patch.object(whoop, "urlopen", side_effect=failure):
            try:
                whoop._request_json(
                    "POST",
                    "https://example.test/token?code=oauth-code-marker",
                    headers={"Authorization": "Bearer bearer-secret-marker"},
                    data=(
                        b"client_secret=client-secret-marker&"
                        b"refresh_token=refresh-secret-marker&code=oauth-code-marker"
                    ),
                    path_hint="token exchange",
                )
            except whoop.WhoopApiError as exc:
                rendered = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
            else:  # pragma: no cover - the mocked urllib call must fail
                self.fail("expected WhoopApiError")

        self.assertIn("HTTP 400 (invalid_request)", rendered)
        for marker in markers:
            self.assertNotIn(marker, rendered)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


if __name__ == "__main__":
    unittest.main()
