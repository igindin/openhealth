import json
import tempfile
import unittest
from pathlib import Path

from openhealth import index, journal_store
from openhealth.modules import correlations


def _day_payload(alcohol=True, notes=""):
    return {
        "habits": {"lifestyle.alcohol": alcohol, "mood_energy": 4},
        "mood": {"quadrant": "calm", "word": "steady", "energy": 4},
        "survey": {"energy": 4, "stress": 2, "sleep_quality": 5, "pain": 1, "mood": 4, "ts": "2026-06-01T10:00:00Z"},
        "notes": notes,
    }


def _recovery_record(day, score):
    return {
        "id": "obs-recovery-%s" % day,
        "record_type": "Observation",
        "source_id": "whoop",
        "title": "Recovery %s" % day,
        "summary": "recovery %s" % score,
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.9,
        "date": day,
        "tags": ["recovery"],
        "metadata": {},
        "observation_kind": "recovery_score",
        "metric_name": "recovery_score",
        "value": score,
        "unit": "%",
    }


class SaveLoadDayTests(unittest.TestCase):
    def test_roundtrip_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as home:
            path = journal_store.save_day("2026-06-01", _day_payload(notes="ok"), home=home)
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "2026-06-01.json")
            # No temp leftovers from the atomic write.
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            loaded = journal_store.load_day("2026-06-01", home=home)
            self.assertEqual(loaded["habits"]["lifestyle.alcohol"], True)
            self.assertEqual(loaded["mood"]["energy"], 4)
            self.assertEqual(loaded["notes"], "ok")
            self.assertEqual(loaded["date"], "2026-06-01")

    def test_private_modes(self):
        with tempfile.TemporaryDirectory() as home:
            path = journal_store.save_day("2026-06-01", _day_payload(), home=home)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_rejects_bad_date_and_bad_payload(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(ValueError):
                journal_store.save_day("01.06.2026", _day_payload(), home=home)
            with self.assertRaises(ValueError):
                journal_store.save_day("2026-06-01", ["not", "a", "dict"], home=home)
            with self.assertRaises(ValueError):
                journal_store.save_day("2026-06-01", {"habits": ["wrong"]}, home=home)

    def test_load_missing_and_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(journal_store.load_day("2026-06-01", home=home))
            path = journal_store.day_path("2026-06-02", home=home)
            path.parent.mkdir(parents=True)
            path.write_text("{broken json", encoding="utf-8")
            self.assertIsNone(journal_store.load_day("2026-06-02", home=home))

    def test_load_range_inclusive_with_gaps(self):
        with tempfile.TemporaryDirectory() as home:
            journal_store.save_day("2026-06-01", _day_payload(True), home=home)
            journal_store.save_day("2026-06-03", _day_payload(False), home=home)
            days = journal_store.load_range("2026-06-01", "2026-06-03", home=home)
            self.assertEqual(sorted(days), ["2026-06-01", "2026-06-03"])
            with self.assertRaises(ValueError):
                journal_store.load_range("2026-06-03", "2026-06-01", home=home)


class ToObservationsTests(unittest.TestCase):
    def test_boolean_habit_matches_correlations_contract(self):
        recs = journal_store.to_observations(_day_payload(), date="2026-06-01")
        habit = next(r for r in recs if r["metric_name"] == "lifestyle.alcohol")
        # The exact fields modules.correlations.from_index reads:
        self.assertEqual(habit["record_type"], "Observation")
        self.assertEqual(habit["observation_kind"], "journal_entry")
        self.assertIsInstance(habit["value"], bool)
        self.assertEqual(habit["date"], "2026-06-01")
        self.assertEqual(habit["metadata"]["behavior_id"], "lifestyle.alcohol")
        self.assertEqual(habit["metadata"]["category"], "lifestyle")

    def test_custom_habit_numeric_mood_survey_and_note(self):
        recs = journal_store.to_observations(
            {
                "habits": {"my_custom_thing": True, "mood_energy": 3},
                "mood": {"quadrant": "tense", "word": "tense-tight", "energy": 2},
                "survey": {"stress": 5},
                "notes": "hard day",
            },
            date="2026-06-02",
        )
        by_metric = {r.get("metric_name"): r for r in recs if r["record_type"] == "Observation"}
        self.assertEqual(by_metric["my_custom_thing"]["metadata"]["category"], "custom")
        self.assertEqual(by_metric["mood_energy"]["value"], 2.0)  # mood section, not the habit slider
        self.assertEqual(by_metric["mood_energy"]["metadata"]["quadrant"], "tense")
        self.assertEqual(by_metric["mood_energy"]["metadata"]["word"], "tense-tight")
        self.assertEqual(by_metric["survey_stress"]["value"], 5.0)
        notes = [r for r in recs if r["record_type"] == "ContextNote"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["summary"], "hard day")
        self.assertEqual(notes[0]["note_kind"], "journal_note")

    def test_date_from_saved_payload(self):
        with tempfile.TemporaryDirectory() as home:
            journal_store.save_day("2026-06-05", _day_payload(), home=home)
            loaded = journal_store.load_day("2026-06-05", home=home)
            recs = journal_store.to_observations(loaded)  # date embedded by save_day
            self.assertTrue(all(r["date"] == "2026-06-05" for r in recs))

    def test_requires_a_date(self):
        with self.assertRaises(ValueError):
            journal_store.to_observations({"habits": {"x": True}})


class CorrelationsPickupTests(unittest.TestCase):
    def test_persist_day_feeds_correlations_from_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            db = Path(tmp) / "index.db"
            index.init_db(db)
            # 10 days: alcohol yes -> low recovery, no -> high (5/5 threshold).
            for i in range(1, 11):
                day = "2026-06-%02d" % i
                yes = i % 2 == 1
                journal_store.persist_day(day, {"habits": {"lifestyle.alcohol": yes}}, db, home=home)
                index.upsert_record(db, _recovery_record(day, 40 if yes else 80))

            behaviors = correlations.from_index(db, as_of="2026-06-10")
            alcohol = next(b for b in behaviors if b["behavior_id"] == "lifestyle.alcohol")
            self.assertEqual(len(alcohol["pairs"]), 10)
            insights = correlations.analyze(behaviors)
            self.assertEqual(len(insights), 1)
            self.assertEqual(insights[0]["metadata"]["behavior_id"], "lifestyle.alcohol")
            self.assertEqual(insights[0]["metadata"]["direction"], "negative")
            # Mirror files exist on disk too.
            self.assertEqual(len(journal_store.load_range("2026-06-01", "2026-06-10", home=home)), 10)


class WeekFocusTests(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as home:
            journal_store.save_week_focus(["sleep by 23:00", "no alcohol", "a walk"], home=home,
                                          week_start="2026-06-08")
            focus = journal_store.load_week_focus(home=home)
            self.assertEqual(len(focus["items"]), 3)
            self.assertEqual(focus["week_start"], "2026-06-08")

    def test_limits_and_default(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(journal_store.load_week_focus(home=home), {"items": [], "week_start": None})
            with self.assertRaises(ValueError):
                journal_store.save_week_focus(["a", "b", "c", "d"], home=home)
            # Blank entries are dropped, not stored.
            journal_store.save_week_focus(["  ", "sleep"], home=home)
            self.assertEqual(journal_store.load_week_focus(home=home)["items"], ["sleep"])


class ExportAllTests(unittest.TestCase):
    def test_export_contains_days_and_focus(self):
        with tempfile.TemporaryDirectory() as home:
            journal_store.save_day("2026-06-01", _day_payload(True), home=home)
            journal_store.save_day("2026-06-02", _day_payload(False), home=home)
            journal_store.save_week_focus(["sleep"], home=home)
            # A foreign file in the mirror dir must not leak into the backup.
            (journal_store.journal_home(home) / "days" / "not-a-date.json").write_text("{}", encoding="utf-8")
            dump = journal_store.export_all(home=home)
            self.assertEqual(dump["version"], 1)
            self.assertEqual(sorted(dump["days"]), ["2026-06-01", "2026-06-02"])
            self.assertEqual(dump["focus"]["items"], ["sleep"])
            json.dumps(dump)  # the whole backup must be JSON-serializable

    def test_export_empty_home(self):
        with tempfile.TemporaryDirectory() as home:
            dump = journal_store.export_all(home=home)
            self.assertEqual(dump["days"], {})
            self.assertEqual(dump["focus"]["items"], [])


if __name__ == "__main__":
    unittest.main()
