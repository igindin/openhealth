import contextlib
import io
import json
import os
import plistlib
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openhealth import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_GLOBALS = runpy.run_path(str(REPO_ROOT / "scripts/build_pinned_runtime.py"))


class WhoopCliScopeReductionTests(unittest.TestCase):
    def test_exchange_commands_pass_explicit_scope_reduction_override(self):
        token_payload = {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "scope": ["offline"],
        }
        cases = (
            (["whoop-exchange-code", "--code", "synthetic-code"], None),
            (
                [
                    "whoop-exchange-redirect-url",
                    "--url",
                    "http://localhost/callback?code=synthetic-code&state=state123",
                ],
                {"code": "synthetic-code", "state": "state123"},
            ),
        )

        for command, parsed_redirect in cases:
            with self.subTest(command=command[0]), tempfile.TemporaryDirectory() as tmp:
                token_path = Path(tmp) / "whoop_tokens.json"
                with (
                    patch("openhealth._certs.ensure_ca_certs"),
                    patch.object(
                        cli,
                        "ensure_repo_structure",
                        return_value=SimpleNamespace(whoop_tokens_path=token_path),
                    ),
                    patch.object(cli, "load_credentials_from_env", return_value=object()),
                    patch.object(cli, "exchange_code_for_tokens", return_value=token_payload),
                    patch.object(cli, "save_tokens") as save_tokens,
                    patch.object(cli, "extract_code_from_redirect_url", return_value=parsed_redirect)
                    if parsed_redirect is not None
                    else contextlib.nullcontext(),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    exit_code = cli.main([*command, "--allow-scope-reduction"])

                self.assertEqual(exit_code, 0)
                save_tokens.assert_called_once_with(
                    token_path,
                    token_payload,
                    allow_scope_reduction=True,
                    fresh_authorization=True,
                )


class WhoopRefreshGateCliTests(unittest.TestCase):
    def test_gate_is_a_separate_cli_invocation(self):
        outcome = {
            "rotation_verified": True,
            "authenticated_get": True,
            "probe_scope": "read:cycles",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            output = io.StringIO()
            with (
                patch("openhealth._certs.ensure_ca_certs"),
                patch.object(cli, "verify_whoop_refresh_rotation", return_value=outcome) as gate,
                contextlib.redirect_stdout(output),
            ):
                exit_code = cli.main(["--repo-root", str(root), "whoop-refresh-gate"])

        self.assertEqual(exit_code, 0)
        gate.assert_called_once_with(root)
        self.assertEqual(json.loads(output.getvalue()), outcome)


class WhoopLaunchAgentInstallerTests(unittest.TestCase):
    REVISION = "a" * 40
    DAILY_LABEL = "com.openhealth.daily-sync"
    BODY_LABEL = "org.openhealth.whoop-body-sync"
    WATCHDOG_LABEL = "com.openhealth.whoop-refresh-watchdog"
    BACKUP_NAME = ".org.openhealth.whoop-body-sync.plist.openhealth-migration-backup"
    MARKER_NAME = ".org.openhealth.whoop-body-sync.openhealth-migration-v1"

    def _prepare_install_tree(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        runtime = root / "runtime-releases" / self.REVISION
        (runtime / "openhealth").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "openhealth/__init__.py", runtime / "openhealth/__init__.py")
        (runtime / "REVISION").write_text(f"{self.REVISION}\n", encoding="utf-8")
        for relative in BUILDER_GLOBALS["REQUIRED_FILES"]:
            source = REPO_ROOT / relative.as_posix()
            destination = runtime / relative.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        manifest = BUILDER_GLOBALS["_manifest_payload"](runtime, self.REVISION)
        (runtime / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        BUILDER_GLOBALS["_apply_owner_only_modes"](runtime)
        BUILDER_GLOBALS["verify_release"](runtime, self.REVISION)

        data_root = root / "data-workspace"
        (data_root / "data/index").mkdir(parents=True)
        (data_root / "ui/web").mkdir(parents=True)
        (data_root / ".env").write_text("SYNTHETIC_SECRET=fixture\n", encoding="utf-8")
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        launchctl_log = root / "launchctl.log"
        launchctl = fake_bin / "launchctl"
        launchctl.write_text(
            """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$OPENHEALTH_TEST_LAUNCHCTL_LOG"
key_for() {
  case "$1" in
    *com.openhealth.daily-sync*|*com.openhealth.daily-sync.plist) printf daily ;;
    *org.openhealth.whoop-body-sync*|*org.openhealth.whoop-body-sync.plist) printf body ;;
    *com.openhealth.whoop-refresh-watchdog*|*com.openhealth.whoop-refresh-watchdog.plist) printf watchdog ;;
    *) printf unknown ;;
  esac
}
state_for() {
  case "$1" in
    daily) printf '%s' "$OPENHEALTH_TEST_DAILY_STATE" ;;
    body) printf '%s' "$OPENHEALTH_TEST_BODY_STATE" ;;
    watchdog) printf '%s' "$OPENHEALTH_TEST_WATCHDOG_STATE" ;;
    *) printf '%s' "$OPENHEALTH_TEST_UNKNOWN_STATE" ;;
  esac
}
case "$1" in
  disable)
    key="$(key_for "${2:-}")"
    if [[ "$key" == body ]]; then touch "$OPENHEALTH_TEST_BODY_DISABLED"; fi
    ;;
  enable)
    key="$(key_for "${2:-}")"
    if [[ "$key" == body ]]; then rm -f -- "$OPENHEALTH_TEST_BODY_DISABLED"; fi
    ;;
  print)
    key="$(key_for "${2:-}")"
    state="$(state_for "$key")"
    [[ -f "$state" ]] || exit 1
    count_file="$OPENHEALTH_TEST_PRINT_COUNT_PREFIX.$key"
    count=0
    if [[ -f "$count_file" ]]; then read -r count < "$count_file"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$count_file"
    if [[ "${OPENHEALTH_TEST_BECOME_ACTIVE_TARGET:-}" == "$key" &&
          "$count" -ge "${OPENHEALTH_TEST_ACTIVATE_ON_PRINT_AFTER:-999}" ]]; then
      touch "$OPENHEALTH_TEST_LATE_ACTIVE_PREFIX.$key"
    fi
    if [[ "${OPENHEALTH_TEST_START_LATE_TARGET:-}" == "$key" &&
          "$count" -ge "${OPENHEALTH_TEST_START_LATE_ON_PRINT_AFTER:-999}" &&
          ! -f "$OPENHEALTH_TEST_LATE_START_PREFIX.$key.started" ]]; then
      touch "$OPENHEALTH_TEST_LATE_START_PREFIX.$key.started"
      case "$key" in
        daily) lifecycle_lock="$OPENHEALTH_TEST_DATA_ROOT/data/index/daily-sync.lifecycle.lock" ;;
        watchdog) lifecycle_lock="$OPENHEALTH_TEST_DATA_ROOT/data/index/whoop-refresh-watchdog.lifecycle.lock" ;;
        *) lifecycle_lock="$OPENHEALTH_TEST_DATA_ROOT/data/index/unsupported.lifecycle.lock" ;;
      esac
      "$OPENHEALTH_TEST_REAL_PYTHON" -P \
        "$OPENHEALTH_TEST_RUNTIME_ROOT/scripts/runner_lifecycle.py" \
        --lock "$lifecycle_lock" -- /usr/bin/touch \
        "$OPENHEALTH_TEST_LATE_CRITICAL_PREFIX.$key" \
        </dev/null >/dev/null 2>&1 &
      printf '%s\n' "$!" > "$OPENHEALTH_TEST_LATE_START_PREFIX.$key.pid"
      sleep 0.1
    fi
    if [[ "${OPENHEALTH_TEST_ACTIVE_TARGET:-}" == "$key" ||
          -f "$OPENHEALTH_TEST_LATE_ACTIVE_PREFIX.$key" ]]; then
      printf 'gui/501/synthetic = {\n\tpid = 4242\n}\n'
    fi
    ;;
  bootout)
    key="$(key_for "${2:-}")"
    state="$(state_for "$key")"
    rm -f -- "$state"
    if [[ -f "$OPENHEALTH_TEST_LATE_START_PREFIX.$key.pid" ]]; then
      read -r late_pid < "$OPENHEALTH_TEST_LATE_START_PREFIX.$key.pid"
      kill -KILL "$late_pid" >/dev/null 2>&1 || true
      rm -f -- "$OPENHEALTH_TEST_LATE_START_PREFIX.$key.pid"
      sleep 0.05
    fi
    if [[ "${OPENHEALTH_TEST_SIGNAL_AFTER_BOOTOUT:-}" == "$key" &&
          ! -f "$OPENHEALTH_TEST_SIGNAL_MARKER" ]]; then
      touch "$OPENHEALTH_TEST_SIGNAL_MARKER"
      kill -TERM "$PPID"
      sleep 0.1
    fi
    ;;
  bootstrap)
    key="$(key_for "${3:-}")"
    state="$(state_for "$key")"
    if [[ "${OPENHEALTH_TEST_PAUSE_BOOTSTRAP_TARGET:-}" == "$key" &&
          ! -f "$OPENHEALTH_TEST_PAUSE_BOOTSTRAP_MARKER" ]]; then
      touch "$OPENHEALTH_TEST_PAUSE_BOOTSTRAP_MARKER"
      while [[ ! -f "$OPENHEALTH_TEST_PAUSE_BOOTSTRAP_RELEASE" ]]; do sleep 0.02; done
    fi
    if [[ "${OPENHEALTH_TEST_FAIL_BOOTSTRAP:-}" == "$key" &&
          ! -f "$OPENHEALTH_TEST_FAIL_MARKER" ]]; then
      touch "$OPENHEALTH_TEST_FAIL_MARKER"
      exit 42
    fi
    touch "$state"
    ;;
esac
""",
            encoding="utf-8",
        )
        launchctl.chmod(0o755)

        plutil = fake_bin / "plutil"
        plutil.write_text(
            """#!/usr/bin/env bash
exec "$OPENHEALTH_TEST_REAL_PYTHON" -E -s "$OPENHEALTH_TEST_PLUTIL_SCRIPT" "$@"
""",
            encoding="utf-8",
        )
        plutil.chmod(0o755)

        pinned_python = fake_bin / "python"
        pinned_python.write_text(
            """#!/usr/bin/env bash
set +e
"$OPENHEALTH_TEST_REAL_PYTHON" "$@"
status="$?"
if [[ "$status" -eq 0 && "${2:-}" == */scripts/launchagent_migration.py &&
      "${3:-}" == "${OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE:-never}" ]]; then
  kill -KILL 0
  sleep 1
fi
exit "$status"
""",
            encoding="utf-8",
        )
        pinned_python.chmod(0o755)
        return runtime, data_root, launchctl, launchctl_log, pinned_python

    def _environment(self, root: Path, launchctl: Path, launchctl_log: Path) -> dict[str, str]:
        environment = os.environ.copy()
        plutil = shutil.which("plutil") or str(root / "fake-bin/plutil")
        environment.update(
            {
                "HOME": str(root / "home"),
                "OPENHEALTH_LAUNCH_AGENTS_DIR": str(root / "LaunchAgents"),
                "OPENHEALTH_LAUNCHCTL_BIN": str(launchctl),
                "OPENHEALTH_PLUTIL_BIN": plutil,
                "OPENHEALTH_LAUNCH_DOMAIN": "gui/501",
                "OPENHEALTH_TEST_REAL_PYTHON": sys.executable,
                "OPENHEALTH_TEST_PLUTIL_SCRIPT": str(REPO_ROOT / "tests/fixtures/fake_plutil.py"),
                "OPENHEALTH_TEST_LAUNCHCTL_LOG": str(launchctl_log),
                "OPENHEALTH_TEST_DAILY_STATE": str(root / "launchctl.daily.state"),
                "OPENHEALTH_TEST_BODY_STATE": str(root / "launchctl.body.state"),
                "OPENHEALTH_TEST_BODY_DISABLED": str(root / "launchctl.body.disabled"),
                "OPENHEALTH_TEST_WATCHDOG_STATE": str(root / "launchctl.watchdog.state"),
                "OPENHEALTH_TEST_UNKNOWN_STATE": str(root / "launchctl.unknown.state"),
                "OPENHEALTH_TEST_FAIL_MARKER": str(root / "launchctl.fail-once"),
                "OPENHEALTH_TEST_SIGNAL_MARKER": str(root / "launchctl.signal-once"),
                "OPENHEALTH_TEST_PRINT_COUNT_PREFIX": str(root / "launchctl.print-count"),
                "OPENHEALTH_TEST_LATE_ACTIVE_PREFIX": str(root / "launchctl.late-active"),
                "OPENHEALTH_TEST_LATE_START_PREFIX": str(root / "launchctl.late-start"),
                "OPENHEALTH_TEST_LATE_CRITICAL_PREFIX": str(root / "launchctl.late-critical"),
                "OPENHEALTH_TEST_PAUSE_BOOTSTRAP_MARKER": str(root / "launchctl.bootstrap-paused"),
                "OPENHEALTH_TEST_PAUSE_BOOTSTRAP_RELEASE": str(root / "launchctl.bootstrap-release"),
                "PYTHONHOME": "/untrusted/pythonhome",
                "PYTHONPLATLIBDIR": "untrusted-lib",
            }
        )
        return environment

    def _install_command(self, runtime: Path, data_root: Path, python: Path, *extra: str) -> list[str]:
        return [
            "bash", str(REPO_ROOT / "scripts/install-whoop-body-sync-launchagent.sh"),
            "--runtime-root", str(runtime), "--data-root", str(data_root),
            "--revision", self.REVISION, "--python-bin", str(python), *extra,
        ]

    def _daily_command(self, runtime: Path, data_root: Path, python: Path, *extra: str) -> list[str]:
        return [
            "bash", str(REPO_ROOT / "scripts/install-daily-sync-launchagent.sh"),
            "--runtime-root", str(runtime), "--data-root", str(data_root),
            "--revision", self.REVISION, "--python-bin", str(python), *extra,
        ]

    def _watchdog_command(self, runtime: Path, data_root: Path, python: Path, *extra: str) -> list[str]:
        return [
            "bash", str(REPO_ROOT / "scripts/install-whoop-refresh-watchdog-launchagent.sh"),
            "--runtime-root", str(runtime), "--data-root", str(data_root),
            "--revision", self.REVISION, "--python-bin", str(python), *extra,
        ]

    def _run(self, command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        # A private process group lets the crash fixture SIGKILL the installer
        # and every command-substitution shell without touching the test runner.
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            start_new_session=True,
        )

    def _seed_schedules(self, root: Path, *, daily: bool = True, body: bool = True) -> tuple[Path, Path]:
        launch_agents = root / "LaunchAgents"
        launch_agents.mkdir(exist_ok=True)
        daily_path = launch_agents / f"{self.DAILY_LABEL}.plist"
        body_path = launch_agents / f"{self.BODY_LABEL}.plist"
        if daily:
            daily_path.write_bytes(
                plistlib.dumps({"Label": self.DAILY_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "daily"})
            )
            (root / "launchctl.daily.state").touch()
        if body:
            body_path.write_bytes(
                plistlib.dumps({"Label": self.BODY_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "body"})
            )
            (root / "launchctl.body.state").touch()
        return daily_path, body_path

    def _simulate_reboot(self, root: Path) -> list[str]:
        for state in root.glob("launchctl.*.state"):
            state.unlink()
        loaded: list[str] = []
        for plist in sorted((root / "LaunchAgents").glob("*.plist")):
            label = plistlib.loads(plist.read_bytes()).get("Label")
            if label == self.DAILY_LABEL:
                (root / "launchctl.daily.state").touch()
                loaded.append("daily")
            elif label == self.BODY_LABEL:
                if not (root / "launchctl.body.disabled").exists():
                    (root / "launchctl.body.state").touch()
                    loaded.append("body")
        return loaded

    def test_body_compatibility_installer_renders_single_daily_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            rendered = root / "rendered/daily.plist"
            completed = self._run(
                self._install_command(runtime, data_root, python, "--render-only", str(rendered)),
                self._environment(root, launchctl, log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse(log.exists())
            parsed = plistlib.loads(rendered.read_bytes())
            self.assertEqual(parsed["Label"], self.DAILY_LABEL)
            self.assertEqual(parsed["ProgramArguments"], ["/bin/bash", str(runtime / "scripts/daily-sync-run.sh")])
            self.assertEqual(parsed["StandardOutPath"], str(data_root / "data/index/daily-sync.log"))
            self.assertEqual(stat.S_IMODE(rendered.stat().st_mode), 0o600)

    def test_first_install_safely_creates_missing_index_before_transaction_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            index = data_root / "data/index"
            index.rmdir()

            completed = self._run(
                self._watchdog_command(runtime, data_root, python),
                self._environment(root, launchctl, log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(index.is_dir())
            self.assertEqual(stat.S_IMODE(index.stat().st_mode), 0o700)
            transaction_lock = index / "openhealth-launchagent.installer.lock"
            self.assertTrue(transaction_lock.is_file())
            self.assertEqual(stat.S_IMODE(transaction_lock.stat().st_mode), 0o600)

    def test_success_retires_legacy_before_daily_promotion_and_finally_verifies_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            daily_path, body_path = self._seed_schedules(root)
            old_daily = daily_path.read_bytes()
            old_body = body_path.read_bytes()
            legacy_log = data_root / "data/index/whoop-body-sync.log"
            legacy_log.write_text("preserve me\n", encoding="utf-8")

            completed = self._run(
                self._daily_command(runtime, data_root, python),
                self._environment(root, launchctl, log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertNotEqual(daily_path.read_bytes(), old_daily)
            self.assertFalse(body_path.exists())
            self.assertEqual((body_path.parent / self.BACKUP_NAME).read_bytes(), old_body)
            self.assertFalse((body_path.parent / self.MARKER_NAME).exists())
            self.assertTrue((root / "launchctl.daily.state").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertEqual(legacy_log.read_text(encoding="utf-8"), "preserve me\n")
            calls = log.read_text(encoding="utf-8").splitlines()
            body_bootout = calls.index(f"bootout gui/501/{self.BODY_LABEL}")
            daily_bootout = calls.index(f"bootout gui/501/{self.DAILY_LABEL}")
            daily_bootstrap = next(i for i, call in enumerate(calls) if call.startswith("bootstrap gui/501") and self.DAILY_LABEL in call)
            self.assertLess(body_bootout, daily_bootout)
            self.assertLess(body_bootout, daily_bootstrap)
            self.assertGreaterEqual(calls.count(f"print gui/501/{self.BODY_LABEL}"), 3)

    def test_failed_daily_bootstrap_keeps_prior_daily_as_only_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            daily_path, body_path = self._seed_schedules(root)
            old_daily = daily_path.read_bytes()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_FAIL_BOOTSTRAP"] = "daily"

            completed = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(daily_path.read_bytes(), old_daily)
            self.assertFalse(body_path.exists())
            self.assertTrue((root / "launchctl.daily.state").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_failed_first_daily_bootstrap_restores_legacy_as_sole_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            daily_path, body_path = self._seed_schedules(root, daily=False, body=True)
            old_body = body_path.read_bytes()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_FAIL_BOOTSTRAP"] = "daily"

            completed = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(daily_path.exists())
            self.assertEqual(body_path.read_bytes(), old_body)
            self.assertTrue((root / "launchctl.body.state").exists())
            self.assertFalse((root / "launchctl.daily.state").exists())
            self.assertEqual(self._simulate_reboot(root), ["body"])

    def test_sigkill_recovery_never_leaves_two_boot_loadable_plists(self):
        for phase in ("prepare", "publish"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
                daily_path, body_path = self._seed_schedules(root)
                environment = self._environment(root, launchctl, log)
                environment["OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE"] = phase

                crashed = self._run(self._daily_command(runtime, data_root, python), environment)
                self.assertNotEqual(crashed.returncode, 0)
                self.assertTrue(daily_path.is_file())
                self.assertFalse(body_path.exists())
                self.assertTrue((body_path.parent / self.BACKUP_NAME).is_file())
                self.assertTrue((body_path.parent / self.MARKER_NAME).is_file())
                self.assertEqual(self._simulate_reboot(root), ["daily"])

                environment.pop("OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE")
                recovered = self._run(self._daily_command(runtime, data_root, python), environment)
                self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
                self.assertTrue(daily_path.is_file())
                self.assertFalse(body_path.exists())
                self.assertTrue((root / "launchctl.daily.state").exists())
                self.assertFalse((root / "launchctl.body.state").exists())
                self.assertFalse((body_path.parent / self.MARKER_NAME).exists())
                self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_legacy_only_sigkill_after_prepare_keeps_one_daily_boot_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            daily_path, body_path = self._seed_schedules(root, daily=False, body=True)
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE"] = "prepare"

            crashed = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertNotEqual(crashed.returncode, 0)
            self.assertTrue(daily_path.is_file())
            self.assertEqual(plistlib.loads(daily_path.read_bytes())["Label"], self.DAILY_LABEL)
            self.assertFalse(body_path.exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertTrue((root / "launchctl.body.disabled").exists())
            self.assertTrue((body_path.parent / self.BACKUP_NAME).is_file())
            self.assertTrue((body_path.parent / self.MARKER_NAME).is_file())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

            environment.pop("OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE")
            recovered = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertTrue(daily_path.is_file())
            self.assertFalse(body_path.exists())
            self.assertTrue((root / "launchctl.daily.state").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertFalse((body_path.parent / self.MARKER_NAME).exists())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_prepare_crash_has_already_retired_registered_legacy_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            daily_path, body_path = self._seed_schedules(root)
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE"] = "prepare"

            crashed = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertNotEqual(crashed.returncode, 0)
            self.assertTrue((root / "launchctl.daily.state").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertTrue((root / "launchctl.body.disabled").exists())
            self.assertTrue(daily_path.is_file())
            self.assertFalse(body_path.exists())

            environment.pop("OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE")
            recovered = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertTrue((root / "launchctl.daily.state").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_rerun_recovers_body_label_left_at_daily_path_during_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            daily_path = launch_agents / f"{self.DAILY_LABEL}.plist"
            body_path = launch_agents / f"{self.BODY_LABEL}.plist"
            legacy_bytes = plistlib.dumps(
                {"Label": self.BODY_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "body"}
            )
            daily_path.write_bytes(legacy_bytes)
            (launch_agents / self.BACKUP_NAME).write_bytes(legacy_bytes)
            (launch_agents / self.MARKER_NAME).touch(mode=0o600)

            self.assertEqual(self._simulate_reboot(root), ["body"])
            completed = self._run(
                self._daily_command(runtime, data_root, python),
                self._environment(root, launchctl, log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(daily_path.is_file())
            self.assertEqual(plistlib.loads(daily_path.read_bytes())["Label"], self.DAILY_LABEL)
            self.assertFalse(body_path.exists())
            self.assertFalse((launch_agents / self.MARKER_NAME).exists())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_daily_and_watchdog_reject_unsafe_operational_logs(self):
        cases = (
            ("daily", "daily-sync.log", "symlink"),
            ("daily", "daily-sync.log", "hardlink"),
            ("daily", "daily-sync.log", "directory"),
            ("watchdog", "whoop-refresh-watchdog.err", "symlink"),
            ("watchdog", "whoop-refresh-watchdog.err", "hardlink"),
            ("watchdog", "whoop-refresh-watchdog.err", "fifo"),
        )
        for service, log_name, kind in cases:
            with self.subTest(service=service, kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
                unsafe_path = data_root / "data/index" / log_name
                target = root / "private-target"
                if kind in {"symlink", "hardlink"}:
                    target.write_text("do not touch\n", encoding="utf-8")
                    target.chmod(0o640)
                    if kind == "symlink":
                        unsafe_path.symlink_to(target)
                    else:
                        os.link(target, unsafe_path)
                elif kind == "directory":
                    unsafe_path.mkdir()
                else:
                    os.mkfifo(unsafe_path, 0o640)

                command = (
                    self._daily_command(runtime, data_root, python)
                    if service == "daily"
                    else self._watchdog_command(runtime, data_root, python)
                )
                completed = self._run(command, self._environment(root, launchctl, log))
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("Operational log setup failed closed", completed.stderr)
                if kind in {"symlink", "hardlink"}:
                    self.assertEqual(target.read_text(encoding="utf-8"), "do not touch\n")
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
                if kind == "symlink":
                    self.assertTrue(unsafe_path.is_symlink())
                elif kind == "hardlink":
                    self.assertEqual(target.stat().st_nlink, 2)
                calls = log.read_text(encoding="utf-8").splitlines()
                self.assertFalse(any(call.startswith(("bootstrap ", "bootout ", "disable ", "enable ")) for call in calls))

    def test_loaded_watchdog_is_replaced_and_reloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
            old_watchdog = plistlib.dumps(
                {"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "old"}
            )
            watchdog_path.write_bytes(old_watchdog)
            (root / "launchctl.watchdog.state").touch()

            completed = self._run(
                self._watchdog_command(runtime, data_root, python),
                self._environment(root, launchctl, log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertNotEqual(watchdog_path.read_bytes(), old_watchdog)
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            calls = log.read_text(encoding="utf-8").splitlines()
            bootout = calls.index(f"bootout gui/501/{self.WATCHDOG_LABEL}")
            bootstrap = next(
                index
                for index, call in enumerate(calls)
                if call.startswith("bootstrap gui/501") and self.WATCHDOG_LABEL in call
            )
            self.assertLess(bootout, bootstrap)

    def test_watchdog_publish_sigkill_keeps_one_valid_boot_plist_and_reruns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
            watchdog_path.write_bytes(
                plistlib.dumps(
                    {"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "old"}
                )
            )
            (root / "launchctl.watchdog.state").touch()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE"] = "publish"

            crashed = self._run(self._watchdog_command(runtime, data_root, python), environment)
            self.assertNotEqual(crashed.returncode, 0)
            self.assertTrue(watchdog_path.is_file())
            self.assertEqual(plistlib.loads(watchdog_path.read_bytes())["Label"], self.WATCHDOG_LABEL)
            self.assertFalse((root / "launchctl.watchdog.state").exists())

            environment.pop("OPENHEALTH_TEST_KILL_AFTER_MIGRATION_PHASE")
            recovered = self._run(self._watchdog_command(runtime, data_root, python), environment)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            self.assertEqual(plistlib.loads(watchdog_path.read_bytes())["Label"], self.WATCHDOG_LABEL)

    def test_late_active_watchdog_is_refused_without_bootout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
            old_watchdog = plistlib.dumps(
                {"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "old"}
            )
            watchdog_path.write_bytes(old_watchdog)
            (root / "launchctl.watchdog.state").touch()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_BECOME_ACTIVE_TARGET"] = "watchdog"
            environment["OPENHEALTH_TEST_ACTIVATE_ON_PRINT_AFTER"] = "2"

            refused = self._run(self._watchdog_command(runtime, data_root, python), environment)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(watchdog_path.read_bytes(), old_watchdog)
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertNotIn(f"bootout gui/501/{self.WATCHDOG_LABEL}", calls)

            environment.pop("OPENHEALTH_TEST_BECOME_ACTIVE_TARGET")
            environment.pop("OPENHEALTH_TEST_ACTIVATE_ON_PRINT_AFTER")
            (root / "launchctl.late-active.watchdog").unlink()
            recovered = self._run(self._watchdog_command(runtime, data_root, python), environment)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            self.assertNotEqual(watchdog_path.read_bytes(), old_watchdog)

    def test_failed_watchdog_bootstrap_restores_loaded_prior_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
            old_watchdog = plistlib.dumps(
                {"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"], "Fixture": "old"}
            )
            watchdog_path.write_bytes(old_watchdog)
            (root / "launchctl.watchdog.state").touch()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_FAIL_BOOTSTRAP"] = "watchdog"

            failed = self._run(self._watchdog_command(runtime, data_root, python), environment)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(watchdog_path.read_bytes(), old_watchdog)
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            calls = log.read_text(encoding="utf-8").splitlines()
            watchdog_bootstraps = [
                call
                for call in calls
                if call.startswith("bootstrap gui/501") and self.WATCHDOG_LABEL in call
            ]
            self.assertEqual(len(watchdog_bootstraps), 2)

    def test_failed_first_bootstrap_durably_removes_new_canonical_plist(self):
        for target in ("daily", "watchdog"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
                environment = self._environment(root, launchctl, log)
                environment["OPENHEALTH_TEST_FAIL_BOOTSTRAP"] = target
                if target == "daily":
                    command = self._daily_command(runtime, data_root, python)
                    label = self.DAILY_LABEL
                else:
                    command = self._watchdog_command(runtime, data_root, python)
                    label = self.WATCHDOG_LABEL
                canonical = root / "LaunchAgents" / f"{label}.plist"

                failed = self._run(command, environment)
                self.assertNotEqual(failed.returncode, 0)
                self.assertFalse(canonical.exists())
                self.assertFalse((root / f"launchctl.{target}.state").exists())

                recovered = self._run(command, environment)
                self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
                self.assertTrue(canonical.is_file())
                self.assertTrue((root / f"launchctl.{target}.state").exists())

    def test_signal_during_legacy_retirement_reconciles_to_prior_daily_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            daily_path, body_path = self._seed_schedules(root)
            old_daily = daily_path.read_bytes()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_SIGNAL_AFTER_BOOTOUT"] = "body"

            completed = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(daily_path.read_bytes(), old_daily)
            self.assertTrue(body_path.is_file())
            self.assertTrue((root / "launchctl.body.disabled").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

            environment.pop("OPENHEALTH_TEST_SIGNAL_AFTER_BOOTOUT")
            recovered = self._run(self._daily_command(runtime, data_root, python), environment)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertTrue(daily_path.is_file())
            self.assertFalse(body_path.exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_active_schedule_is_refused_without_mutation(self):
        for target in ("daily", "body"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
                daily_path, body_path = self._seed_schedules(root)
                daily_bytes, body_bytes = daily_path.read_bytes(), body_path.read_bytes()
                environment = self._environment(root, launchctl, log)
                environment["OPENHEALTH_TEST_ACTIVE_TARGET"] = target
                completed = self._run(self._daily_command(runtime, data_root, python), environment)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("actively running", completed.stderr)
                self.assertEqual(daily_path.read_bytes(), daily_bytes)
                self.assertEqual(body_path.read_bytes(), body_bytes)

    def test_schedule_becoming_active_at_final_recheck_is_never_booted_out(self):
        for target in ("body", "daily"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
                daily_path, body_path = self._seed_schedules(root)
                old_daily = daily_path.read_bytes()
                environment = self._environment(root, launchctl, log)
                environment["OPENHEALTH_TEST_BECOME_ACTIVE_TARGET"] = target
                environment["OPENHEALTH_TEST_ACTIVATE_ON_PRINT_AFTER"] = "2"

                refused = self._run(self._daily_command(runtime, data_root, python), environment)
                self.assertNotEqual(refused.returncode, 0)
                calls = log.read_text(encoding="utf-8").splitlines()
                service_label = self.BODY_LABEL if target == "body" else self.DAILY_LABEL
                self.assertNotIn(f"bootout gui/501/{service_label}", calls)
                self.assertEqual(daily_path.read_bytes(), old_daily)
                self.assertTrue((root / "launchctl.daily.state").exists())
                if target == "body":
                    self.assertTrue(body_path.is_file())
                    self.assertTrue((root / "launchctl.body.state").exists())
                    self.assertFalse((root / "launchctl.body.disabled").exists())
                else:
                    self.assertFalse(body_path.exists())
                    self.assertFalse((root / "launchctl.body.state").exists())

                environment.pop("OPENHEALTH_TEST_BECOME_ACTIVE_TARGET")
                environment.pop("OPENHEALTH_TEST_ACTIVATE_ON_PRINT_AFTER")
                (root / f"launchctl.late-active.{target}").unlink()
                recovered = self._run(self._daily_command(runtime, data_root, python), environment)
                self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
                self.assertTrue(daily_path.is_file())
                self.assertFalse(body_path.exists())
                self.assertFalse((root / "launchctl.body.state").exists())
                self.assertEqual(self._simulate_reboot(root), ["daily"])

    def test_post_recheck_runner_start_is_killed_before_claim_or_send(self):
        for target in ("daily", "watchdog"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
                if target == "daily":
                    self._seed_schedules(root)
                    command = self._daily_command(runtime, data_root, python)
                    label = self.DAILY_LABEL
                else:
                    launch_agents = root / "LaunchAgents"
                    launch_agents.mkdir()
                    watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
                    watchdog_path.write_bytes(
                        plistlib.dumps(
                            {"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"]}
                        )
                    )
                    (root / "launchctl.watchdog.state").touch()
                    command = self._watchdog_command(runtime, data_root, python)
                    label = self.WATCHDOG_LABEL
                environment = self._environment(root, launchctl, log)
                environment.update(
                    {
                        "OPENHEALTH_TEST_START_LATE_TARGET": target,
                        "OPENHEALTH_TEST_START_LATE_ON_PRINT_AFTER": "2",
                        "OPENHEALTH_TEST_RUNTIME_ROOT": str(runtime),
                        "OPENHEALTH_TEST_DATA_ROOT": str(data_root),
                    }
                )

                completed = self._run(command, environment)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                time.sleep(0.1)
                self.assertFalse((root / f"launchctl.late-critical.{target}").exists())
                self.assertFalse((data_root / "data/index/daily-sync-claims").exists())
                calls = log.read_text(encoding="utf-8").splitlines()
                self.assertIn(f"bootout gui/501/{label}", calls)

                rerun = self._run(command, environment)
                self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)

    def test_concurrent_watchdog_installers_are_serialized_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
            watchdog_path.write_bytes(
                plistlib.dumps({"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"]})
            )
            (root / "launchctl.watchdog.state").touch()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_PAUSE_BOOTSTRAP_TARGET"] = "watchdog"
            command = self._watchdog_command(runtime, data_root, python)

            first = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
            pause_marker = root / "launchctl.bootstrap-paused"
            deadline = time.monotonic() + 8
            while not pause_marker.exists() and time.monotonic() < deadline:
                if first.poll() is not None:
                    break
                time.sleep(0.02)
            if not pause_marker.exists():
                first_output = first.communicate(timeout=3)
                self.fail("installer never reached paused bootstrap: " + "".join(first_output))
            lines_before_second = log.read_text(encoding="utf-8").splitlines()

            second = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
            time.sleep(0.2)
            self.assertIsNone(second.poll())
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), lines_before_second)

            (root / "launchctl.bootstrap-release").touch()
            first_output = first.communicate(timeout=12)
            second_output = second.communicate(timeout=12)
            self.assertEqual(first.returncode, 0, "".join(first_output))
            self.assertEqual(second.returncode, 0, "".join(second_output))
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            self.assertEqual(plistlib.loads(watchdog_path.read_bytes())["Label"], self.WATCHDOG_LABEL)

    def test_daily_and_watchdog_installers_share_one_transaction_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            self._seed_schedules(root)
            launch_agents = root / "LaunchAgents"
            watchdog_path = launch_agents / f"{self.WATCHDOG_LABEL}.plist"
            watchdog_path.write_bytes(
                plistlib.dumps({"Label": self.WATCHDOG_LABEL, "ProgramArguments": ["/bin/true"]})
            )
            (root / "launchctl.watchdog.state").touch()
            environment = self._environment(root, launchctl, log)
            environment["OPENHEALTH_TEST_PAUSE_BOOTSTRAP_TARGET"] = "watchdog"

            watchdog_install = subprocess.Popen(
                self._watchdog_command(runtime, data_root, python),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
            pause_marker = root / "launchctl.bootstrap-paused"
            deadline = time.monotonic() + 8
            while not pause_marker.exists() and time.monotonic() < deadline:
                if watchdog_install.poll() is not None:
                    break
                time.sleep(0.02)
            if not pause_marker.exists():
                output = watchdog_install.communicate(timeout=3)
                self.fail("watchdog installer never paused: " + "".join(output))
            calls_before_daily = log.read_text(encoding="utf-8").splitlines()

            daily_install = subprocess.Popen(
                self._daily_command(runtime, data_root, python),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
            time.sleep(0.2)
            self.assertIsNone(daily_install.poll())
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), calls_before_daily)

            (root / "launchctl.bootstrap-release").touch()
            watchdog_output = watchdog_install.communicate(timeout=12)
            daily_output = daily_install.communicate(timeout=12)
            self.assertEqual(watchdog_install.returncode, 0, "".join(watchdog_output))
            self.assertEqual(daily_install.returncode, 0, "".join(daily_output))
            self.assertTrue((root / "launchctl.watchdog.state").exists())
            self.assertTrue((root / "launchctl.daily.state").exists())
            self.assertFalse((root / "launchctl.body.state").exists())
            self.assertFalse((launch_agents / f"{self.BODY_LABEL}.plist").exists())

    def test_installer_rejects_tampered_runtime_before_launchctl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, log, python = self._prepare_install_tree(root)
            tampered = runtime / "openhealth/__init__.py"
            tampered.chmod(0o600)
            tampered.write_text("# tampered\n", encoding="utf-8")
            completed = self._run(
                self._daily_command(runtime, data_root, python, "--render-only", str(root / "daily.plist")),
                self._environment(root, launchctl, log),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("manifest verification failed", completed.stderr)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
