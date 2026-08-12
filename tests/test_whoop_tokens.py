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
            with self.assertRaisesRegex(whoop.WhoopApiError, "timed out"):
                whoop._request_json_with_curl(
                    "GET",
                    "https://example.test/whoop",
                    headers={},
                    data=None,
                    path_hint="synthetic",
                )

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
