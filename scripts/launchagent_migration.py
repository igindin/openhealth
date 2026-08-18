#!/usr/bin/env python3
"""Crash-recoverable retirement of the legacy WHOOP body LaunchAgent.

Only private plist copies and empty control markers are written.  Before the
legacy service is disabled/booted out, its boot path is atomically replaced by
a valid daily plist (a bridge).  Consequently every power-loss topology has a
boot-loadable WHOOP schedule, while a completed ``prepare`` has only the daily
service effective.  All namespace changes are followed by a directory fsync.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path


LEGACY_LABEL = "org.openhealth.whoop-body-sync"
DAILY_LABEL = "com.openhealth.daily-sync"
WATCHDOG_LABEL = "com.openhealth.whoop-refresh-watchdog"
LEGACY_NAME = f"{LEGACY_LABEL}.plist"
DAILY_NAME = f"{DAILY_LABEL}.plist"
BACKUP_NAME = ".org.openhealth.whoop-body-sync.plist.openhealth-migration-backup"
MARKER_NAME = ".org.openhealth.whoop-body-sync.openhealth-migration-v1"
CONFLICT_PREFIX = ".org.openhealth.whoop-body-sync.plist.openhealth-migration-conflict-"
BRIDGE_PREFIX = ".org.openhealth.whoop-body-sync.openhealth-daily-bridge-"
COPY_TEMP_TOKEN = ".openhealth-copy-"
COPY_TEMP_PATTERN = re.compile(r"^\..+\.openhealth-copy-[0-9a-f]{32}$")


def _entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RuntimeError("migration parent must be a real directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise RuntimeError("LaunchAgents directory must be absolute")
    parent_details = os.lstat(path.parent)
    if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
        raise RuntimeError("LaunchAgents parent is not a regular directory")
    created = False
    try:
        os.mkdir(path, 0o700)
        created = True
    except FileExistsError:
        pass
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise RuntimeError("LaunchAgents path is not a regular directory")
    _fsync_directory(path)
    if created:
        _fsync_directory(path.parent)
    return path


def _open_private_regular(path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        os.close(descriptor)
        raise RuntimeError(f"unsafe migration entry: {path.name}")
    return descriptor


def _sync_private_regular(path: Path, *, require_empty: bool = False) -> None:
    descriptor = _open_private_regular(path)
    try:
        details = os.fstat(descriptor)
        if require_empty and details.st_size != 0:
            raise RuntimeError(f"migration marker is not empty: {path.name}")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _plist_label(path: Path) -> str:
    descriptor = _open_private_regular(path)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = plistlib.load(handle)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise RuntimeError(f"invalid LaunchAgent plist: {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    label = payload.get("Label") if isinstance(payload, dict) else None
    if not isinstance(label, str):
        raise RuntimeError(f"LaunchAgent plist has no label: {path.name}")
    return label


def _validate_legacy_backup(path: Path) -> None:
    _sync_private_regular(path)
    if _plist_label(path) != LEGACY_LABEL:
        raise RuntimeError("legacy recovery backup has an unexpected label")


def _copy_private_regular(source: Path, destination: Path) -> None:
    if _entry_exists(destination):
        raise RuntimeError(f"migration destination already exists: {destination.name}")
    if source.parent != destination.parent:
        raise RuntimeError("migration copies must stay in one directory")
    source_descriptor = _open_private_regular(source)
    temporary = destination.parent / f".{destination.name}{COPY_TEMP_TOKEN}{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    destination_descriptor = -1
    try:
        destination_descriptor = os.open(temporary, flags, 0o600)
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                view = view[written:]
        os.fchmod(destination_descriptor, 0o600)
        os.fsync(destination_descriptor)
        os.close(destination_descriptor)
        destination_descriptor = -1
        os.link(temporary, destination, follow_symlinks=False)
        _fsync_directory(destination.parent)
        temporary.unlink()
        _fsync_directory(destination.parent)
    except Exception:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
            destination_descriptor = -1
        if _entry_exists(temporary):
            try:
                temporary.unlink()
                _fsync_directory(destination.parent)
            except OSError:
                pass
        raise
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)


def _cleanup_copy_temporaries(root: Path) -> None:
    changed = False
    for path in root.iterdir():
        if not COPY_TEMP_PATTERN.fullmatch(path.name):
            continue
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise RuntimeError(f"unsafe migration copy temporary: {path.name}")
        path.unlink()
        changed = True
    if changed:
        _fsync_directory(root)


def _repair_interrupted_backup(root: Path) -> bool:
    backup = root / BACKUP_NAME
    if not _entry_exists(backup):
        return False
    details = os.lstat(backup)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise RuntimeError("unsafe legacy recovery backup")
    try:
        _validate_legacy_backup(backup)
        return True
    except RuntimeError:
        legacy = root / LEGACY_NAME
        if not _entry_exists(legacy) or _plist_label(legacy) != LEGACY_LABEL:
            raise
        conflict = root / f"{CONFLICT_PREFIX}{uuid.uuid4().hex}"
        os.replace(backup, conflict)
        _sync_private_regular(conflict)
        _fsync_directory(root)
        return False


def _create_or_validate_marker(root: Path) -> None:
    marker = root / MARKER_NAME
    if _entry_exists(marker):
        _sync_private_regular(marker, require_empty=True)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(marker, flags, 0o600)
    except FileExistsError:
        _sync_private_regular(marker, require_empty=True)
        return
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(root)


def _validate_staged_daily(root: Path, source: Path) -> None:
    if not source.is_absolute() or source.parent != root:
        raise RuntimeError("staged daily plist must be inside LaunchAgents")
    if source.name in {DAILY_NAME, LEGACY_NAME, BACKUP_NAME, MARKER_NAME}:
        raise RuntimeError("invalid staged daily plist name")
    _sync_private_regular(source)
    if _plist_label(source) != DAILY_LABEL:
        raise RuntimeError("staged replacement is not the daily LaunchAgent")


def _replace_with_daily_bridge(root: Path, legacy: Path, source: Path) -> None:
    bridge = root / f"{BRIDGE_PREFIX}{uuid.uuid4().hex}"
    _copy_private_regular(source, bridge)
    os.replace(bridge, legacy)
    _sync_private_regular(legacy)
    _fsync_directory(root)


def _launchctl_target(
    launchctl_bin: Path,
    launch_domain: str,
    service_label: str = LEGACY_LABEL,
) -> str:
    if not launchctl_bin.is_absolute():
        raise RuntimeError("launchctl path must be absolute")
    details = os.lstat(launchctl_bin)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise RuntimeError("launchctl must be a regular executable")
    if not os.access(launchctl_bin, os.X_OK):
        raise RuntimeError("launchctl is not executable")
    if not launch_domain or any(character.isspace() for character in launch_domain):
        raise RuntimeError("launch domain is invalid")
    if service_label not in {LEGACY_LABEL, DAILY_LABEL, WATCHDOG_LABEL}:
        raise RuntimeError("service label is invalid")
    return f"{launch_domain}/{service_label}"


@contextmanager
def _exclusive_lock(path: Path):
    if not path.is_absolute():
        raise RuntimeError("installer lock path must be absolute")
    parent_details = os.lstat(path.parent)
    if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
        raise RuntimeError("installer lock parent must be a real directory")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow | nonblock,
            0o600,
        )
        created = True
    except FileExistsError:
        descriptor = os.open(path, os.O_RDWR | nofollow | nonblock)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise RuntimeError("installer lock must be a single-link regular file")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        if created:
            _fsync_directory(path.parent)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise RuntimeError("installer lock became unsafe while acquiring it")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _service_is_active(output: str) -> bool:
    return re.search(r"(?m)^\s*pid\s*=\s*[0-9]+\s*$", output) is not None


def _wait_until_absent(
    launchctl_bin: Path,
    target: str,
) -> None:
    for _ in range(50):
        state = subprocess.run(
            [str(launchctl_bin), "print", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if state.returncode != 0:
            return
        time.sleep(0.1)
    raise RuntimeError("LaunchAgent remained registered after bootout")


def guarded_bootout(
    *,
    launchctl_bin: Path,
    launch_domain: str,
    service_label: str,
    sync_lock: Path | None,
    lifecycle_lock: Path | None,
) -> str:
    target = _launchctl_target(launchctl_bin, launch_domain, service_label)
    with ExitStack() as locks:
        if lifecycle_lock is not None:
            locks.enter_context(_exclusive_lock(lifecycle_lock))
        if sync_lock is not None:
            locks.enter_context(_exclusive_lock(sync_lock))
        state = subprocess.run(
            [str(launchctl_bin), "print", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
        if state.returncode != 0:
            return "absent"
        if _service_is_active(state.stdout):
            raise RuntimeError(f"refusing to boot out active {service_label}")
        retired = subprocess.run(
            [str(launchctl_bin), "bootout", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if retired.returncode != 0:
            raise RuntimeError(f"could not boot out {service_label}")
        _wait_until_absent(launchctl_bin, target)
    return "retired"


def _run_launchctl(
    launchctl_bin: Path,
    launch_domain: str,
    *,
    sync_lock: Path,
) -> None:
    target = _launchctl_target(launchctl_bin, launch_domain)
    with _exclusive_lock(sync_lock):
        disabled = subprocess.run(
            [str(launchctl_bin), "disable", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if disabled.returncode != 0:
            raise RuntimeError("could not disable the legacy WHOOP schedule")

        state = subprocess.run(
            [str(launchctl_bin), "print", target],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
        if state.returncode != 0:
            return
        if _service_is_active(state.stdout):
            _enable_legacy(launchctl_bin, launch_domain)
            raise RuntimeError("refusing to boot out an active legacy WHOOP sync")
        retired = subprocess.run(
            [str(launchctl_bin), "bootout", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if retired.returncode != 0:
            raise RuntimeError("could not boot out the legacy WHOOP schedule")
        _wait_until_absent(launchctl_bin, target)


def _enable_legacy(launchctl_bin: Path, launch_domain: str) -> None:
    target = _launchctl_target(launchctl_bin, launch_domain)
    enabled = subprocess.run(
        [str(launchctl_bin), "enable", target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if enabled.returncode != 0:
        raise RuntimeError("could not enable the legacy WHOOP fallback")


def recover(root: Path) -> str:
    """Finish any single-plist bridge rename left by SIGKILL/power loss."""

    root = _ensure_directory(root)
    _cleanup_copy_temporaries(root)
    legacy = root / LEGACY_NAME
    daily = root / DAILY_NAME
    backup = root / BACKUP_NAME
    marker = root / MARKER_NAME
    if not (_entry_exists(backup) and _entry_exists(marker)):
        return "no_recovery"
    _sync_private_regular(marker, require_empty=True)
    if not _repair_interrupted_backup(root):
        return "recovery_ready"

    if _entry_exists(daily) and _plist_label(daily) == LEGACY_LABEL:
        if _entry_exists(legacy):
            raise RuntimeError("both legacy fallback paths exist during recovery")
        os.replace(daily, legacy)
        _sync_private_regular(legacy)
        _fsync_directory(root)
        return "restored_legacy_path"

    if _entry_exists(legacy) and _plist_label(legacy) == DAILY_LABEL:
        if _entry_exists(daily):
            if _plist_label(daily) != DAILY_LABEL:
                raise RuntimeError("daily bridge conflicts with canonical daily path")
            legacy.unlink()
        else:
            os.replace(legacy, daily)
            _sync_private_regular(daily)
        _fsync_directory(root)
        return "retired_legacy_path"
    return "recovery_ready"


def prepare(
    root: Path,
    source: Path,
    *,
    launchctl_bin: Path,
    launch_domain: str,
    sync_lock: Path,
    legacy_loaded: bool,
) -> str:
    root = _ensure_directory(root)
    recover(root)
    _validate_staged_daily(root, source)
    legacy = root / LEGACY_NAME
    daily = root / DAILY_NAME
    backup = root / BACKUP_NAME
    had_legacy_path = _entry_exists(legacy)
    had_backup = _entry_exists(backup)
    had_daily_path = _entry_exists(daily)

    if had_backup:
        _validate_legacy_backup(backup)
    if not (had_legacy_path or had_backup or legacy_loaded):
        return "no_legacy"

    _create_or_validate_marker(root)
    status = "already_stashed" if had_backup else "stashed"

    if had_daily_path:
        if _plist_label(daily) != DAILY_LABEL:
            raise RuntimeError("existing daily boot plist has an unexpected label")
        # The prior daily plist already guarantees reboot coverage. Disable and
        # retire the old label first, so the currently registered provider jobs
        # are also reduced to one before any further filesystem mutation.
        _run_launchctl(
            launchctl_bin,
            launch_domain,
            sync_lock=sync_lock,
        )

    if had_legacy_path:
        label = _plist_label(legacy)
        if label == LEGACY_LABEL:
            if not had_backup:
                _copy_private_regular(legacy, backup)
                had_backup = True
            else:
                conflict = root / f"{CONFLICT_PREFIX}{uuid.uuid4().hex}"
                _copy_private_regular(legacy, conflict)
                status = "restashed"
            if had_daily_path:
                # The canonical daily plist is already the sole enabled boot
                # schedule, so do not create a duplicate-label bridge.
                legacy.unlink()
                _fsync_directory(root)
            else:
                # Replacing, rather than first renaming, the legacy boot path
                # means it is never absent on a legacy-only first upgrade.
                _replace_with_daily_bridge(root, legacy, source)
        elif label == DAILY_LABEL:
            if not had_backup:
                raise RuntimeError("daily bridge exists without a legacy backup")
        else:
            raise RuntimeError("unexpected legacy LaunchAgent label")
    elif not _entry_exists(daily):
        # Compatibility with an interrupted v1 migration which already moved
        # the legacy plist to BACKUP_NAME and left no daily boot plist.
        staged_daily = root / f"{BRIDGE_PREFIX}{uuid.uuid4().hex}"
        _copy_private_regular(source, staged_daily)
        os.replace(staged_daily, daily)
        _sync_private_regular(daily)
        _fsync_directory(root)

    if not had_daily_path:
        # On a legacy-only install the bridge must come before disabling the old
        # label. At this point it provides reboot coverage, so bootout cannot
        # create a zero-schedule power-loss state.
        _run_launchctl(
            launchctl_bin,
            launch_domain,
            sync_lock=sync_lock,
        )

    if _entry_exists(legacy):
        if _plist_label(legacy) != DAILY_LABEL:
            raise RuntimeError("legacy boot path was not converted to a daily bridge")
        if _entry_exists(daily):
            _sync_private_regular(daily)
            legacy.unlink()
        else:
            os.replace(legacy, daily)
            _sync_private_regular(daily)
        _fsync_directory(root)
    if not _entry_exists(daily):
        raise RuntimeError("migration retired legacy without a daily boot plist")
    if _plist_label(daily) != DAILY_LABEL:
        raise RuntimeError("daily boot plist has an unexpected label")
    return status


def promote(root: Path, source: Path) -> str:
    publish(root, source, DAILY_LABEL)
    return "promoted"


def _service_plist_name(service_label: str) -> str:
    if service_label not in {DAILY_LABEL, WATCHDOG_LABEL}:
        raise RuntimeError("unsupported published service label")
    return f"{service_label}.plist"


def _validate_plist_source(root: Path, source: Path, service_label: str) -> None:
    if not source.is_absolute() or source.parent != root:
        raise RuntimeError("plist source must be inside LaunchAgents")
    _sync_private_regular(source)
    if _plist_label(source) != service_label:
        raise RuntimeError("plist source label does not match the target service")


def publish(root: Path, source: Path, service_label: str) -> str:
    root = _ensure_directory(root)
    _cleanup_copy_temporaries(root)
    _validate_plist_source(root, source, service_label)
    destination = root / _service_plist_name(service_label)
    if source == destination:
        raise RuntimeError("plist source is already canonical")
    if _entry_exists(destination):
        _sync_private_regular(destination)
    os.replace(source, destination)
    _sync_private_regular(destination)
    _fsync_directory(root)
    return "published"


def snapshot(root: Path, source: Path, service_label: str) -> str:
    root = _ensure_directory(root)
    _cleanup_copy_temporaries(root)
    _validate_plist_source(root, source, service_label)
    destination = root / f".{service_label}.prior.{uuid.uuid4().hex}"
    _copy_private_regular(source, destination)
    _validate_plist_source(root, destination, service_label)
    return str(destination)


def remove_canonical(root: Path, service_label: str) -> str:
    root = _ensure_directory(root)
    _cleanup_copy_temporaries(root)
    destination = root / _service_plist_name(service_label)
    if not _entry_exists(destination):
        return "absent"
    _validate_plist_source(root, destination, service_label)
    destination.unlink()
    _fsync_directory(root)
    return "removed"


def complete(root: Path) -> str:
    root = _ensure_directory(root)
    _cleanup_copy_temporaries(root)
    legacy = root / LEGACY_NAME
    marker = root / MARKER_NAME
    if _entry_exists(legacy):
        raise RuntimeError("legacy boot plist still exists")
    if _entry_exists(root / BACKUP_NAME):
        _validate_legacy_backup(root / BACKUP_NAME)
    if _entry_exists(marker):
        _sync_private_regular(marker, require_empty=True)
        marker.unlink()
        _fsync_directory(root)
    return "complete"


def restore(
    root: Path,
    *,
    launchctl_bin: Path,
    launch_domain: str,
) -> str:
    root = _ensure_directory(root)
    _cleanup_copy_temporaries(root)
    legacy = root / LEGACY_NAME
    daily = root / DAILY_NAME
    backup = root / BACKUP_NAME
    marker = root / MARKER_NAME
    if _entry_exists(legacy):
        raise RuntimeError("legacy boot path already exists")
    if not _entry_exists(daily):
        raise RuntimeError("daily bridge is required for crash-safe legacy restore")
    if _plist_label(daily) != DAILY_LABEL:
        raise RuntimeError("daily bridge has an unexpected label")
    if not _entry_exists(backup):
        raise RuntimeError("legacy recovery backup is unavailable")
    _validate_legacy_backup(backup)
    # Enable while the daily boot plist is still intact. Then atomically turn
    # that same single boot path into the legacy fallback before renaming it;
    # every interruption therefore leaves one enabled boot-loadable label.
    _enable_legacy(launchctl_bin, launch_domain)
    fallback = root / f"{BRIDGE_PREFIX}{uuid.uuid4().hex}"
    _copy_private_regular(backup, fallback)
    os.replace(fallback, daily)
    _sync_private_regular(daily)
    _fsync_directory(root)
    os.replace(daily, legacy)
    _sync_private_regular(legacy)
    _fsync_directory(root)
    if _entry_exists(marker):
        _sync_private_regular(marker, require_empty=True)
        marker.unlink()
        _fsync_directory(root)
    return "restored"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "recover",
            "prepare",
            "promote",
            "publish",
            "snapshot",
            "remove",
            "complete",
            "restore",
            "guarded-bootout",
        ),
    )
    parser.add_argument("--launch-agents-dir", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--launchctl-bin", type=Path)
    parser.add_argument("--launch-domain")
    parser.add_argument("--legacy-loaded", action="store_true")
    parser.add_argument("--sync-lock", type=Path)
    parser.add_argument("--lifecycle-lock", type=Path)
    parser.add_argument("--service-label")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.operation in {"prepare", "promote", "publish", "snapshot"} and args.source is None:
        raise RuntimeError(f"{args.operation} requires --source")
    if args.operation == "prepare":
        if args.launchctl_bin is None or args.launch_domain is None or args.sync_lock is None:
            raise RuntimeError("prepare requires launchctl path, domain, and WHOOP sync lock")
        if args.lifecycle_lock is not None or args.service_label is not None:
            raise RuntimeError("invalid prepare options")
        result = prepare(
            args.launch_agents_dir,
            args.source,
            launchctl_bin=args.launchctl_bin,
            launch_domain=args.launch_domain,
            sync_lock=args.sync_lock,
            legacy_loaded=args.legacy_loaded,
        )
    elif args.operation == "promote":
        if any((args.launchctl_bin, args.launch_domain, args.legacy_loaded, args.sync_lock, args.lifecycle_lock, args.service_label)):
            raise RuntimeError("launchctl options are valid only for prepare")
        result = promote(args.launch_agents_dir, args.source)
    elif args.operation in {"publish", "snapshot"}:
        if any((args.launchctl_bin, args.launch_domain, args.legacy_loaded, args.sync_lock, args.lifecycle_lock)):
            raise RuntimeError(f"invalid {args.operation} options")
        if args.service_label is None:
            raise RuntimeError(f"{args.operation} requires a service label")
        operation = publish if args.operation == "publish" else snapshot
        result = operation(args.launch_agents_dir, args.source, args.service_label)
    elif args.operation == "remove":
        if any((args.source, args.launchctl_bin, args.launch_domain, args.legacy_loaded, args.sync_lock, args.lifecycle_lock)):
            raise RuntimeError("invalid remove options")
        if args.service_label is None:
            raise RuntimeError("remove requires a service label")
        result = remove_canonical(args.launch_agents_dir, args.service_label)
    elif args.operation == "restore":
        if args.source is not None or args.legacy_loaded or args.sync_lock or args.lifecycle_lock or args.service_label:
            raise RuntimeError("invalid restore options")
        if args.launchctl_bin is None or args.launch_domain is None:
            raise RuntimeError("restore requires launchctl path and domain")
        result = restore(
            args.launch_agents_dir,
            launchctl_bin=args.launchctl_bin,
            launch_domain=args.launch_domain,
        )
    elif args.operation == "recover":
        if any((args.source, args.launchctl_bin, args.launch_domain, args.legacy_loaded, args.sync_lock, args.lifecycle_lock, args.service_label)):
            raise RuntimeError("extra options are not valid for recovery")
        result = recover(args.launch_agents_dir)
    elif args.operation == "guarded-bootout":
        if args.source is not None or args.legacy_loaded:
            raise RuntimeError("invalid guarded bootout options")
        if any(value is None for value in (args.launchctl_bin, args.launch_domain, args.service_label)):
            raise RuntimeError("guarded bootout requires launchctl path, domain, and service label")
        if args.service_label == LEGACY_LABEL:
            if args.sync_lock is None or args.lifecycle_lock is not None:
                raise RuntimeError("legacy guarded bootout requires only the WHOOP sync lock")
        elif args.service_label == DAILY_LABEL:
            if args.sync_lock is None or args.lifecycle_lock is None:
                raise RuntimeError("daily guarded bootout requires lifecycle and WHOOP sync locks")
        elif args.service_label == WATCHDOG_LABEL:
            if args.sync_lock is not None or args.lifecycle_lock is None:
                raise RuntimeError("watchdog guarded bootout requires only its lifecycle lock")
        result = guarded_bootout(
            launchctl_bin=args.launchctl_bin,
            launch_domain=args.launch_domain,
            service_label=args.service_label,
            sync_lock=args.sync_lock,
            lifecycle_lock=args.lifecycle_lock,
        )
    else:
        if any((args.source, args.launchctl_bin, args.launch_domain, args.legacy_loaded, args.sync_lock, args.lifecycle_lock, args.service_label)):
            raise RuntimeError("extra options are not valid for this operation")
        result = complete(args.launch_agents_dir)
    print(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"LaunchAgent migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
