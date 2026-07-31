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
REVISION = "b" * 40
BUILDER_GLOBALS = runpy.run_path(str(REPO_ROOT / "scripts/build_pinned_runtime.py"))


class PinnedSyncRunnerTests(unittest.TestCase):
    def _roots(self, root: Path) -> tuple[Path, Path]:
        runtime = root / "runtime-releases" / REVISION
        (runtime / "openhealth").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "openhealth/__init__.py", runtime / "openhealth/__init__.py")
        (runtime / "REVISION").write_text(f"{REVISION}\n", encoding="utf-8")
        for relative in BUILDER_GLOBALS["REQUIRED_FILES"]:
            source = REPO_ROOT / relative.as_posix()
            destination = runtime / relative.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        manifest = BUILDER_GLOBALS["_manifest_payload"](runtime, REVISION)
        (runtime / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        BUILDER_GLOBALS["_apply_owner_only_modes"](runtime)
        BUILDER_GLOBALS["verify_release"](runtime, REVISION)

        data_root = root / "data-workspace"
        (data_root / "data/index").mkdir(parents=True)
        (data_root / "ui/web").mkdir(parents=True)
        (data_root / ".env").write_text(
            "SYNTHETIC_SECRET=loaded\n"
            "PYTHONPATH=/untrusted/pythonpath\n"
            "OPENHEALTH_RUNTIME_ROOT=/untrusted/runtime\n"
            "OPENHEALTH_DATA_ROOT=/untrusted/data\n"
            "OPENHEALTH_PYTHON_BIN=/untrusted/python\n"
            "PYTHONHOME=/untrusted/pythonhome\n"
            "PYTHONSTARTUP=/untrusted/startup.py\n"
            "PYTHONINSPECT=1\n"
            "PYTHONBREAKPOINT=untrusted.breakpoint\n"
            "PYTHONUSERBASE=/untrusted/userbase\n"
            "PYTHONPLATLIBDIR=untrusted-lib\n"
            "PYTHONWARNINGS=error\n",
            encoding="utf-8",
        )
        return runtime, data_root

    def _fake_python(self, root: Path) -> Path:
        fake_python = root / "fake-python"
        fake_python.write_text(
            """#!/usr/bin/env bash
set -eu
if [[ "${2:-}" == "$OPENHEALTH_RUNTIME_ROOT/scripts/build_pinned_runtime.py" && "${3:-}" == verify ]]; then
  exec "$OPENHEALTH_TEST_REAL_PYTHON" "$@"
fi
{
  printf 'CALL\\n'
  printf 'cwd=%s\\n' "$PWD"
  printf 'PYTHONPATH=%s\\n' "$PYTHONPATH"
  printf 'PYTHONSAFEPATH=%s\\n' "$PYTHONSAFEPATH"
  printf 'OPENHEALTH_RUNTIME_ROOT=%s\\n' "$OPENHEALTH_RUNTIME_ROOT"
  printf 'OPENHEALTH_DATA_ROOT=%s\\n' "$OPENHEALTH_DATA_ROOT"
  printf 'OPENHEALTH_PYTHON_BIN=%s\\n' "$OPENHEALTH_PYTHON_BIN"
  printf 'PYTHONHOME=%s\\n' "${PYTHONHOME-unset}"
  printf 'PYTHONSTARTUP=%s\\n' "${PYTHONSTARTUP-unset}"
  printf 'PYTHONINSPECT=%s\\n' "${PYTHONINSPECT-unset}"
  printf 'PYTHONBREAKPOINT=%s\\n' "${PYTHONBREAKPOINT-unset}"
  printf 'PYTHONUSERBASE=%s\\n' "${PYTHONUSERBASE-unset}"
  printf 'PYTHONPLATLIBDIR=%s\\n' "${PYTHONPLATLIBDIR-unset}"
  printf 'PYTHONWARNINGS=%s\\n' "${PYTHONWARNINGS-unset}"
  printf 'SYNTHETIC_SECRET=%s\\n' "$SYNTHETIC_SECRET"
  printf 'arg=%s\\n' "$@"
} >> "$OPENHEALTH_TEST_CAPTURE"
""",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        return fake_python

    def _fake_plutil(self, root: Path) -> Path:
        fake_plutil = root / "fake-plutil"
        fake_plutil.write_text(
            """#!/usr/bin/env bash
exec "$OPENHEALTH_TEST_REAL_PYTHON" -E -s "$OPENHEALTH_TEST_PLUTIL_SCRIPT" "$@"
""",
            encoding="utf-8",
        )
        fake_plutil.chmod(0o755)
        return fake_plutil

    def _environment(
        self,
        runtime: Path,
        data_root: Path,
        fake_python: Path,
        capture: Path,
    ) -> dict[str, str]:
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
        return environment

    def test_body_runner_isolates_code_from_data_and_uses_safe_python_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            fake_python = self._fake_python(root)
            capture = root / "capture.log"

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/whoop-body-sync-run.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(runtime, data_root, fake_python, capture),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            lines = capture.read_text(encoding="utf-8").splitlines()
            self.assertIn(f"cwd={runtime}", lines)
            self.assertIn(f"PYTHONPATH={runtime}", lines)
            self.assertIn("PYTHONSAFEPATH=1", lines)
            self.assertIn(f"OPENHEALTH_RUNTIME_ROOT={runtime}", lines)
            self.assertIn(f"OPENHEALTH_DATA_ROOT={data_root}", lines)
            self.assertIn(f"OPENHEALTH_PYTHON_BIN={fake_python}", lines)
            self.assertIn("SYNTHETIC_SECRET=loaded", lines)
            self.assertIn("PYTHONHOME=unset", lines)
            self.assertIn("PYTHONSTARTUP=unset", lines)
            self.assertIn("PYTHONINSPECT=unset", lines)
            self.assertIn("PYTHONBREAKPOINT=unset", lines)
            self.assertIn("PYTHONUSERBASE=unset", lines)
            self.assertIn("PYTHONPLATLIBDIR=unset", lines)
            self.assertIn("PYTHONWARNINGS=unset", lines)
            self.assertEqual(
                [line.removeprefix("arg=") for line in lines if line.startswith("arg=")],
                ["-P", "-m", "openhealth", "--repo-root", str(data_root), "whoop-body-sync"],
            )

    def test_body_runner_rejects_revision_drift_before_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            revision_path = runtime / "REVISION"
            revision_path.chmod(0o600)
            revision_path.write_text(f"{'c' * 40}\n", encoding="utf-8")
            fake_python = self._fake_python(root)
            capture = root / "capture.log"

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/whoop-body-sync-run.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(runtime, data_root, fake_python, capture),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("revision does not match", completed.stderr)
            self.assertFalse(capture.exists())

    def test_body_runner_rejects_tampered_payload_with_unchanged_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            tampered = runtime / "openhealth/__init__.py"
            tampered.chmod(0o600)
            tampered.write_text("# tampered with unchanged REVISION\n", encoding="utf-8")
            fake_python = self._fake_python(root)
            capture = root / "capture.log"

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/whoop-body-sync-run.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(runtime, data_root, fake_python, capture),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("manifest verification failed", completed.stderr)
            self.assertFalse(capture.exists())

    def test_body_runner_rejects_relative_python_before_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            environment = self._environment(runtime, data_root, Path("python3"), root / "capture.log")

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/whoop-body-sync-run.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("absolute executable path", completed.stderr)
            self.assertFalse((root / "capture.log").exists())

    def test_daily_runner_uses_same_pin_for_every_local_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            fake_python = self._fake_python(root)
            capture = root / "capture.log"

            completed = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/daily-sync-run.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(runtime, data_root, fake_python, capture),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            calls = capture.read_text(encoding="utf-8").split("CALL\n")[1:]
            self.assertEqual(len(calls), 4)
            for call in calls:
                self.assertIn(f"cwd={runtime}\n", call)
                self.assertIn(f"PYTHONPATH={runtime}\n", call)
                self.assertIn("PYTHONSAFEPATH=1\n", call)
                self.assertIn(f"OPENHEALTH_DATA_ROOT={data_root}\n", call)
                self.assertIn(f"OPENHEALTH_PYTHON_BIN={fake_python}\n", call)
                self.assertIn("PYTHONHOME=unset\n", call)
                self.assertIn("PYTHONSTARTUP=unset\n", call)
                self.assertIn("PYTHONINSPECT=unset\n", call)
                self.assertIn("PYTHONBREAKPOINT=unset\n", call)
                self.assertIn("PYTHONUSERBASE=unset\n", call)
                self.assertIn("PYTHONPLATLIBDIR=unset\n", call)
                self.assertIn("PYTHONWARNINGS=unset\n", call)
                self.assertIn("arg=-P\n", call)
            self.assertIn("arg=whoop-sync\n", calls[0])
            self.assertIn("arg=oura-sync\n", calls[1])
            self.assertIn("arg=openhealth.scheduler\n", calls[2])
            self.assertIn(f"arg={runtime / 'ui/web/build_dashboard_data.py'}\n", calls[3])

    def test_daily_installer_renders_the_same_runtime_and_data_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            rendered = root / "rendered/daily.plist"
            fake_plutil = self._fake_plutil(root)
            plutil = shutil.which("plutil") or str(fake_plutil)
            environment = os.environ.copy()
            environment.update(
                {
                    "OPENHEALTH_PLUTIL_BIN": plutil,
                    "OPENHEALTH_TEST_REAL_PYTHON": sys.executable,
                    "OPENHEALTH_TEST_PLUTIL_SCRIPT": str(REPO_ROOT / "tests/fixtures/fake_plutil.py"),
                }
            )

            completed = subprocess.run(
                [
                    "bash",
                    str(REPO_ROOT / "scripts/install-daily-sync-launchagent.sh"),
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
            self.assertEqual(parsed["WorkingDirectory"], str(runtime))
            self.assertEqual(
                parsed["ProgramArguments"],
                ["/bin/bash", str(runtime / "scripts/daily-sync-run.sh")],
            )
            environment = parsed["EnvironmentVariables"]
            self.assertEqual(environment["OPENHEALTH_RUNTIME_ROOT"], str(runtime))
            self.assertEqual(environment["OPENHEALTH_DATA_ROOT"], str(data_root))
            self.assertEqual(environment["OPENHEALTH_RUNTIME_REVISION"], REVISION)
            self.assertEqual(environment["PYTHONPATH"], str(runtime))
            self.assertEqual(environment["PYTHONSAFEPATH"], "1")

    def test_installer_rejects_relative_python_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)

            completed = subprocess.run(
                [
                    "bash",
                    str(REPO_ROOT / "scripts/install-daily-sync-launchagent.sh"),
                    "--runtime-root",
                    str(runtime),
                    "--data-root",
                    str(data_root),
                    "--revision",
                    REVISION,
                    "--python-bin",
                    "python3",
                    "--render-only",
                    str(root / "rendered/daily.plist"),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("absolute executable path", completed.stderr)


class PinnedSyncFileModeTests(unittest.TestCase):
    def test_committed_runners_and_installers_are_executable(self):
        for relative_path in (
            "scripts/build_pinned_runtime.py",
            "scripts/whoop-body-sync-run.sh",
            "scripts/daily-sync-run.sh",
            "scripts/install-whoop-body-sync-launchagent.sh",
            "scripts/install-daily-sync-launchagent.sh",
            "scripts/install-pinned-sync-launchagent.sh",
        ):
            with self.subTest(path=relative_path):
                mode = stat.S_IMODE((REPO_ROOT / relative_path).stat().st_mode)
                self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
