#!/usr/bin/env python3
"""Create durable, privacy-safe local-date claims for scheduled WHOOP sync."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLAIM_SUFFIX = ".whoop-attempt"
SUCCESS_SUFFIX = ".whoop-success"


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RuntimeError("marker parent must be a real directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_root(path: Path) -> Path:
    if not path.is_absolute():
        raise RuntimeError("claim root must be absolute")
    parent_details = os.lstat(path.parent)
    if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
        raise RuntimeError("claim root parent must be a real directory")
    created = False
    try:
        os.mkdir(path, 0o700)
        created = True
    except FileExistsError:
        # Another scheduled runner may have won the directory-creation race.
        # Validate the winner instead of treating that expected race as an
        # operational failure.
        pass
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise RuntimeError("claim root must be a real directory")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise RuntimeError("claim root must be a real directory")
        os.fchmod(descriptor, 0o700)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if created:
        _fsync_directory(path.parent)
    return path


def _validate_marker(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_size != 0:
            raise RuntimeError(f"unsafe daily sync marker: {path.name}")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_marker(root: Path, path: Path) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _validate_marker(path)
        return False
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(root)
    return True


def _paths(root: Path, local_date: str) -> tuple[Path, Path]:
    if not DATE_PATTERN.fullmatch(local_date):
        raise RuntimeError("date must use YYYY-MM-DD")
    return root / f"{local_date}{CLAIM_SUFFIX}", root / f"{local_date}{SUCCESS_SUFFIX}"


def claim(root: Path, local_date: str) -> str:
    root = _ensure_root(root)
    attempt, success = _paths(root, local_date)
    if _exists(success):
        _validate_marker(success)
        if not _exists(attempt):
            raise RuntimeError("success marker exists without an attempt marker")
        _validate_marker(attempt)
        return "already_success"
    if _create_marker(root, attempt):
        return "claimed"
    return "already_attempted"


def mark_success(root: Path, local_date: str) -> str:
    root = _ensure_root(root)
    attempt, success = _paths(root, local_date)
    if not _exists(attempt):
        raise RuntimeError("cannot mark success without an attempt marker")
    _validate_marker(attempt)
    if _create_marker(root, success):
        return "success_marked"
    return "already_success"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("claim", "success"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--date", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    result = claim(args.root, args.date) if args.operation == "claim" else mark_success(args.root, args.date)
    print(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"Daily sync claim failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
