#!/usr/bin/env python3
"""Build and verify a non-writable owner-only runtime from one Git SHA."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PACKAGE_PREFIX = PurePosixPath("openhealth")
REQUIRED_FILES = (
    PurePosixPath("scripts/build_pinned_runtime.py"),
    PurePosixPath("scripts/whoop-body-sync-run.sh"),
    PurePosixPath("scripts/whoop-body-sync.plist"),
    PurePosixPath("scripts/daily-sync-run.sh"),
    PurePosixPath("scripts/daily-sync.plist"),
    PurePosixPath("scripts/install-whoop-body-sync-launchagent.sh"),
    PurePosixPath("scripts/install-daily-sync-launchagent.sh"),
    PurePosixPath("scripts/install-pinned-sync-launchagent.sh"),
    PurePosixPath("ui/web/build_dashboard_data.py"),
)
EXECUTABLE_FILES = frozenset(
    path
    for path in REQUIRED_FILES
    if path.suffix == ".sh"
)
METADATA_FILES = frozenset(
    {
        PurePosixPath("REVISION"),
        PurePosixPath("MANIFEST.json"),
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_commit(source: Path, requested_revision: str) -> str:
    revision = subprocess.check_output(
        [
            "git",
            "-C",
            str(source),
            "rev-parse",
            "--verify",
            f"{requested_revision}^{{commit}}",
        ],
        text=True,
    ).strip()
    if not REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError("Git did not resolve a full lowercase commit SHA")
    return revision


def _is_payload_path(relative: PurePosixPath) -> bool:
    return (
        relative == PACKAGE_PREFIX
        or PACKAGE_PREFIX in relative.parents
        or relative in REQUIRED_FILES
        or relative == PurePosixPath("REVISION")
    )


def _is_allowed_directory(relative: PurePosixPath) -> bool:
    if relative == PACKAGE_PREFIX or PACKAGE_PREFIX in relative.parents:
        return True
    return any(relative in required.parents for required in REQUIRED_FILES)


def _validate_archive_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    seen: set[PurePosixPath] = set()
    for member in members:
        relative = PurePosixPath(member.name)
        if relative in seen:
            raise RuntimeError(f"duplicate runtime archive member: {member.name}")
        seen.add(relative)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise RuntimeError(f"unsafe runtime archive member: {member.name}")
        if member.isdir():
            allowed = _is_allowed_directory(relative)
        elif member.isfile():
            allowed = _is_payload_path(relative)
        else:
            allowed = False
        if not allowed:
            raise RuntimeError(f"unsafe runtime archive member: {member.name}")
    return members


def _extract_validated_members(
    archive: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    staging: Path,
) -> None:
    """Extract only validated regular files without tarfile version-specific filters."""

    for member in members:
        relative = PurePosixPath(member.name)
        destination = staging.joinpath(*relative.parts)
        if member.isdir():
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"could not read runtime archive member: {member.name}")
        with source, destination.open("xb") as output:
            shutil.copyfileobj(source, output)


def _expected_mode(relative: PurePosixPath, *, is_directory: bool = False) -> int:
    if is_directory or relative in EXECUTABLE_FILES:
        return 0o500
    return 0o400


def _payload_files(release: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(release.rglob("*")):
        relative = PurePosixPath(path.relative_to(release).as_posix())
        if path.is_symlink():
            raise RuntimeError(f"runtime symlink is forbidden: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError(f"runtime special file is forbidden: {relative}")
        if relative == PurePosixPath("MANIFEST.json"):
            continue
        if not _is_payload_path(relative):
            raise RuntimeError(f"unexpected runtime file: {relative}")
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in relative.parts:
            raise RuntimeError(f"generated Python file is forbidden: {relative}")
        files[relative.as_posix()] = {
            "mode": f"{_expected_mode(relative):04o}",
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }

    required_payload = {*REQUIRED_FILES, PurePosixPath("openhealth/__init__.py"), PurePosixPath("REVISION")}
    missing = sorted(path.as_posix() for path in required_payload if path.as_posix() not in files)
    if missing:
        raise RuntimeError(f"runtime payload is incomplete: {', '.join(missing)}")
    return files


def _manifest_payload(release: Path, revision: str) -> dict[str, Any]:
    return {
        "files": _payload_files(release),
        "revision": revision,
        "schema_version": SCHEMA_VERSION,
    }


def _apply_owner_only_modes(release: Path) -> None:
    for path in sorted(release.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise RuntimeError(f"runtime symlink is forbidden: {path}")
        relative = PurePosixPath(path.relative_to(release).as_posix())
        if path.is_dir():
            path.chmod(_expected_mode(relative, is_directory=True))
        elif path.is_file():
            path.chmod(_expected_mode(relative))
        else:
            raise RuntimeError(f"runtime special file is forbidden: {path}")
    release.chmod(0o500)


def verify_release(release: Path, revision: str) -> dict[str, Any]:
    """Repeat all checksum, allowlist, marker, and mode checks."""

    if not REVISION_PATTERN.fullmatch(revision):
        raise RuntimeError("revision must be a full lowercase Git SHA")
    if release.is_symlink() or not release.is_dir():
        raise RuntimeError("runtime release is not a real directory")
    if (release.stat().st_mode & 0o777) != 0o500:
        raise RuntimeError("runtime release directory must have mode 0500")

    revision_path = release / "REVISION"
    if revision_path.is_symlink() or not revision_path.is_file():
        raise RuntimeError("runtime revision marker is unavailable")
    if revision_path.read_text(encoding="utf-8").strip() != revision:
        raise RuntimeError("runtime revision marker does not match")

    manifest_path = release / "MANIFEST.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("runtime checksum manifest is unavailable")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("runtime checksum manifest is invalid") from exc
    expected = _manifest_payload(release, revision)
    if manifest != expected:
        raise RuntimeError("runtime checksum manifest does not match")

    for path in release.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"runtime symlink is forbidden: {path}")
        relative = PurePosixPath(path.relative_to(release).as_posix())
        expected_mode = _expected_mode(relative, is_directory=path.is_dir())
        actual_mode = path.stat().st_mode & 0o777
        if actual_mode != expected_mode:
            raise RuntimeError(
                f"unsafe runtime mode for {relative}: {actual_mode:04o} != {expected_mode:04o}"
            )
    return manifest


def build_release(
    source: Path,
    releases_root: Path,
    requested_revision: str,
) -> tuple[Path, str, dict[str, Any]]:
    """Build one release atomically, or re-verify an existing identical release."""

    source = source.resolve(strict=True)
    if not (source / ".git").exists():
        raise RuntimeError(f"source is not a Git worktree: {source}")
    revision = _resolved_commit(source, requested_revision)

    releases_root = releases_root.expanduser()
    if not releases_root.is_absolute():
        raise RuntimeError("runtime releases root must be an absolute path")
    if releases_root.exists() and (releases_root.is_symlink() or not releases_root.is_dir()):
        raise RuntimeError("runtime releases root is not a real directory")
    releases_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    releases_root.chmod(0o700)
    destination = releases_root / revision
    if destination.exists():
        return destination, revision, verify_release(destination, revision)

    temporary = Path(tempfile.mkdtemp(prefix=f".{revision[:12]}.", dir=releases_root))
    temporary.chmod(0o700)
    try:
        archive_path = temporary / "runtime.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                f"--output={archive_path}",
                revision,
                "--",
                "openhealth",
                *(path.as_posix() for path in REQUIRED_FILES),
            ],
            check=True,
        )
        staging = temporary / "release"
        staging.mkdir(mode=0o700)
        with tarfile.open(archive_path, "r") as archive:
            members = _validate_archive_members(archive)
            _extract_validated_members(archive, members, staging)

        (staging / "REVISION").write_text(f"{revision}\n", encoding="utf-8")
        manifest = _manifest_payload(staging, revision)
        manifest_path = staging / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _apply_owner_only_modes(staging)
        verify_release(staging, revision)
        # macOS requires write permission on a directory being renamed. Keep
        # the private staging tree otherwise sealed, reopen only its root for
        # the atomic publication, then seal the destination immediately.
        staging.chmod(0o700)
        os.replace(staging, destination)
        destination.chmod(0o500)
        manifest = verify_release(destination, revision)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return destination, revision, manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="build or re-verify one committed runtime")
    build.add_argument("--source", required=True, type=Path)
    build.add_argument("--releases-root", required=True, type=Path)
    build.add_argument("--revision", required=True)

    verify = commands.add_parser("verify", help="repeat verification of one runtime")
    verify.add_argument("--release", required=True, type=Path)
    verify.add_argument("--revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        destination, revision, manifest = build_release(
            args.source,
            args.releases_root,
            args.revision,
        )
    else:
        destination = args.release.expanduser()
        revision = args.revision
        manifest = verify_release(destination, revision)
    print(
        json.dumps(
            {
                "destination": str(destination),
                "file_count": len(manifest["files"]),
                "revision": revision,
                "verified": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
