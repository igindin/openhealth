from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts/operational_file.py"


class PrivateFileTests(unittest.TestCase):
    def _run(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER), "--path", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_create_is_empty_owner_only_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "sync.log"
            completed = self._run(path)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            details = path.stat()
            self.assertTrue(stat.S_ISREG(details.st_mode))
            self.assertEqual(details.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
            self.assertEqual(path.read_bytes(), b"")

    def test_existing_regular_content_is_preserved_and_mode_is_tightened(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "sync.log"
            path.write_text("preserve\n", encoding="utf-8")
            path.chmod(0o644)
            completed = self._run(path)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_symlink_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "target"
            target.write_text("private target\n", encoding="utf-8")
            target.chmod(0o640)
            path = root / "sync.log"
            path.symlink_to(target)

            completed = self._run(path)
            self.assertNotEqual(completed.returncode, 0)
            self.assertTrue(path.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "private target\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    def test_hardlink_is_rejected_without_touching_inode(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "target"
            target.write_text("private target\n", encoding="utf-8")
            target.chmod(0o640)
            path = root / "sync.log"
            os.link(target, path)

            completed = self._run(path)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "private target\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual(target.stat().st_nlink, 2)

    def test_nonregular_entries_are_rejected_without_blocking(self) -> None:
        for kind in ("directory", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw_root:
                path = Path(raw_root) / "sync.log"
                if kind == "directory":
                    path.mkdir()
                else:
                    os.mkfifo(path, 0o640)
                completed = self._run(path)
                self.assertNotEqual(completed.returncode, 0)
                details = path.lstat()
                self.assertEqual(stat.S_ISDIR(details.st_mode), kind == "directory")
                self.assertEqual(stat.S_ISFIFO(details.st_mode), kind == "fifo")


if __name__ == "__main__":
    unittest.main()
