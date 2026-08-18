from __future__ import annotations

import concurrent.futures
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts/daily_sync_claim.py"


class DailySyncClaimTests(unittest.TestCase):
    def _run(self, operation: str, root: Path, local_date: str = "2026-08-18") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER), operation, "--root", str(root), "--date", local_date],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_claim_and_success_are_empty_owner_only_durable_markers(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "claims"
            self.assertEqual(self._run("claim", root).stdout.strip(), "claimed")
            self.assertEqual(self._run("claim", root).stdout.strip(), "already_attempted")
            self.assertEqual(self._run("success", root).stdout.strip(), "success_marked")
            self.assertEqual(self._run("claim", root).stdout.strip(), "already_success")

            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            for marker in root.iterdir():
                self.assertEqual(marker.read_bytes(), b"")
                self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_concurrent_claim_has_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "claims"
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(lambda _: self._run("claim", root).stdout.strip(), range(16)))
            self.assertEqual(results.count("claimed"), 1)
            self.assertEqual(results.count("already_attempted"), 15)
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_orphan_success_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "claims"
            root.mkdir()
            (root / "2026-08-18.whoop-success").touch()
            result = self._run("claim", root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("without an attempt", result.stderr)

    def test_symlink_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "claims"
            root.mkdir()
            target = root / "target"
            target.touch()
            os.symlink(target, root / "2026-08-18.whoop-attempt")
            result = self._run("claim", root)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
