import json
import os
import plistlib
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REVISION = "d" * 40


class WhoopRefreshWatchdogRuntimeTests(unittest.TestCase):
    def _builder(self):
        return runpy.run_path(str(REPO_ROOT / "scripts/build_pinned_runtime.py"))

    def _roots(self, root: Path):
        builder = self._builder()
        runtime = root / "runtime" / REVISION
        (runtime / "openhealth").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "openhealth/__init__.py", runtime / "openhealth/__init__.py")
        (runtime / "REVISION").write_text(REVISION + "\n", encoding="utf-8")
        for relative in builder["REQUIRED_FILES"]:
            source = REPO_ROOT / relative.as_posix()
            destination = runtime / relative.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        manifest = builder["_manifest_payload"](runtime, REVISION)
        (runtime / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        builder["_apply_owner_only_modes"](runtime)
        builder["verify_release"](runtime, REVISION)

        data_root = root / "data-root"
        (data_root / "data/index").mkdir(parents=True)
        (data_root / "ui/web").mkdir(parents=True)
        (data_root / ".env").write_text(
            "OPENHEALTH_TELEGRAM_ALERT_CHAT_ID=123\n"
            "PYTHONHOME=/untrusted/home\n"
            "PYTHONSTARTUP=/untrusted/startup\n",
            encoding="utf-8",
        )
        return runtime, data_root

    def _fake_plutil(self, root: Path) -> Path:
        fake = root / "fake-plutil"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            "exec \"$OPENHEALTH_TEST_REAL_PYTHON\" -E -s "
            "\"$OPENHEALTH_TEST_PLUTIL_SCRIPT\" \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        return fake

    def test_installer_renders_pinned_marker_watch_and_fallback_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime, data_root = self._roots(root)
            rendered = root / "rendered/watchdog.plist"
            fake_plutil = self._fake_plutil(root)
            environment = os.environ.copy()
            environment.update(
                {
                    "OPENHEALTH_PLUTIL_BIN": shutil.which("plutil") or str(fake_plutil),
                    "OPENHEALTH_TEST_REAL_PYTHON": sys.executable,
                    "OPENHEALTH_TEST_PLUTIL_SCRIPT": str(
                        REPO_ROOT / "tests/fixtures/fake_plutil.py"
                    ),
                }
            )
            completed = subprocess.run(
                [
                    "bash",
                    str(REPO_ROOT / "scripts/install-whoop-refresh-watchdog-launchagent.sh"),
                    "--runtime-root",
                    str(runtime),
                    "--data-root",
                    str(data_root),
                    "--revision",
                    REVISION,
                    "--python-bin",
                    sys.executable,
                    "--render-only",
                    str(rendered),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            parsed = plistlib.loads(rendered.read_bytes())
            self.assertEqual(parsed["Label"], "com.openhealth.whoop-refresh-watchdog")
            self.assertEqual(
                parsed["ProgramArguments"],
                ["/bin/bash", str(runtime / "scripts/whoop-refresh-watchdog-run.sh")],
            )
            self.assertEqual(parsed["StartInterval"], 120)
            self.assertFalse(parsed["RunAtLoad"])
            self.assertEqual(
                parsed["WatchPaths"],
                [str(data_root / "data/index/whoop_tokens.json.refresh-state")],
            )
            self.assertEqual(parsed["WorkingDirectory"], str(runtime))
            self.assertEqual(stat.S_IMODE(rendered.stat().st_mode), 0o600)

    def test_runner_uses_only_pinned_python_and_sources_alert_recipient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime, data_root = self._roots(root)
            capture = root / "capture.json"
            fake_python = root / "fake-python"
            fake_python.write_text(
                """#!/usr/bin/env bash
set -eu
if [[ "${2:-}" == "$OPENHEALTH_RUNTIME_ROOT/scripts/build_pinned_runtime.py" && "${3:-}" == verify ]]; then
  exec "$OPENHEALTH_TEST_REAL_PYTHON" "$@"
fi
if [[ "${2:-}" == "$OPENHEALTH_RUNTIME_ROOT/scripts/runner_lifecycle.py" ]]; then
  exec "$OPENHEALTH_TEST_REAL_PYTHON" "$@"
fi
"$OPENHEALTH_TEST_REAL_PYTHON" -c 'import json, os, sys; json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "pythonpath": os.environ.get("PYTHONPATH"), "safepath": os.environ.get("PYTHONSAFEPATH"), "chat": os.environ.get("OPENHEALTH_TELEGRAM_ALERT_CHAT_ID"), "pythonhome": os.environ.get("PYTHONHOME"), "startup": os.environ.get("PYTHONSTARTUP")}, open(os.environ["OPENHEALTH_TEST_CAPTURE"], "w"))' "$@"
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "OPENHEALTH_RUNTIME_ROOT": str(runtime),
                    "OPENHEALTH_DATA_ROOT": str(data_root),
                    "OPENHEALTH_RUNTIME_REVISION": REVISION,
                    "OPENHEALTH_PYTHON_BIN": str(fake_python),
                    "OPENHEALTH_TEST_REAL_PYTHON": sys.executable,
                    "OPENHEALTH_TEST_CAPTURE": str(capture),
                }
            )
            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/whoop-refresh-watchdog-run.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            observed = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(
                observed["argv"],
                ["-P", "-m", "openhealth.watchdog", "--repo-root", str(data_root)],
            )
            self.assertEqual(observed["cwd"], str(runtime))
            self.assertEqual(observed["pythonpath"], str(runtime))
            self.assertEqual(observed["safepath"], "1")
            self.assertEqual(observed["chat"], "123")
            self.assertIsNone(observed["pythonhome"])
            self.assertIsNone(observed["startup"])

    def test_committed_watchdog_scripts_are_executable(self):
        for relative in (
            "scripts/whoop-refresh-watchdog-run.sh",
            "scripts/install-whoop-refresh-watchdog-launchagent.sh",
            "scripts/runner_lifecycle.py",
        ):
            with self.subTest(relative=relative):
                self.assertTrue(stat.S_IMODE((REPO_ROOT / relative).stat().st_mode) & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
