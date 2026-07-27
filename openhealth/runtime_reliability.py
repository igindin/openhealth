"""Small stdlib-only reliability primitives for long-running local adapters."""

from __future__ import annotations

import gzip
import math
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


class ExponentialBackoff:
    """Stateful ``base * 2**attempt`` delays with a cap and explicit reset."""

    def __init__(self, base_seconds: float = 1.0, cap_seconds: float = 60.0):
        self.base_seconds = float(base_seconds)
        self.cap_seconds = float(cap_seconds)
        if not math.isfinite(self.base_seconds) or self.base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if (
            not math.isfinite(self.cap_seconds)
            or self.cap_seconds < self.base_seconds
        ):
            raise ValueError("cap_seconds must be >= base_seconds")
        self.attempts = 0
        self._cap_attempt = int(
            math.ceil(math.log2(self.cap_seconds / self.base_seconds))
        )

    def failure(self) -> float:
        exponent = min(self.attempts, self._cap_attempt)
        delay = min(self.base_seconds * (2 ** exponent), self.cap_seconds)
        self.attempts = min(self.attempts + 1, self._cap_attempt)
        return delay

    def reset(self) -> None:
        self.attempts = 0


class RateLimitedEvent:
    """Emit the first event, suppress a burst, then report its suppressed count."""

    def __init__(
        self,
        interval_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.interval_seconds = float(interval_seconds)
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._clock = clock
        self._last_emitted: Optional[float] = None
        self._suppressed = 0

    def record(self, now: Optional[float] = None) -> Optional[int]:
        """Return suppressed count when the event should be logged, else ``None``."""
        moment = self._clock() if now is None else float(now)
        if self._last_emitted is None or moment - self._last_emitted >= self.interval_seconds:
            suppressed = self._suppressed
            self._suppressed = 0
            self._last_emitted = moment
            return suppressed
        self._suppressed += 1
        return None

    @property
    def suppressed(self) -> int:
        return self._suppressed

    def reset(self) -> None:
        self._last_emitted = None
        self._suppressed = 0

    def drain(self) -> int:
        """Return the pending suppressed count and reset the event window."""
        suppressed = self._suppressed
        self.reset()
        return suppressed


def utc_log_timestamp(now: Optional[datetime] = None) -> str:
    """Second-resolution UTC timestamp suitable for local adapter logs."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_archive_path(path: Path, now: Optional[datetime]) -> Path:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name("%s.%s.log" % (path.stem, stamp))
    suffix = 1
    while candidate.exists() or candidate.with_suffix(candidate.suffix + ".gz").exists():
        candidate = path.with_name("%s.%s-%d.log" % (path.stem, stamp, suffix))
        suffix += 1
    return candidate


def _prune_archives(path: Path, archive_count: int) -> None:
    pattern = "%s.*.log*" % path.stem
    archives = sorted(
        (
            item
            for item in path.parent.glob(pattern)
            if item != path and item.is_file()
        ),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )
    for old in archives[archive_count:]:
        old.unlink()


def rotate_log_file(
    path: Path,
    max_bytes: int,
    archive_count: int = 3,
    *,
    now: Optional[datetime] = None,
    reopen_streams: Optional[Callable[[Path], None]] = None,
) -> Optional[Path]:
    """Rotate, gzip and retain a bounded number of owner-only local logs.

    ``reopen_streams`` runs after the new active file exists and before the old
    file is compressed.  A daemon whose stdout/stderr point at ``path`` can use
    it to ``dup2`` those descriptors, avoiding writes into the renamed archive.
    """
    path = Path(path)
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if archive_count < 1:
        raise ValueError("archive_count must be at least 1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    try:
        size = path.stat().st_size
        path.chmod(0o600)
    except FileNotFoundError:
        return None
    if size <= max_bytes:
        return None

    archive = _next_archive_path(path, now)
    os.replace(path, archive)
    active_fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(active_fd)
    path.chmod(0o600)

    if reopen_streams is not None:
        try:
            reopen_streams(path)
        except Exception:
            # The daemon is still attached to the renamed inode. Restore its
            # original pathname so it keeps a reachable active log and a later
            # rotation can retry safely.
            path.unlink()
            os.replace(archive, path)
            path.chmod(0o600)
            raise

    compressed = archive.with_suffix(archive.suffix + ".gz")
    compressed_tmp = compressed.with_name(
        ".%s.%d.tmp" % (compressed.name, os.getpid())
    )
    try:
        with archive.open("rb") as source, gzip.open(
            compressed_tmp,
            "wb",
        ) as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
        compressed_tmp.chmod(0o600)
        tmp_fd = os.open(str(compressed_tmp), os.O_RDONLY)
        try:
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        os.replace(compressed_tmp, compressed)
        archive.unlink()
    except Exception:
        try:
            compressed_tmp.unlink()
        except FileNotFoundError:
            pass
        archive.chmod(0o600)
        raise

    _prune_archives(path, archive_count)
    return compressed
