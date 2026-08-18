#!/usr/bin/env python3
"""Run one LaunchAgent payload while holding its owner-only lifecycle lock."""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import sys
from pathlib import Path


GUARD_ENV = "OPENHEALTH_RUNNER_LIFECYCLE_GUARDED"
ALLOWED_GUARD_ENVS = {
    GUARD_ENV,
    "OPENHEALTH_INSTALLER_TRANSACTION_GUARDED",
}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RuntimeError("lifecycle-lock parent must be a real directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_lock(path: Path) -> int:
    if not path.is_absolute():
        raise RuntimeError("lifecycle lock must be absolute")
    parent_parent = os.lstat(path.parent.parent)
    if stat.S_ISLNK(parent_parent.st_mode) or not stat.S_ISDIR(parent_parent.st_mode):
        raise RuntimeError("lifecycle-lock parent root must be a real directory")
    created_parent = False
    try:
        os.mkdir(path.parent, 0o700)
        created_parent = True
    except FileExistsError:
        pass
    parent = os.lstat(path.parent)
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError("lifecycle-lock parent must be a real directory")
    parent_descriptor = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if created_parent or path.parent.name == "index":
            os.fchmod(parent_descriptor, 0o700)
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    if created_parent:
        _fsync_directory(path.parent.parent)
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
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        os.close(descriptor)
        raise RuntimeError("lifecycle lock must be a single-link regular file")
    os.fchmod(descriptor, 0o600)
    os.fsync(descriptor)
    if created:
        _fsync_directory(path.parent)
    return descriptor


def run(lock_path: Path, command: list[str], guard_env: str = GUARD_ENV) -> None:
    if not command or not Path(command[0]).is_absolute():
        raise RuntimeError("guarded command must start with an absolute executable")
    if guard_env not in ALLOWED_GUARD_ENVS:
        raise RuntimeError("unsupported lifecycle guard environment")
    descriptor = _open_lock(lock_path)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise RuntimeError("lifecycle lock became unsafe while acquiring it")
        os.set_inheritable(descriptor, True)
        environment = os.environ.copy()
        environment[guard_env] = "1"
        os.execve(command[0], command, environment)
    finally:
        os.close(descriptor)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--guard-env", default=GUARD_ENV, choices=sorted(ALLOWED_GUARD_ENVS))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    run(args.lock, args.command, args.guard_env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"Runner lifecycle guard failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
