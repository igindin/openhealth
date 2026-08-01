import gzip
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from openhealth.runtime_reliability import (
    ExponentialBackoff,
    RateLimitedEvent,
    rotate_log_file,
    utc_log_timestamp,
)


class ExponentialBackoffTests(unittest.TestCase):
    def test_grows_caps_and_resets(self):
        backoff = ExponentialBackoff(base_seconds=1, cap_seconds=4)

        self.assertEqual([backoff.failure() for _ in range(5)], [1, 2, 4, 4, 4])
        backoff.reset()
        self.assertEqual(backoff.failure(), 1)

    def test_attempt_counter_does_not_grow_forever_at_cap(self):
        backoff = ExponentialBackoff(base_seconds=1, cap_seconds=60)

        for _ in range(10000):
            self.assertLessEqual(backoff.failure(), 60)
        self.assertLessEqual(backoff.attempts, 6)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            ExponentialBackoff(base_seconds=0)
        with self.assertRaises(ValueError):
            ExponentialBackoff(base_seconds=2, cap_seconds=1)
        with self.assertRaises(ValueError):
            ExponentialBackoff(base_seconds=float("nan"))
        with self.assertRaises(ValueError):
            ExponentialBackoff(cap_seconds=float("inf"))


class RateLimitedEventTests(unittest.TestCase):
    def test_first_event_and_periodic_summary(self):
        limiter = RateLimitedEvent(interval_seconds=10)

        self.assertEqual(limiter.record(now=100), 0)
        self.assertIsNone(limiter.record(now=101))
        self.assertIsNone(limiter.record(now=102))
        self.assertEqual(limiter.suppressed, 2)
        self.assertEqual(limiter.record(now=110), 2)
        self.assertEqual(limiter.suppressed, 0)

    def test_reset_makes_next_event_visible(self):
        limiter = RateLimitedEvent(interval_seconds=10)
        limiter.record(now=100)
        limiter.record(now=101)
        limiter.reset()
        self.assertEqual(limiter.record(now=102), 0)

    def test_drain_reports_suppressed_burst_and_resets(self):
        limiter = RateLimitedEvent(interval_seconds=10)
        limiter.record(now=100)
        limiter.record(now=101)
        limiter.record(now=102)
        self.assertEqual(limiter.drain(), 2)
        self.assertEqual(limiter.record(now=103), 0)


class LogRotationTests(unittest.TestCase):
    def test_rotates_compresses_reopens_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "bot.log"
            log.write_bytes(b"old log\n" * 100)
            os.chmod(log, 0o644)
            callbacks = []
            first_now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)

            archive = rotate_log_file(
                log,
                max_bytes=100,
                archive_count=2,
                now=first_now,
                reopen_streams=lambda path: callbacks.append(path),
            )

            self.assertEqual(callbacks, [log])
            self.assertTrue(log.exists())
            self.assertEqual(log.read_bytes(), b"")
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertTrue(archive.name.endswith(".log.gz"))
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            with gzip.open(archive, "rb") as compressed:
                self.assertEqual(compressed.read(), b"old log\n" * 100)

            for minute in (1, 2):
                log.write_bytes(("new %d\n" % minute).encode() * 100)
                rotate_log_file(
                    log,
                    max_bytes=100,
                    archive_count=2,
                    now=datetime(2026, 7, 27, 10, minute, tzinfo=timezone.utc),
                )
            self.assertEqual(len(list(Path(tmp).glob("bot.*.log.gz"))), 2)

    def test_below_threshold_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "bot.log"
            log.write_text("small", encoding="utf-8")
            self.assertIsNone(rotate_log_file(log, max_bytes=100))
            self.assertEqual(log.read_text(encoding="utf-8"), "small")
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_failed_stream_reopen_restores_original_active_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "bot.log"
            original = b"important log\n" * 20
            log.write_bytes(original)

            def fail_reopen(path):
                raise OSError("synthetic reopen failure")

            with self.assertRaises(OSError):
                rotate_log_file(
                    log,
                    max_bytes=10,
                    reopen_streams=fail_reopen,
                )

            self.assertEqual(log.read_bytes(), original)
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).glob("bot.*.log*")), [])

    def test_compression_failure_keeps_raw_archive_and_old_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "bot.log"
            current = b"current important log\n" * 20
            log.write_bytes(current)
            old = root / "bot.20260726T100000Z.log.gz"
            old.write_bytes(b"old archive")

            with mock.patch(
                "openhealth.runtime_reliability.shutil.copyfileobj",
                side_effect=OSError("synthetic disk full"),
            ), self.assertRaises(OSError):
                rotate_log_file(
                    log,
                    max_bytes=10,
                    archive_count=1,
                    now=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
                )

            raw_archives = [
                path
                for path in root.glob("bot.*.log")
                if path.name != "bot.log"
            ]
            self.assertEqual(len(raw_archives), 1)
            self.assertEqual(raw_archives[0].read_bytes(), current)
            self.assertEqual(old.read_bytes(), b"old archive")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_utc_timestamp_is_explicit(self):
        stamp = utc_log_timestamp(datetime(2026, 7, 27, 10, 5, 3))
        self.assertEqual(stamp, "2026-07-27T10:05:03Z")


if __name__ == "__main__":
    unittest.main()
