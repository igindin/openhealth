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


class WhoopLaunchAgentInstallerTests(unittest.TestCase):
    REVISION = "a" * 40

    def _prepare_install_tree(self, root: Path) -> tuple[Path, Path, Path, Path]:
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
        manifest_path = runtime / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
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
set -u
printf '%s\\n' "$*" >> "$OPENHEALTH_TEST_LAUNCHCTL_LOG"
case "$1" in
  print)
    if [[ ! -f "$OPENHEALTH_TEST_LAUNCHCTL_STATE" ]]; then
      exit 1
    fi
    if [[ -n "${OPENHEALTH_TEST_ACTIVE_PID:-}" ]]; then
      printf 'gui/501/synthetic = {\\n\\tpid = %s\\n}\\n' "$OPENHEALTH_TEST_ACTIVE_PID"
    fi
    ;;
  bootout)
    rm -f -- "$OPENHEALTH_TEST_LAUNCHCTL_STATE"
    if [[ "${OPENHEALTH_TEST_SIGNAL_AFTER_BOOTOUT:-0}" == 1 &&
          ! -f "$OPENHEALTH_TEST_SIGNAL_MARKER" ]]; then
      touch "$OPENHEALTH_TEST_SIGNAL_MARKER"
      kill -TERM "$PPID"
      sleep 0.05
    fi
    ;;
  bootstrap)
    candidate="${3:-}"
    if [[ "${OPENHEALTH_TEST_FAIL_NEW:-0}" == 1 && ! -f "$OPENHEALTH_TEST_FAIL_MARKER" ]]; then
      touch "$OPENHEALTH_TEST_FAIL_MARKER"
      exit 42
    fi
    touch "$OPENHEALTH_TEST_LAUNCHCTL_STATE"
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
        return runtime, data_root, launchctl, launchctl_log

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
                "OPENHEALTH_TEST_LAUNCHCTL_STATE": str(root / "launchctl.state"),
                "OPENHEALTH_TEST_FAIL_MARKER": str(root / "launchctl.fail-once"),
                "OPENHEALTH_TEST_SIGNAL_MARKER": str(root / "launchctl.signal-once"),
                "PYTHONHOME": "/untrusted/pythonhome",
                "PYTHONPLATLIBDIR": "untrusted-lib",
            }
        )
        return environment

    def _install_command(
        self,
        runtime: Path,
        data_root: Path,
        *extra: str,
    ) -> list[str]:
        return [
            "bash",
            str(REPO_ROOT / "scripts/install-whoop-body-sync-launchagent.sh"),
            "--runtime-root",
            str(runtime),
            "--data-root",
            str(data_root),
            "--revision",
            self.REVISION,
            "--python-bin",
            sys.executable,
            *extra,
        ]

    def _install_daily_command(
        self,
        runtime: Path,
        data_root: Path,
        *extra: str,
    ) -> list[str]:
        return [
            "bash",
            str(REPO_ROOT / "scripts/install-daily-sync-launchagent.sh"),
            "--runtime-root",
            str(runtime),
            "--data-root",
            str(data_root),
            "--revision",
            self.REVISION,
            "--python-bin",
            sys.executable,
            *extra,
        ]

    def test_render_only_pins_runtime_and_never_calls_launchctl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            rendered = root / "rendered/body.plist"

            completed = subprocess.run(
                self._install_command(runtime, data_root, "--render-only", str(rendered)),
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(root, launchctl, launchctl_log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse(launchctl_log.exists())
            self.assertFalse((root / "LaunchAgents").exists())
            self.assertEqual(stat.S_IMODE(rendered.stat().st_mode), 0o600)

            parsed = plistlib.loads(rendered.read_bytes())
            self.assertEqual(parsed["Umask"], 63)
            self.assertEqual(parsed["WorkingDirectory"], str(runtime))
            self.assertEqual(
                parsed["ProgramArguments"],
                ["/bin/bash", str(runtime / "scripts/whoop-body-sync-run.sh")],
            )
            environment = parsed["EnvironmentVariables"]
            self.assertEqual(environment["OPENHEALTH_RUNTIME_ROOT"], str(runtime))
            self.assertEqual(environment["OPENHEALTH_DATA_ROOT"], str(data_root))
            self.assertEqual(environment["OPENHEALTH_RUNTIME_REVISION"], self.REVISION)
            self.assertEqual(environment["PYTHONPATH"], str(runtime))
            self.assertEqual(environment["PYTHONSAFEPATH"], "1")
            self.assertEqual(parsed["StandardOutPath"], str(data_root / "data/index/whoop-body-sync.log"))

    def test_upgrade_preserves_logs_and_loads_promoted_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            index_dir = data_root / "data/index"
            log_path = index_dir / "whoop-body-sync.log"
            err_path = index_dir / "whoop-body-sync.err"
            token_path = index_dir / "whoop_tokens.json"
            log_path.write_text("old stdout\n", encoding="utf-8")
            err_path.write_text("old stderr\n", encoding="utf-8")
            token_path.write_text('{"access_token":"synthetic"}\n', encoding="utf-8")
            for path in (log_path, err_path, token_path):
                path.chmod(0o644)

            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "org.openhealth.whoop-body-sync.plist"
            destination.write_text("old plist\n", encoding="utf-8")
            destination.chmod(0o644)
            (root / "launchctl.state").touch()

            completed = subprocess.run(
                self._install_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(root, launchctl, launchctl_log),
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            self.assertEqual(log_path.read_text(encoding="utf-8"), "old stdout\n")
            self.assertEqual(err_path.read_text(encoding="utf-8"), "old stderr\n")
            self.assertEqual(token_path.read_text(encoding="utf-8"), '{"access_token":"synthetic"}\n')
            for path in (data_root / ".env", log_path, err_path, token_path, destination):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            parsed = plistlib.loads(destination.read_bytes())
            self.assertEqual(parsed["WorkingDirectory"], str(runtime))
            calls = launchctl_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [call.split()[0] for call in calls],
                ["print", "bootout", "print", "bootstrap", "print"],
            )
            self.assertEqual(calls[3], f"bootstrap gui/501 {destination}")
            self.assertEqual(list(launch_agents.glob(".org.openhealth.whoop-body-sync.plist.*")), [])

    def test_failed_bootstrap_restores_loaded_prior_service_and_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "org.openhealth.whoop-body-sync.plist"
            destination.write_text("known-good plist\n", encoding="utf-8")
            prior_runner = root / "prior-runtime/whoop-body-sync-run.sh"
            prior_runner.parent.mkdir()
            prior_runner.write_text("known-good runner\n", encoding="utf-8")
            (root / "launchctl.state").touch()

            environment = self._environment(root, launchctl, launchctl_log)
            environment["OPENHEALTH_TEST_FAIL_NEW"] = "1"

            completed = subprocess.run(
                self._install_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(destination.read_text(encoding="utf-8"), "known-good plist\n")
            self.assertEqual(prior_runner.read_text(encoding="utf-8"), "known-good runner\n")
            self.assertTrue((root / "launchctl.state").exists())
            calls = launchctl_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [call.split()[0] for call in calls],
                ["print", "bootout", "print", "bootstrap", "bootout", "print", "bootstrap"],
            )
            self.assertEqual(calls[-1], f"bootstrap gui/501 {destination}")
            self.assertEqual(list(launch_agents.glob(".org.openhealth.whoop-body-sync.plist.*")), [])

    def test_running_service_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "org.openhealth.whoop-body-sync.plist"
            destination.write_text("known-good running plist\n", encoding="utf-8")
            (root / "launchctl.state").touch()

            environment = self._environment(root, launchctl, launchctl_log)
            environment["OPENHEALTH_TEST_ACTIVE_PID"] = "123"
            completed = subprocess.run(
                self._install_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("actively running", completed.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "known-good running plist\n")
            self.assertTrue((root / "launchctl.state").exists())
            calls = launchctl_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual([call.split()[0] for call in calls], ["print"])

    def test_failure_after_plist_promotion_restores_owner_only_prior_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "org.openhealth.whoop-body-sync.plist"
            prior_bytes = b"known-good plist after promotion window\n"
            destination.write_bytes(prior_bytes)
            destination.chmod(0o600)
            (root / "launchctl.state").touch()

            fake_bin = root / "fake-mv-bin"
            fake_bin.mkdir()
            fake_mv = fake_bin / "mv"
            fake_mv.write_text(
                """#!/usr/bin/env bash
set -eu
if [[ "${2:-}" == */.org.openhealth.whoop-body-sync.plist.* &&
      "${3:-}" == */org.openhealth.whoop-body-sync.plist ]]; then
  /bin/mv "$@"
  exit 43
fi
exec /bin/mv "$@"
""",
                encoding="utf-8",
            )
            fake_mv.chmod(0o755)
            environment = self._environment(root, launchctl, launchctl_log)
            environment["PATH"] = os.pathsep.join((str(fake_bin), environment["PATH"]))

            completed = subprocess.run(
                self._install_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(destination.read_bytes(), prior_bytes)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertTrue((root / "launchctl.state").exists())
            calls = launchctl_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [call.split()[0] for call in calls],
                ["print", "bootout", "print", "bootout", "print", "bootstrap"],
            )
            self.assertEqual(calls[-1], f"bootstrap gui/501 {destination}")
            self.assertEqual(list(launch_agents.glob(".org.openhealth.whoop-body-sync.*")), [])

    def test_failed_daily_bootstrap_restores_loaded_prior_service_and_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "com.openhealth.daily-sync.plist"
            destination.write_text("known-good daily plist\n", encoding="utf-8")
            dashboard_data = data_root / "ui/web/data.local.json"
            dashboard_data.write_text('{"synthetic":true}\n', encoding="utf-8")
            dashboard_data.chmod(0o644)
            (root / "launchctl.state").touch()

            environment = self._environment(root, launchctl, launchctl_log)
            environment["OPENHEALTH_TEST_FAIL_NEW"] = "1"

            completed = subprocess.run(
                self._install_daily_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(destination.read_text(encoding="utf-8"), "known-good daily plist\n")
            self.assertEqual(stat.S_IMODE(dashboard_data.stat().st_mode), 0o600)
            self.assertTrue((root / "launchctl.state").exists())
            calls = launchctl_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [call.split()[0] for call in calls],
                ["print", "bootout", "print", "bootstrap", "bootout", "print", "bootstrap"],
            )
            self.assertEqual(calls[-1], f"bootstrap gui/501 {destination}")
            self.assertEqual(list(launch_agents.glob(".com.openhealth.daily-sync.plist.*")), [])

    def test_installer_rejects_tampered_runtime_with_unchanged_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            tampered = runtime / "openhealth/__init__.py"
            tampered.chmod(0o600)
            tampered.write_text("# tampered with unchanged REVISION\n", encoding="utf-8")

            completed = subprocess.run(
                self._install_command(
                    runtime,
                    data_root,
                    "--render-only",
                    str(root / "rendered/body.plist"),
                ),
                check=False,
                capture_output=True,
                text=True,
                env=self._environment(root, launchctl, launchctl_log),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("manifest verification failed", completed.stderr)
            self.assertFalse(launchctl_log.exists())

    def test_active_loaded_service_is_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "org.openhealth.whoop-body-sync.plist"
            prior_bytes = b"known-good active plist\n"
            destination.write_bytes(prior_bytes)
            (root / "launchctl.state").touch()
            original_env_mode = stat.S_IMODE((data_root / ".env").stat().st_mode)
            environment = self._environment(root, launchctl, launchctl_log)
            environment["OPENHEALTH_TEST_ACTIVE_PID"] = "4242"

            completed = subprocess.run(
                self._install_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("actively running", completed.stderr)
            self.assertEqual(destination.read_bytes(), prior_bytes)
            self.assertTrue((root / "launchctl.state").exists())
            self.assertEqual(stat.S_IMODE((data_root / ".env").stat().st_mode), original_env_mode)
            self.assertFalse((data_root / "data/index/whoop-body-sync.log").exists())
            self.assertEqual(list(launch_agents.glob(".org.openhealth.whoop-body-sync.*")), [])
            self.assertEqual(launchctl_log.read_text(encoding="utf-8").splitlines(), [
                "print gui/501/org.openhealth.whoop-body-sync",
            ])

    def test_signal_after_bootout_restores_prior_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, data_root, launchctl, launchctl_log = self._prepare_install_tree(root)
            launch_agents = root / "LaunchAgents"
            launch_agents.mkdir()
            destination = launch_agents / "org.openhealth.whoop-body-sync.plist"
            prior_bytes = b"known-good signal-window plist\n"
            destination.write_bytes(prior_bytes)
            (root / "launchctl.state").touch()
            environment = self._environment(root, launchctl, launchctl_log)
            environment["OPENHEALTH_TEST_SIGNAL_AFTER_BOOTOUT"] = "1"

            completed = subprocess.run(
                self._install_command(runtime, data_root),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(destination.read_bytes(), prior_bytes)
            self.assertTrue((root / "launchctl.state").exists())
            calls = launchctl_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                [call.split()[0] for call in calls],
                ["print", "bootout", "bootout", "print", "bootstrap"],
            )
            self.assertEqual(calls[-1], f"bootstrap gui/501 {destination}")


if __name__ == "__main__":
    unittest.main()
