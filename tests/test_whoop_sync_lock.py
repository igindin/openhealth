import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from openhealth import whoop


class WhoopSyncLockTests(unittest.TestCase):
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
