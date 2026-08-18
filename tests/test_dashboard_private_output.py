import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "ui/web/build_dashboard_data.py"


def _load_dashboard_builder():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard_data_private_output",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load dashboard data builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_dashboard_builder()


class DashboardPrivateOutputTests(unittest.TestCase):
    def test_existing_public_file_is_atomically_replaced_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "data.local.json"
            output.write_text('{"old": true}\n', encoding="utf-8")
            output.chmod(0o644)
            old_inode = output.stat().st_ino

            BUILDER._write_private_json(output, {"private": "health-data"})

            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"private": "health-data"},
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertNotEqual(output.stat().st_ino, old_inode)
            self.assertEqual(list(output.parent.glob(".data.local.json.*.tmp")), [])

    def test_failed_promotion_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "data.local.json"
            original = b'{"known": "good"}\n'
            output.write_bytes(original)
            output.chmod(0o600)

            with patch.object(
                BUILDER.os,
                "replace",
                side_effect=OSError("synthetic promotion failure"),
            ):
                with self.assertRaisesRegex(OSError, "promotion failure"):
                    BUILDER._write_private_json(output, {"new": "value"})

            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(output.parent.glob(".data.local.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
