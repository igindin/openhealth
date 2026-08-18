import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from openhealth import whoop


class WhoopSyncLockTests(unittest.TestCase):
    def test_bundled_full_and_body_sync_uses_one_client_and_one_token_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            credentials = object()
            tokens = {"access_token": "synthetic"}

            with (
                patch.object(whoop, "load_credentials_from_env", return_value=credentials),
                patch.object(whoop, "ensure_valid_tokens", return_value=tokens) as ensure_tokens,
                patch.object(whoop, "WhoopClient") as client_class,
            ):
                client = client_class.return_value
                client.list_cycles.return_value = []
                client.list_recoveries.return_value = []
                client.list_sleeps.return_value = []
                client.list_workouts.return_value = []
                client.get_body_measurements.return_value = {}

                result = whoop.sync_whoop(
                    root,
                    include_profile=False,
                    include_body_measurements=True,
                )

            ensure_tokens.assert_called_once()
            token_call = ensure_tokens.call_args
            self.assertEqual(token_call.args, (root / "data/index/whoop_tokens.json", credentials))
            self.assertEqual(
                set(token_call.kwargs["required_scopes"]),
                {
                    "offline",
                    "read:cycles",
                    "read:recovery",
                    "read:sleep",
                    "read:workout",
                    "read:body_measurement",
                },
            )
            self.assertEqual(token_call.kwargs["operation"], "full sync")
            client_class.assert_called_once_with(credentials, tokens)
            client.get_body_measurements.assert_called_once_with()
            self.assertEqual(result["collections"]["body_measurements"], 0)
            lock_path = root / "data/index/whoop-sync.lock"
            self.assertTrue(lock_path.is_file())
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_full_and_body_syncs_cannot_write_concurrently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full_started = threading.Event()
            release_full = threading.Event()
            body_started = threading.Event()
            results = []
            errors = []

            def full_sync(**kwargs):
                self.assertEqual(kwargs["root"], root)
                full_started.set()
                self.assertTrue(release_full.wait(timeout=3))
                return {"kind": "full"}

            def body_sync(**kwargs):
                self.assertEqual(kwargs["root"], root)
                body_started.set()
                return {"kind": "body"}

            def run(call):
                try:
                    results.append(call())
                except Exception as exc:  # pragma: no cover - assertion aid
                    errors.append(exc)

            with (
                patch.object(
                    whoop,
                    "_sync_whoop_unlocked",
                    side_effect=full_sync,
                ),
                patch.object(
                    whoop,
                    "_sync_whoop_body_measurements_unlocked",
                    side_effect=body_sync,
                ),
            ):
                full_thread = threading.Thread(
                    target=run,
                    args=(lambda: whoop.sync_whoop(root),),
                )
                body_thread = threading.Thread(
                    target=run,
                    args=(
                        lambda: whoop.sync_whoop_body_measurements(root),
                    ),
                )
                full_thread.start()
                self.assertTrue(full_started.wait(timeout=2))
                body_thread.start()
                self.assertFalse(body_started.wait(timeout=0.2))
                release_full.set()
                full_thread.join(timeout=3)
                body_thread.join(timeout=3)

            self.assertFalse(full_thread.is_alive())
            self.assertFalse(body_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(body_started.is_set())
            self.assertEqual(
                {result["kind"] for result in results},
                {"full", "body"},
            )
            lock_path = root / "data/index/whoop-sync.lock"
            self.assertTrue(lock_path.is_file())
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
