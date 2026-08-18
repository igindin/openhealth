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
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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
            "PYTHONWARNINGS=error\n"
            "TZ=Pacific/Kiritimati\n",
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
if [[ "${2:-}" == "$OPENHEALTH_RUNTIME_ROOT/scripts/daily_sync_claim.py" ]]; then
  exec "$OPENHEALTH_TEST_REAL_PYTHON" "$@"
fi
if [[ "${2:-}" == "$OPENHEALTH_RUNTIME_ROOT/scripts/runner_lifecycle.py" ]]; then
  exec "$OPENHEALTH_TEST_REAL_PYTHON" "$@"
fi
printf 'TRACE %s\n' "$*" >> "$OPENHEALTH_TEST_CAPTURE"
{
  printf 'CALL\n'
  printf 'cwd=%s\n' "$PWD"
  printf 'PYTHONPATH=%s\n' "$PYTHONPATH"
  printf 'PYTHONSAFEPATH=%s\n' "$PYTHONSAFEPATH"
  printf 'OPENHEALTH_RUNTIME_ROOT=%s\n' "$OPENHEALTH_RUNTIME_ROOT"
  printf 'OPENHEALTH_DATA_ROOT=%s\n' "$OPENHEALTH_DATA_ROOT"
  printf 'OPENHEALTH_PYTHON_BIN=%s\n' "$OPENHEALTH_PYTHON_BIN"
  printf 'PYTHONHOME=%s\n' "${PYTHONHOME-unset}"
  printf 'PYTHONSTARTUP=%s\n' "${PYTHONSTARTUP-unset}"
  printf 'PYTHONINSPECT=%s\n' "${PYTHONINSPECT-unset}"
  printf 'PYTHONBREAKPOINT=%s\n' "${PYTHONBREAKPOINT-unset}"
  printf 'PYTHONUSERBASE=%s\n' "${PYTHONUSERBASE-unset}"
  printf 'PYTHONPLATLIBDIR=%s\n' "${PYTHONPLATLIBDIR-unset}"
  printf 'PYTHONWARNINGS=%s\n' "${PYTHONWARNINGS-unset}"
  printf 'TZ=%s\n' "${TZ-unset}"
  printf 'SYNTHETIC_SECRET=%s\n' "$SYNTHETIC_SECRET"
  printf 'arg=%s\n' "$@"
} >> "$OPENHEALTH_TEST_CAPTURE"
if [[ "$*" == *" whoop-sync "* ]]; then
  if [[ -n "${OPENHEALTH_TEST_WHOOP_DELAY:-}" ]]; then sleep "$OPENHEALTH_TEST_WHOOP_DELAY"; fi
  if [[ "${OPENHEALTH_TEST_FAIL_WHOOP:-0}" == 1 ]]; then exit 1; fi
