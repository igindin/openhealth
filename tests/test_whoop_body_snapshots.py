import tempfile
import unittest
from pathlib import Path

from openhealth import index
from openhealth.storage import ensure_repo_structure
from openhealth.whoop import (
    WHOOP_SOURCE_ID,
    normalize_body_measurements,
    purge_existing_whoop_records,
    sync_whoop_body_measurements,
)


class FakeBodyMeasurementClient:
    def __init__(self, weight=75.6):
        self.weight = weight

    def get_body_measurements(self):
        return {
            "height_meter": 1.75,
            "weight_kilogram": self.weight,
            "max_heart_rate": 190,
        }


class WhoopBodySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.paths = ensure_repo_structure(self.root)
        index.init_db(self.paths.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_daily_sync_retains_days_and_is_idempotent_within_a_day(self):
        client = FakeBodyMeasurementClient()
        first = sync_whoop_body_measurements(
            self.root,
            client=client,
            fetched_at="2026-07-27T07:00:00+00:00",
        )
        client.weight = 75.4
        sync_whoop_body_measurements(
            self.root,
            client=client,
            fetched_at="2026-07-27T10:00:00+00:00",
        )
        client.weight = 75.1
        second_day = sync_whoop_body_measurements(
            self.root,
            client=client,
            fetched_at="2026-07-28T07:00:00+00:00",
        )

        records = index.list_records_by_source(self.paths.db_path, WHOOP_SOURCE_ID)
        body_records = [record for record in records if record.get("observation_kind") == "whoop_body_measurement"]
        weight_records = sorted(
            (record for record in body_records if record.get("metric_name") == "weight_kilogram"),
            key=lambda record: record["date"],
        )

        self.assertEqual(first["records_imported"], 3)
        self.assertEqual(second_day["records_imported"], 3)
        self.assertEqual(len(body_records), 6)
        self.assertEqual([record["value"] for record in weight_records], [75.4, 75.1])
        self.assertEqual(
            [record["id"] for record in weight_records],
            [
                "whoop-body-weight-kilogram-2026-07-27",
                "whoop-body-weight-kilogram-2026-07-28",
            ],
        )
        self.assertEqual(weight_records[0]["captured_at"], "2026-07-27T10:00:00+00:00")
        self.assertEqual(
            weight_records[0]["metadata"]["openhealth_snapshot"]["date_basis"],
            "fetch_timestamp",
        )
        self.assertTrue(Path(second_day["archived_path"]).exists())

    def test_provider_timestamp_is_preserved_but_capture_time_is_local_fetch(self):
        records = normalize_body_measurements(
            {
                "weight_kilogram": 74.9,
                "updated_at": "2026-07-25T06:45:00Z",
            },
            artifact_id="artifact-1",
            source_id=WHOOP_SOURCE_ID,
            fetched_at="2026-07-28T08:00:00+00:00",
        )

        self.assertEqual(records[0]["id"], "whoop-body-weight-kilogram-2026-07-25")
        self.assertEqual(records[0]["date"], "2026-07-25")
        self.assertEqual(records[0]["captured_at"], "2026-07-28T08:00:00+00:00")
        self.assertEqual(
            records[0]["metadata"]["openhealth_snapshot"]["provider_measurement_timestamp"],
            "2026-07-25T06:45:00Z",
        )
        self.assertEqual(
            records[0]["metadata"]["openhealth_snapshot"]["date_basis"],
            "provider_timestamp",
        )

    def test_full_sync_purge_preserves_older_body_snapshots(self):
        old_body = {
            "id": "whoop-body-weight-kilogram-2026-07-27",
            "record_type": "Observation",
            "source_id": WHOOP_SOURCE_ID,
            "title": "Weight",
            "summary": "Historical daily snapshot",
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 0.95,
            "date": "2026-07-27",
            "observation_kind": "whoop_body_measurement",
            "metric_name": "weight_kilogram",
            "value": 75.4,
            "unit": "kg",
        }
        old_cycle = {
            "id": "whoop-cycle-old",
            "record_type": "TimelineEvent",
            "source_id": WHOOP_SOURCE_ID,
            "title": "Cycle",
            "summary": "Old cycle in refresh window",
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 0.95,
            "date": "2026-07-27",
            "event_kind": "whoop_cycle",
        }
        index.upsert_record(self.paths.db_path, old_body)
        index.upsert_record(self.paths.db_path, old_cycle)

        purge_existing_whoop_records(
            self.paths.db_path,
            new_records=[],
            start="2026-07-26T00:00:00Z",
            end="2026-07-28T23:59:59Z",
        )

        remaining_ids = {
            record["id"] for record in index.list_records_by_source(self.paths.db_path, WHOOP_SOURCE_ID)
        }
        self.assertIn(old_body["id"], remaining_ids)
        self.assertNotIn(old_cycle["id"], remaining_ids)


if __name__ == "__main__":
    unittest.main()
