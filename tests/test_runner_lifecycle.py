from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts/runner_lifecycle.py"


class RunnerLifecycleTests(unittest.TestCase):
    def _command(self, lock: Path, executable: str, *args: str) -> list[str]:
        return [
            sys.executable,
            str(HELPER),
            "--lock",
            str(lock),
            "--",
            executable,
            *args,
        ]

    def test_killed_waiter_never_enters_the_guarded_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            lock = root / "daily.lifecycle.lock"
            critical = root / "daily-claim-or-send"
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            waiter = subprocess.Popen(
                self._command(lock, "/usr/bin/touch", str(critical)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                time.sleep(0.15)
                self.assertIsNone(waiter.poll())
                self.assertFalse(critical.exists())
                waiter.kill()
                waiter.communicate(timeout=3)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            self.assertFalse(critical.exists())

    def test_lock_survives_exec_for_the_whole_runner(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            lock = root / "watchdog.lifecycle.lock"
            started = root / "started"
            release = root / "release"
            second = root / "second"
            first = subprocess.Popen(
                self._command(
                    lock,
                    "/bin/bash",
                    "-c",
                    f'touch "{started}"; while [[ ! -f "{release}" ]]; do sleep 0.02; done',
                )
            )
            deadline = time.monotonic() + 3
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(started.exists())
            blocked = subprocess.Popen(self._command(lock, "/usr/bin/touch", str(second)))
            time.sleep(0.15)
            self.assertFalse(second.exists())
            release.touch()
            self.assertEqual(first.wait(timeout=3), 0)
            self.assertEqual(blocked.wait(timeout=3), 0)
            self.assertTrue(second.exists())
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_symlink_and_hardlink_locks_fail_closed(self) -> None:
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                target = root / "target"
                target.write_text("preserve\n", encoding="utf-8")
                target.chmod(0o640)
                lock = root / "runner.lifecycle.lock"
                if kind == "symlink":
                    lock.symlink_to(target)
                else:
                    os.link(target, lock)
                critical = root / "critical"
                completed = subprocess.run(
                    self._command(lock, "/usr/bin/touch", str(critical)),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(critical.exists())
                self.assertEqual(target.read_text(encoding="utf-8"), "preserve\n")
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()
