from __future__ import annotations

import os
import fcntl
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts/launchagent_migration.py"
LEGACY_LABEL = "org.openhealth.whoop-body-sync"
DAILY_LABEL = "com.openhealth.daily-sync"
LEGACY = f"{LEGACY_LABEL}.plist"
DAILY = f"{DAILY_LABEL}.plist"
BACKUP = ".org.openhealth.whoop-body-sync.plist.openhealth-migration-backup"
MARKER = ".org.openhealth.whoop-body-sync.openhealth-migration-v1"


def plist_bytes(label: str, fixture: str) -> bytes:
    return plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/true"], "Fixture": fixture})


class LaunchAgentMigrationTests(unittest.TestCase):
    def _launchctl(self, root: Path) -> Path:
        launchctl = root / "launchctl"
        launchctl.write_text(
            """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$MIGRATION_TEST_LOG"
case "$1" in
  disable) touch "$MIGRATION_TEST_DISABLED" ;;
  enable) rm -f -- "$MIGRATION_TEST_DISABLED" ;;
  bootout) rm -f -- "$MIGRATION_TEST_STATE" ;;
  print)
    [[ -f "$MIGRATION_TEST_STATE" ]] || exit 1
    if [[ -f "$MIGRATION_TEST_ACTIVE" ]]; then
      printf 'gui/501/synthetic = {\n\tpid = 4242\n}\n'
    fi
    ;;
esac
""",
            encoding="utf-8",
        )
        launchctl.chmod(0o755)
        return launchctl

    def _run(
        self,
        operation: str,
        root: Path,
        *,
        source: Path | None = None,
        launchctl: Path | None = None,
        loaded: bool = False,
        service_label: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(HELPER), operation, "--launch-agents-dir", str(root)]
        if source is not None:
            command.extend(("--source", str(source)))
        if operation in {"prepare", "restore"}:
            assert launchctl is not None
            command.extend(("--launchctl-bin", str(launchctl), "--launch-domain", "gui/501"))
        if operation == "prepare":
            command.extend(("--sync-lock", str(root.parent / "whoop-sync.lock")))
        if loaded:
            command.append("--legacy-loaded")
        if service_label is not None:
            command.extend(("--service-label", service_label))
        environment = os.environ.copy()
        environment.update(
            {
                "MIGRATION_TEST_LOG": str(root.parent / "launchctl.log"),
                "MIGRATION_TEST_STATE": str(root.parent / "body.state"),
                "MIGRATION_TEST_DISABLED": str(root.parent / "body.disabled"),
                "MIGRATION_TEST_ACTIVE": str(root.parent / "body.active"),
            }
        )
        return subprocess.run(command, text=True, capture_output=True, check=False, env=environment)

    def _source(self, root: Path) -> Path:
        source = root / ".com.openhealth.daily-sync.plist.staged"
        source.write_bytes(plist_bytes(DAILY_LABEL, "new-daily"))
        return source

    def test_legacy_only_prepare_always_leaves_daily_boot_and_retires_registered_body(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            legacy_bytes = plist_bytes(LEGACY_LABEL, "legacy")
            (root / LEGACY).write_bytes(legacy_bytes)
            (base / "body.state").touch()

            result = self._run("prepare", root, source=self._source(root), launchctl=launchctl, loaded=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "stashed")
            self.assertFalse((root / LEGACY).exists())
            self.assertEqual(plistlib.loads((root / DAILY).read_bytes())["Label"], DAILY_LABEL)
            self.assertEqual((root / BACKUP).read_bytes(), legacy_bytes)
            self.assertEqual((root / MARKER).read_bytes(), b"")
            self.assertFalse((base / "body.state").exists())
            self.assertTrue((base / "body.disabled").exists())
            for name in (BACKUP, MARKER, DAILY):
                self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o600)

            completed = self._run("complete", root)
            self.assertEqual(completed.stdout.strip(), "complete")
            self.assertFalse((root / MARKER).exists())
            self.assertTrue((root / BACKUP).is_file())

    def test_current_daily_is_effective_before_legacy_boot_path_is_changed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            (root / DAILY).write_bytes(plist_bytes(DAILY_LABEL, "old-daily"))
            (root / LEGACY).write_bytes(plist_bytes(LEGACY_LABEL, "legacy"))
            (base / "body.state").touch()

            result = self._run("prepare", root, source=self._source(root), launchctl=launchctl, loaded=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (base / "launchctl.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                calls[:3],
                [
                    f"disable gui/501/{LEGACY_LABEL}",
                    f"print gui/501/{LEGACY_LABEL}",
                    f"bootout gui/501/{LEGACY_LABEL}",
                ],
            )
            self.assertFalse((base / "body.state").exists())
            self.assertFalse((root / LEGACY).exists())
            self.assertEqual(plistlib.loads((root / DAILY).read_bytes())["Fixture"], "old-daily")

    def test_reintroduced_legacy_never_overwrites_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            first = plist_bytes(LEGACY_LABEL, "first")
            second = plist_bytes(LEGACY_LABEL, "second")
            (root / LEGACY).write_bytes(first)
            self.assertEqual(
                self._run("prepare", root, source=self._source(root), launchctl=launchctl).stdout.strip(),
                "stashed",
            )
            (root / LEGACY).write_bytes(second)
            self.assertEqual(
                self._run("prepare", root, source=self._source(root), launchctl=launchctl).stdout.strip(),
                "restashed",
            )
            self.assertEqual((root / BACKUP).read_bytes(), first)
            conflicts = list(root.glob(".org.openhealth.whoop-body-sync.plist.openhealth-migration-conflict-*"))
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].read_bytes(), second)
            self.assertFalse(conflicts[0].name.endswith(".plist"))

    def test_restore_and_recover_transitional_daily_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            legacy_bytes = plist_bytes(LEGACY_LABEL, "legacy")
            (root / BACKUP).write_bytes(legacy_bytes)
            (root / MARKER).touch(mode=0o600)
            (root / DAILY).write_bytes(plist_bytes(DAILY_LABEL, "bridge"))
            (base / "body.disabled").touch()

            restored = self._run("restore", root, launchctl=launchctl)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertFalse((root / DAILY).exists())
            self.assertEqual((root / LEGACY).read_bytes(), legacy_bytes)
            self.assertFalse((base / "body.disabled").exists())

            # Model SIGKILL after restore replaced the canonical daily file but
            # before it renamed that single body-labelled boot path.
            (root / DAILY).write_bytes((root / LEGACY).read_bytes())
            (root / LEGACY).unlink()
            (root / BACKUP).write_bytes(legacy_bytes)
            (root / MARKER).touch(mode=0o600)
            recovered = self._run("recover", root)
            self.assertEqual(recovered.stdout.strip(), "restored_legacy_path")
            self.assertFalse((root / DAILY).exists())
            self.assertEqual((root / LEGACY).read_bytes(), legacy_bytes)

    def test_promote_atomically_replaces_daily_plist(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "LaunchAgents"
            root.mkdir()
            (root / DAILY).write_bytes(plist_bytes(DAILY_LABEL, "old"))
            source = self._source(root)
            result = self._run("promote", root, source=source)
            self.assertEqual(result.stdout.strip(), "promoted")
            self.assertFalse(source.exists())
            self.assertEqual(plistlib.loads((root / DAILY).read_bytes())["Fixture"], "new-daily")
            self.assertEqual(stat.S_IMODE((root / DAILY).stat().st_mode), 0o600)

    def test_watchdog_snapshot_publish_and_restore_are_validated_and_durable(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "LaunchAgents"
            root.mkdir()
            watchdog_name = "com.openhealth.whoop-refresh-watchdog.plist"
            old_bytes = plist_bytes("com.openhealth.whoop-refresh-watchdog", "old")
            new_bytes = plist_bytes("com.openhealth.whoop-refresh-watchdog", "new")
            canonical = root / watchdog_name
            canonical.write_bytes(old_bytes)

            snapshotted = self._run(
                "snapshot",
                root,
                source=canonical,
                service_label="com.openhealth.whoop-refresh-watchdog",
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            snapshot_path = Path(snapshotted.stdout.strip())
            self.assertEqual(snapshot_path.read_bytes(), old_bytes)

            staged = root / ".watchdog.staged"
            staged.write_bytes(new_bytes)
            published = self._run(
                "publish",
                root,
                source=staged,
                service_label="com.openhealth.whoop-refresh-watchdog",
            )
            self.assertEqual(published.stdout.strip(), "published")
            self.assertEqual(canonical.read_bytes(), new_bytes)

            restored = self._run(
                "publish",
                root,
                source=snapshot_path,
                service_label="com.openhealth.whoop-refresh-watchdog",
            )
            self.assertEqual(restored.stdout.strip(), "published")
            self.assertEqual(canonical.read_bytes(), old_bytes)
            self.assertEqual(stat.S_IMODE(canonical.stat().st_mode), 0o600)

            wrong = root / ".wrong.staged"
            wrong.write_bytes(plist_bytes(DAILY_LABEL, "wrong"))
            refused = self._run(
                "publish",
                root,
                source=wrong,
                service_label="com.openhealth.whoop-refresh-watchdog",
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(canonical.read_bytes(), old_bytes)

    def test_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            target = root / "target"
            target.write_bytes(plist_bytes(LEGACY_LABEL, "target"))
            os.symlink(target, root / LEGACY)
            result = self._run("prepare", root, source=self._source(root), launchctl=launchctl)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((root / LEGACY).is_symlink())

    def test_late_active_legacy_sync_is_enabled_and_never_booted_out(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            daily_bytes = plist_bytes(DAILY_LABEL, "daily")
            legacy_bytes = plist_bytes(LEGACY_LABEL, "legacy")
            (root / DAILY).write_bytes(daily_bytes)
            (root / LEGACY).write_bytes(legacy_bytes)
            (base / "body.state").touch()
            (base / "body.active").touch()

            result = self._run("prepare", root, source=self._source(root), launchctl=launchctl, loaded=True)
            self.assertNotEqual(result.returncode, 0)
            calls = (base / "launchctl.log").read_text(encoding="utf-8").splitlines()
            self.assertIn(f"enable gui/501/{LEGACY_LABEL}", calls)
            self.assertNotIn(f"bootout gui/501/{LEGACY_LABEL}", calls)
            self.assertFalse((base / "body.disabled").exists())
            self.assertEqual((root / DAILY).read_bytes(), daily_bytes)
            self.assertEqual((root / LEGACY).read_bytes(), legacy_bytes)

    def test_partial_backup_and_copy_temporary_recover_from_intact_legacy(self) -> None:
        for backup_bytes in (plist_bytes(DAILY_LABEL, "wrong"), b"not a plist"):
            with self.subTest(backup=backup_bytes[:12]), tempfile.TemporaryDirectory() as raw_root:
                base = Path(raw_root)
                root = base / "LaunchAgents"
                root.mkdir()
                launchctl = self._launchctl(base)
                daily_bytes = plist_bytes(DAILY_LABEL, "daily")
                legacy_bytes = plist_bytes(LEGACY_LABEL, "legacy")
                (root / DAILY).write_bytes(daily_bytes)
                (root / LEGACY).write_bytes(legacy_bytes)
                (root / BACKUP).write_bytes(backup_bytes)
                (root / MARKER).touch(mode=0o600)
                interrupted_copy = root / f".{BACKUP}.openhealth-copy-{'0' * 32}"
                interrupted_copy.write_bytes(legacy_bytes[: max(1, len(legacy_bytes) // 2)])

                result = self._run("prepare", root, source=self._source(root), launchctl=launchctl)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "stashed")
                self.assertEqual((root / DAILY).read_bytes(), daily_bytes)
                self.assertFalse((root / LEGACY).exists())
                self.assertEqual((root / BACKUP).read_bytes(), legacy_bytes)
                self.assertFalse(interrupted_copy.exists())
                conflicts = list(root.glob(".org.openhealth.whoop-body-sync.plist.openhealth-migration-conflict-*"))
                self.assertEqual(len(conflicts), 1)
                self.assertEqual(conflicts[0].read_bytes(), backup_bytes)

    def test_process_death_mid_backup_copy_never_publishes_partial_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            legacy_bytes = plist_bytes(LEGACY_LABEL, "x" * (3 * 1024 * 1024))
            legacy = root / LEGACY
            backup = root / BACKUP
            legacy.write_bytes(legacy_bytes)
            (root / MARKER).touch(mode=0o600)
            crash_script = """
import importlib.util, os, pathlib, sys
spec = importlib.util.spec_from_file_location("migration_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_write = module.os.write
def die_after_first_write(descriptor, payload):
    written = original_write(descriptor, payload)
    module.os._exit(77)
module.os.write = die_after_first_write
module._copy_private_regular(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
"""
            crashed = subprocess.run(
                [sys.executable, "-c", crash_script, str(HELPER), str(legacy), str(backup)],
                check=False,
            )
            self.assertEqual(crashed.returncode, 77)
            self.assertFalse(backup.exists())
            temporaries = list(root.glob(".*.openhealth-copy-*"))
            self.assertEqual(len(temporaries), 1)
            self.assertLess(temporaries[0].stat().st_size, len(legacy_bytes))

            recovered = self._run(
                "prepare",
                root,
                source=self._source(root),
                launchctl=launchctl,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse(temporaries[0].exists())
            self.assertEqual(backup.read_bytes(), legacy_bytes)

    def test_guarded_bootout_waits_for_the_provider_sync_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            base = Path(raw_root)
            root = base / "LaunchAgents"
            root.mkdir()
            launchctl = self._launchctl(base)
            (base / "body.state").touch()
            sync_lock = base / "whoop-sync.lock"
            descriptor = os.open(sync_lock, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            environment = os.environ.copy()
            environment.update(
                {
                    "MIGRATION_TEST_LOG": str(base / "launchctl.log"),
                    "MIGRATION_TEST_STATE": str(base / "body.state"),
                    "MIGRATION_TEST_DISABLED": str(base / "body.disabled"),
                    "MIGRATION_TEST_ACTIVE": str(base / "body.active"),
                }
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(HELPER),
                    "guarded-bootout",
                    "--launch-agents-dir",
                    str(root),
                    "--launchctl-bin",
                    str(launchctl),
                    "--launch-domain",
                    "gui/501",
                    "--service-label",
                    LEGACY_LABEL,
                    "--sync-lock",
                    str(sync_lock),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            try:
                time.sleep(0.15)
                self.assertIsNone(process.poll())
                self.assertTrue((base / "body.state").exists())
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "retired")
            self.assertFalse((base / "body.state").exists())


if __name__ == "__main__":
    unittest.main()
