#!/usr/bin/env python3
"""Safely create or validate owner-only operational files."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RuntimeError("operational-file parent must be a real directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_private_file(path: Path) -> str:
    if not path.is_absolute():
        raise RuntimeError("operational-file path must be absolute")
    parent_details = os.lstat(path.parent)
    if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
        raise RuntimeError("operational-file parent must be a real directory")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    create_flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | nofollow | nonblock
    created = False
    try:
        descriptor = os.open(path, create_flags, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(path, os.O_RDWR | os.O_APPEND | nofollow | nonblock)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise RuntimeError("operational file must be a single-link regular file")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if created:
        _fsync_directory(path.parent)
    return "created" if created else "validated"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    seen: set[Path] = set()
    for path in args.path:
        if path in seen:
            continue
        seen.add(path)
        ensure_private_file(path)
    print("private-files-ready")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"Private operational file setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
