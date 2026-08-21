"""WHOOP sync must never purge its window without windowed evidence.

The idempotency purge (delete in-window records the API did not re-emit) is
only safe after the windowed datasets (cycles/recoveries/sleeps/workouts)
actually return data. A fetch where they all come back empty — an API
brownout returning 200-with-empty-lists — must leave the database
untouched, or the purge deletes the whole sync window. The evidence is
per-dataset: cycles answering says nothing about sleeps, and the profile /
body-measurement datasets answer independently of the window entirely, so
neither may put another dataset's records on the purge list. All data is
synthetic.

Run directly:  PYTHONPATH=$PWD python3 tests/test_whoop_sync_purge.py
"""

import tempfile
import unittest
from pathlib import Path

from openhealth import index
from openhealth.storage import ensure_repo_structure
from openhealth.whoop import WHOOP_SOURCE_ID, sync_whoop


class _EmptyWindowClient:
    """WhoopClient stand-in: every windowed dataset comes back empty."""

    def list_cycles(self, start, end):
        return []

    list_recoveries = list_sleeps = list_workouts = list_cycles

    def get_profile(self):
        return {"user_id": 1001, "first_name": "Test"}

    def get_body_measurements(self):
        return {"height_meter": 1.8, "weight_kilogram": 75.0, "max_heart_rate": 190}


class _OneCycleClient(_EmptyWindowClient):
    """Same, but cycles returns one synthetic record."""

    def list_cycles(self, start, end):
        return [{"records": [{"id": "c-new", "start": "2026-07-27T08:00:00Z", "score": {"strain": 9.5}}]}]


def _seed_record(db_path, record_id, event_kind):
    record = {
        "id": record_id,
        "record_type": "TimelineEvent",
        "source_id": WHOOP_SOURCE_ID,
        "title": "Existing record",
        "summary": "Record already in the window",
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.95,
        "date": "2026-07-27",
        "event_kind": event_kind,
    }
    index.upsert_record(db_path, record)
    return record


def _seed_cycle(db_path, record_id="whoop-cycle-existing"):
    return _seed_record(db_path, record_id, "whoop_cycle")


class EmptyFetchPurgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = ensure_repo_structure(self.root)
        index.init_db(self.paths.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _remaining_ids(self):
        return {
            record["id"]
            for record in index.list_records_by_source(self.paths.db_path, WHOOP_SOURCE_ID)
        }

    def test_empty_fetch_never_purges_existing_records(self):
        existing = _seed_cycle(self.paths.db_path)

        summary = sync_whoop(
            self.root,
            start="2026-07-26T00:00:00Z",
            end="2026-07-28T23:59:59Z",
            include_profile=False,
            include_body_measurements=False,
            client=_EmptyWindowClient(),
        )

        self.assertEqual(summary["records_imported"], 0)
        self.assertEqual(summary["replaced_record_ids"], 0)
        self.assertIn(existing["id"], self._remaining_ids())

    def test_profile_and_body_records_are_not_windowed_evidence(self):
        # The dangerous shape: windowed datasets empty, but profile and body
        # measurements still answer, so `records` as a whole is NOT empty.
        # A guard on total record count would purge here and wipe the window.
        existing = _seed_cycle(self.paths.db_path)

        summary = sync_whoop(
            self.root,
            start="2026-07-26T00:00:00Z",
            end="2026-07-28T23:59:59Z",
            include_profile=True,
            include_body_measurements=True,
            client=_EmptyWindowClient(),
        )

        self.assertGreater(summary["records_imported"], 0)
        # replaced_record_ids counts the delete-then-reinsert of the incoming
        # profile/body records themselves; the point is that nothing BEYOND
        # them was touched — the existing windowed record survives.
        self.assertEqual(summary["replaced_record_ids"], summary["records_imported"])
        self.assertIn(existing["id"], self._remaining_ids())

    def test_windowed_data_still_purges_stale_records(self):
        # The guard must not disable the cleanup: once a windowed dataset
        # returns data, a stale in-window record that was not re-emitted goes.
        stale = _seed_cycle(self.paths.db_path, record_id="whoop-cycle-stale")

        summary = sync_whoop(
            self.root,
            start="2026-07-26T00:00:00Z",
            end="2026-07-28T23:59:59Z",
            include_profile=False,
            include_body_measurements=False,
            client=_OneCycleClient(),
        )

        self.assertGreater(summary["records_imported"], 0)
        self.assertGreater(summary["replaced_record_ids"], 0)
        self.assertNotIn(stale["id"], self._remaining_ids())

    def test_partial_brownout_protects_sibling_datasets(self):
        # Evidence is per-dataset: cycles answering with data says nothing
        # about sleeps. If sleeps comes back 200-with-empty-list, its existing
        # in-window records must survive — while the cycles dataset (which DID
        # return data) still gets its stale records cleaned up.
        stale_cycle = _seed_cycle(self.paths.db_path, record_id="whoop-cycle-stale")
        existing_sleep = _seed_record(
            self.paths.db_path, "whoop-sleep-existing", "whoop_sleep"
        )

        summary = sync_whoop(
            self.root,
            start="2026-07-26T00:00:00Z",
            end="2026-07-28T23:59:59Z",
            include_profile=False,
            include_body_measurements=False,
            client=_OneCycleClient(),
        )

        self.assertGreater(summary["records_imported"], 0)
        remaining = self._remaining_ids()
        self.assertIn(
            existing_sleep["id"], remaining,
            "an empty sleeps fetch must not purge existing sleep records",
        )
        self.assertNotIn(
            stale_cycle["id"], remaining,
            "cycles returned data, so its stale records must still be cleaned up",
        )


if __name__ == "__main__":
    unittest.main()
