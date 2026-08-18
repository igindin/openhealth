import concurrent.futures
import json
import stat
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from openhealth import watchdog
from openhealth.config import build_paths
from openhealth.whoop import (
    REFRESH_GATE_FORMAT,
    REFRESH_STATE_FORMAT,
    whoop_refresh_gate_path,
)


NOW = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)


class WhoopRefreshWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = build_paths(self.root)
        self.paths.data_index.mkdir(parents=True)
        self.marker = self.paths.whoop_tokens_path.with_name(
            self.paths.whoop_tokens_path.name + ".refresh-state"
        )
        self.state = self.paths.data_index / "watchdog-state.json"
        self.sent = []

    def _sender(self, text):
        self.sent.append(text)
        return True

    def _write_marker(self, **overrides):
        payload = {
            "format": REFRESH_STATE_FORMAT,
            "state": "outcome_uncertain",
            "recorded_at": (NOW - timedelta(minutes=2)).isoformat(),
            "base_fingerprint": "not-forwarded",
            "cause_code": "http_uncertain",
            "http_status": 502,
            "provider_error_code": "server_error",
        }
        payload.update(overrides)
        self.marker.write_text(json.dumps(payload), encoding="utf-8")
        self.marker.chmod(0o600)
        return payload

    def _write_gate_proof(self, verified_at):
        proof_path = whoop_refresh_gate_path(self.paths.whoop_tokens_path)
        proof_path.write_text(
            json.dumps(
                {"format": REFRESH_GATE_FORMAT, "verified_at": verified_at}
            ),
            encoding="utf-8",
        )
        proof_path.chmod(0o600)
        return proof_path

    def _activate_incident(self):
        marker = self._write_marker()
        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            1,
        )
        self.marker.unlink()
        return datetime.fromisoformat(marker["recorded_at"])

    def test_terminal_marker_alerts_once_and_state_is_private(self):
        self._write_marker(
            provider_body="access_token=hostile-secret",
            request_url="https://example.invalid/hostile-secret",
        )

        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            1,
        )
        self.assertEqual(len(self.sent), 1)
        self.assertIn("HTTP 502", self.sent[0])
        self.assertIn("server_error", self.sent[0])
        self.assertNotIn("hostile-secret", self.sent[0])
        state_text = self.state.read_text(encoding="utf-8")
        self.assertNotIn("fingerprint", state_text)
        self.assertNotIn("hostile-secret", state_text)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
        self.assertEqual(list(self.state.parent.glob(self.state.name + ".pending.*")), [])

        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            0,
        )
        self.assertEqual(len(self.sent), 1)

    def test_new_recorded_at_is_a_new_incident(self):
        self._write_marker()
        watchdog.run_once(
            repo_root=self.root,
            sender=self._sender,
            now=NOW,
            state_path=self.state,
        )
        self._write_marker(recorded_at=NOW.isoformat(), cause_code="transport_timeout")

        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW + timedelta(minutes=1),
                state_path=self.state,
            ),
            1,
        )
        self.assertEqual(len(self.sent), 2)
        self.assertIn("таймаут", self.sent[1])

    def test_reduced_scope_successor_requests_reauthorization(self):
        self._write_marker(
            state="reauthorization_required",
            cause_code="unusable_successor",
        )

        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            1,
        )
        self.assertEqual(len(self.sent), 1)
        self.assertIn("непригодную новую пару токенов", self.sent[0])
        self.assertIn("нужна новая авторизация WHOOP", self.sent[0])

    def test_concurrent_runs_send_only_one_alert_for_an_incident(self):
        self._write_marker()
        barrier = threading.Barrier(2)
        sent = []

        def slow_sender(text):
            sent.append(text)
            time.sleep(0.05)
            return True

        def invoke():
            barrier.wait()
            return watchdog.run_once(
                repo_root=self.root,
                sender=slow_sender,
                now=NOW,
                state_path=self.state,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _value: invoke(), range(2)))

        self.assertEqual(sorted(results), [0, 1])
        self.assertEqual(len(sent), 1)
        lock_path = self.state.with_name(self.state.name + ".lock")
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_delivery_failure_is_retried(self):
        self._write_marker()
        attempts = []

        def failing_sender(text):
            attempts.append(text)
            return False

        for _ in range(2):
            self.assertEqual(
                watchdog.run_once(
                    repo_root=self.root,
                    sender=failing_sender,
                    now=NOW,
                    state_path=self.state,
                ),
                2,
            )
        self.assertEqual(len(attempts), 2)
        self.assertFalse(self.state.exists())

    def test_corrupt_or_unsafe_marker_fails_closed_without_echo(self):
        self.marker.write_text('{"token":"do-not-echo"', encoding="utf-8")
        self.marker.chmod(0o600)
        incident = watchdog.read_refresh_incident(self.marker, now=NOW)
        self.assertEqual(incident.cause_code, "invalid_refresh_state")
        self.assertNotIn("do-not-echo", watchdog.render_incident_alert(incident))

        self.marker.chmod(0o644)
        incident = watchdog.read_refresh_incident(self.marker, now=NOW)
        self.assertEqual(incident.cause_code, "invalid_refresh_state")

    def test_marker_deleted_between_stat_and_open_is_absent(self):
        self._write_marker()

        def delete_before_read(path):
            path.unlink()
            return None

        with patch.object(
            watchdog,
            "_read_private_json_object",
            side_effect=delete_before_read,
        ):
            self.assertIsNone(
                watchdog.read_refresh_incident(self.marker, now=NOW)
            )

    def test_marker_deletion_race_can_complete_verified_recovery(self):
        incident_time = self._activate_incident()
        gate_time = incident_time + timedelta(minutes=1)
        sync_time = gate_time + timedelta(minutes=1)
        self._write_gate_proof(gate_time.isoformat())
        self.paths.whoop_sync_state_path.write_text(
            json.dumps({"last_successful_sync": sync_time.isoformat()}),
            encoding="utf-8",
        )
        self.assertIsNotNone(
            watchdog._verified_recovery(
                token_path=self.paths.whoop_tokens_path,
                sync_state_path=self.paths.whoop_sync_state_path,
                recorded_at=incident_time.isoformat(),
            )
        )
        self.assertTrue(json.loads(self.state.read_text(encoding="utf-8"))["active"])
        self._write_marker(recorded_at=incident_time.isoformat())

        def delete_before_read(path):
            path.unlink()
            return None

        verified = []
        real_verified_recovery = watchdog._verified_recovery

        def capture_verified_recovery(**kwargs):
            result = real_verified_recovery(**kwargs)
            verified.append(result)
            return result

        with patch.object(
            watchdog,
            "_read_private_json_object",
            side_effect=delete_before_read,
        ), patch.object(
            watchdog,
            "_verified_recovery",
            side_effect=capture_verified_recovery,
        ):
            result = watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            )

        self.assertEqual(verified, [gate_time.isoformat()])
        self.assertEqual(result, 0)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[-1].startswith("✅ WHOOP"))

    def test_unknown_metadata_is_omitted(self):
        self._write_marker(
            cause_code="provider-said-secret",
            provider_error_code="secret-provider-code",
            http_status="502 secret",
        )
        incident = watchdog.read_refresh_incident(self.marker, now=NOW)
        self.assertEqual(incident.cause_code, "unknown")
        self.assertIsNone(incident.provider_error_code)
        self.assertIsNone(incident.http_status)
        self.assertNotIn("secret", watchdog.render_incident_alert(incident))

    def test_in_flight_and_successor_pending_observe_grace(self):
        for state, cause in (
            ("refresh_in_flight", "refresh_interrupted"),
            ("successor_verification_pending", "successor_verification_pending"),
        ):
            with self.subTest(state=state):
                self._write_marker(
                    state=state,
                    recorded_at=(NOW - timedelta(seconds=30)).isoformat(),
                    cause_code=None,
                )
                self.assertIsNone(watchdog.read_refresh_incident(self.marker, now=NOW))
                incident = watchdog.read_refresh_incident(
                    self.marker, now=NOW + timedelta(seconds=90)
                )
                self.assertEqual(incident.cause_code, cause)
                alert = watchdog.render_incident_alert(incident)
                if state == "successor_verification_pending":
                    self.assertIn("новая пара токенов сохранена", alert)
                    self.assertIn("новый refresh-запрос отправлен не будет", alert)
                    self.assertNotIn("нужна новая авторизация", alert)
                else:
                    self.assertIn("OAuth не понадобится", alert)
                    self.assertNotIn("нужна новая авторизация", alert)

    def test_interrupted_gate_uses_safe_get_resume_copy(self):
        self._write_marker(
            state="refresh_in_flight",
            recorded_at=(NOW - timedelta(minutes=2)).isoformat(),
            cause_code=None,
            verification_required=True,
        )

        incident = watchdog.read_refresh_incident(self.marker, now=NOW)
        self.assertTrue(incident.verification_required)
        alert = watchdog.render_incident_alert(incident)
        self.assertIn("повторён только GET", alert)
        self.assertIn("без нового refresh-запроса", alert)
        self.assertNotIn("нужна новая авторизация", alert)

    def test_recovery_requires_gate_after_incident_and_import_after_gate(self):
        incident_time = self._activate_incident()
        pre_gate_sync = incident_time + timedelta(minutes=1)
        self.paths.whoop_sync_state_path.write_text(
            json.dumps({"last_successful_sync": pre_gate_sync.isoformat()}),
            encoding="utf-8",
        )

        # Fresh OAuth plus an access-token sync is not live-rotation proof.
        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            0,
        )
        gate_time = incident_time + timedelta(minutes=5)
        self._write_gate_proof(gate_time.isoformat())

        # Even with a gate proof, the import must be strictly later than it.
        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            0,
        )
        self.paths.whoop_sync_state_path.write_text(
            json.dumps(
                {"last_successful_sync": (gate_time + timedelta(minutes=1)).isoformat()}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            0,
        )
        self.assertEqual(len(self.sent), 2)
        recovered = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertFalse(recovered["active"])
        self.assertEqual(recovered["gate_verified_at"], gate_time.isoformat())

    def test_stale_gate_proof_cannot_close_new_incident(self):
        incident_time = self._activate_incident()
        self._write_gate_proof((incident_time - timedelta(minutes=1)).isoformat())
        self.paths.whoop_sync_state_path.write_text(
            json.dumps(
                {"last_successful_sync": (incident_time + timedelta(minutes=5)).isoformat()}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            0,
        )
        self.assertEqual(len(self.sent), 1)

    def test_fresh_marker_cannot_be_mistaken_for_recovery(self):
        incident_time = self._activate_incident()
        gate_time = incident_time + timedelta(minutes=1)
        self._write_gate_proof(gate_time.isoformat())
        self.paths.whoop_sync_state_path.write_text(
            json.dumps(
                {"last_successful_sync": (gate_time + timedelta(minutes=1)).isoformat()}
            ),
            encoding="utf-8",
        )
        self._write_marker(
            state="refresh_in_flight",
            recorded_at=(NOW - timedelta(seconds=30)).isoformat(),
            cause_code=None,
        )

        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=self._sender,
                now=NOW,
                state_path=self.state,
            ),
            0,
        )
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(json.loads(self.state.read_text())["active"])

    def test_recovery_delivery_failure_retries(self):
        incident_time = self._activate_incident()
        gate_time = incident_time + timedelta(minutes=1)
        self._write_gate_proof(gate_time.isoformat())
        self.paths.whoop_sync_state_path.write_text(
            json.dumps(
                {"last_successful_sync": (gate_time + timedelta(minutes=1)).isoformat()}
            ),
            encoding="utf-8",
        )
        attempts = []

        def fail(text):
            attempts.append(text)
            return False

        self.assertEqual(
            watchdog.run_once(
                repo_root=self.root,
                sender=fail,
                now=NOW,
                state_path=self.state,
            ),
            2,
        )
        self.assertTrue(json.loads(self.state.read_text())["active"])
        self.assertEqual(len(attempts), 1)

    def test_telegram_delivery_requires_private_token_and_ok_json(self):
        token_path = self.root / "telegram.token"
        token_path.write_text("synthetic-token", encoding="utf-8")
        token_path.chmod(0o600)

        class Response:
            def __init__(self, status, body=b'{"ok":true}'):
                self.status = status
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return self.body

        with patch.object(
            watchdog.urllib.request, "urlopen", return_value=Response(200)
        ) as request:
            self.assertTrue(
                watchdog.send_telegram_alert(
                    "safe text", token_path=token_path, chat_id="123"
                )
            )
        self.assertIn("synthetic-token", request.call_args.args[0])
        self.assertNotIn(b"synthetic-token", request.call_args.args[1])

        for response in (Response(500), Response(200, b'{"ok":false}')):
            with patch.object(watchdog.urllib.request, "urlopen", return_value=response):
                self.assertFalse(
                    watchdog.send_telegram_alert(
                        "safe text", token_path=token_path, chat_id="123"
                    )
                )

        token_path.chmod(0o644)
        with patch.object(watchdog.urllib.request, "urlopen") as request:
            self.assertFalse(
                watchdog.send_telegram_alert(
                    "safe text", token_path=token_path, chat_id="123"
                )
            )
        request.assert_not_called()

    def test_cli_exit_marks_only_delivery_failure_unhealthy(self):
        for watcher_result, expected_exit in ((0, 0), (1, 0), (2, 1)):
            with self.subTest(watcher_result=watcher_result):
                with patch.object(watchdog, "run_once", return_value=watcher_result):
                    self.assertEqual(
                        watchdog.main(
                            [
                                "--repo-root",
                                str(self.root),
                                "--config-dir",
                                str(self.root),
                                "--chat-id",
                                "123",
                            ]
                        ),
                        expected_exit,
                    )


if __name__ == "__main__":
    unittest.main()