fi
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

    def _environment(self, runtime: Path, data_root: Path, fake_python: Path, capture: Path) -> dict[str, str]:
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

    def _run_daily(self, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/daily-sync-run.sh")],
            check=False, capture_output=True, text=True, env=environment,
        )

    def test_daily_runner_uses_one_bundle_and_same_pin_for_every_local_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            fake_python = self._fake_python(root)
            capture = root / "capture.log"
            environment = self._environment(runtime, data_root, fake_python, capture)

            for _ in range(3):
                completed = self._run_daily(environment)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            trace = capture.read_text(encoding="utf-8")
            trace_lines = [line for line in trace.splitlines() if line.startswith("TRACE ")]
            self.assertEqual(sum(" whoop-sync " in line for line in trace_lines), 1)
            self.assertEqual(sum(" oura-sync " in line for line in trace_lines), 3)
            self.assertEqual(sum(" openhealth.scheduler " in line for line in trace_lines), 3)
            self.assertEqual(sum("build_dashboard_data.py" in line for line in trace_lines), 3)
            self.assertIn(
                f"-P -m openhealth --repo-root {data_root} whoop-sync --no-profile --days-back 14",
                trace,
            )
            self.assertNotIn("--no-body-measurements", trace)
            self.assertIn(f"cwd={runtime}\n", trace)
            self.assertIn(f"PYTHONPATH={runtime}\n", trace)
            self.assertIn("PYTHONSAFEPATH=1\n", trace)
            self.assertIn("PYTHONHOME=unset\n", trace)
            self.assertIn("PYTHONWARNINGS=unset\n", trace)
            self.assertIn("TZ=unset\n", trace)
            markers = list((data_root / "data/index/daily-sync-claims").iterdir())
            self.assertEqual(len(markers), 2)
            for marker in markers:
                self.assertEqual(marker.read_bytes(), b"")
                self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_whoop_failure_does_not_block_oura_or_retry_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            fake_python = self._fake_python(root)
            capture = root / "capture.log"
            environment = self._environment(runtime, data_root, fake_python, capture)
            environment["OPENHEALTH_TEST_FAIL_WHOOP"] = "1"

            first = self._run_daily(environment)
            second = self._run_daily(environment)
            self.assertNotEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            trace = capture.read_text(encoding="utf-8")
            trace_lines = [line for line in trace.splitlines() if line.startswith("TRACE ")]
            self.assertEqual(sum(" whoop-sync " in line for line in trace_lines), 1)
            self.assertEqual(sum(" oura-sync " in line for line in trace_lines), 2)
            self.assertEqual(sum(" openhealth.scheduler " in line for line in trace_lines), 0)
            self.assertEqual(sum("build_dashboard_data.py" in line for line in trace_lines), 2)
            self.assertIn("skip automatic same-day retry", second.stdout)

    def test_concurrent_daily_runs_claim_exactly_one_whoop_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            fake_python = self._fake_python(root)
            capture = root / "capture.log"
            environment = self._environment(runtime, data_root, fake_python, capture)
            environment["OPENHEALTH_TEST_WHOOP_DELAY"] = "0.3"
            command = ["bash", str(REPO_ROOT / "scripts/daily-sync-run.sh")]

            first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
            second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
            first_output = first.communicate(timeout=15)
            second_output = second.communicate(timeout=15)
            self.assertEqual(first.returncode, 0, "".join(first_output))
            self.assertEqual(second.returncode, 0, "".join(second_output))
            trace = capture.read_text(encoding="utf-8")
            trace_lines = [line for line in trace.splitlines() if line.startswith("TRACE ")]
            self.assertEqual(sum(" whoop-sync " in line for line in trace_lines), 1)
            self.assertEqual(sum(" oura-sync " in line for line in trace_lines), 2)
            attempt_markers = list((data_root / "data/index/daily-sync-claims").glob("*.whoop-attempt"))
            self.assertEqual(len(attempt_markers), 1)
            self.assertEqual(stat.S_IMODE(attempt_markers[0].stat().st_mode), 0o600)

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
                    "bash", str(REPO_ROOT / "scripts/install-daily-sync-launchagent.sh"),
                    "--runtime-root", str(runtime), "--data-root", str(data_root),
                    "--revision", REVISION, "--python-bin", sys.executable,
                    "--render-only", str(rendered),
                ],
                check=False, capture_output=True, text=True, env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            parsed = plistlib.loads(rendered.read_bytes())
            self.assertEqual(parsed["WorkingDirectory"], str(runtime))
            self.assertEqual(parsed["ProgramArguments"], ["/bin/bash", str(runtime / "scripts/daily-sync-run.sh")])
            rendered_environment = parsed["EnvironmentVariables"]
            self.assertEqual(rendered_environment["OPENHEALTH_RUNTIME_ROOT"], str(runtime))
            self.assertEqual(rendered_environment["OPENHEALTH_DATA_ROOT"], str(data_root))
            self.assertEqual(rendered_environment["OPENHEALTH_RUNTIME_REVISION"], REVISION)
            self.assertEqual(rendered_environment["PYTHONPATH"], str(runtime))
            self.assertEqual(rendered_environment["PYTHONSAFEPATH"], "1")
            self.assertEqual([item["Hour"] for item in parsed["StartCalendarInterval"]], [9, 14, 21])

    def test_installer_rejects_relative_python_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root = self._roots(root)
            completed = subprocess.run(
                [
                    "bash", str(REPO_ROOT / "scripts/install-daily-sync-launchagent.sh"),
                    "--runtime-root", str(runtime), "--data-root", str(data_root),
                    "--revision", REVISION, "--python-bin", "python3",
                    "--render-only", str(root / "rendered/daily.plist"),
                ],
                check=False, capture_output=True, text=True, env=os.environ.copy(),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("absolute executable path", completed.stderr)


class PinnedSyncFileModeTests(unittest.TestCase):
    def test_committed_runners_installers_and_helpers_are_executable(self):
        for relative_path in (
            "scripts/build_pinned_runtime.py",
            "scripts/daily-sync-run.sh",
            "scripts/daily_sync_claim.py",
            "scripts/launchagent_migration.py",
            "scripts/operational_file.py",
            "scripts/runner_lifecycle.py",
            "scripts/install-whoop-body-sync-launchagent.sh",
            "scripts/install-daily-sync-launchagent.sh",
            "scripts/install-pinned-sync-launchagent.sh",
            "scripts/whoop-refresh-watchdog-run.sh",
            "scripts/install-whoop-refresh-watchdog-launchagent.sh",
        ):
            with self.subTest(path=relative_path):
                mode = stat.S_IMODE((REPO_ROOT / relative_path).stat().st_mode)
                self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
