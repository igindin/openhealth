import tempfile
import unittest
from pathlib import Path

from openhealth import index, modules
from openhealth.modules import vaccination
from openhealth.modules.base import KNOWN_DOMAINS


class RegistrationTests(unittest.TestCase):
    def test_domain_known_and_module_registered(self):
        self.assertIn("vaccination", KNOWN_DOMAINS)
        module = modules.get_module("vaccination")
        self.assertEqual(module.domain, "vaccination")
        self.assertIn("items", module.schema()["properties"])


class NormalizeTests(unittest.TestCase):
    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            vaccination.normalize_item({"name": "  "})
        with self.assertRaises(ValueError):
            vaccination.normalize_item({"name": "Influenza", "date": "06.10.2025"})
        with self.assertRaises(ValueError):
            vaccination.normalize_item({"name": "Influenza", "next_due": "späterm"})
        with self.assertRaises(ValueError):
            vaccination.normalize_item({"name": "Influenza", "dose_number": 0})

    def test_undated_record_stays_undated(self):
        item = vaccination.normalize_item({"name": "Measles (childhood)"})
        self.assertIsNone(item["date"])  # no invented dates


class SlugTests(unittest.TestCase):
    def test_cyrillic_name_keeps_letters_in_slug(self):
        # The escaped fixtures are Russian for "influenza" and "measles
        # (childhood)". storage.slugify would drop the Cyrillic letters
        # entirely; vaccination._slug must keep them so Russian-named records
        # still get distinct, readable ids.
        self.assertEqual(vaccination._slug("\u0413\u0440\u0438\u043f\u043f"), "\u0433\u0440\u0438\u043f\u043f")
        self.assertEqual(vaccination._slug("\u041a\u043e\u0440\u044c (\u0434\u0435\u0442\u0441\u0442\u0432\u043e)"), "\u043a\u043e\u0440\u044c-\u0434\u0435\u0442\u0441\u0442\u0432\u043e")

    def test_slug_falls_back_for_unslugabble_name(self):
        self.assertEqual(vaccination._slug("+++"), "item")


class ComputeTests(unittest.TestCase):
    def _compute(self, items, today="2026-06-10"):
        return modules.get_module("vaccination").compute({"items": items, "today": today})

    def test_ledger_observations(self):
        result = self._compute([
            {"name": "Influenza (seasonal)", "date": "2025-10-06", "dose_number": 1, "next_due": "2026-10-01"},
            {"name": "Tick-borne encephalitis", "date": "2024-05-01", "dose_number": 2},
        ])
        self.assertEqual(len(result.metrics), 2)
        obs = result.metrics[0]
        self.assertEqual(obs["record_type"], "Observation")
        self.assertEqual(obs["observation_kind"], "vaccination")
        self.assertEqual(obs["date"], "2025-10-06")
        self.assertEqual(obs["metadata"]["next_due"], "2026-10-01")
        self.assertEqual(obs["value"], 1)

    def test_overdue_next_due_raises_attention_flag(self):
        result = self._compute([
            {"name": "Td (tetanus/diphtheria)", "date": "2015-03-01", "next_due": "2025-03-01"},
        ])
        flags = [i for i in result.insights if i["record_type"] == "InsightHypothesis"]
        self.assertEqual(len(flags), 1)
        flag = flags[0]
        self.assertIn("attention", flag["tags"])
        self.assertIn("see-clinician", flag["tags"])
        self.assertEqual(flag["metadata"]["next_due"], "2025-03-01")
        # C3: framed as a question/prompt, never an instruction.
        self.assertTrue(flag["summary"].startswith("[C3"))
        self.assertIn("worth discussing with a clinician", flag["statement"])

    def test_future_next_due_is_upcoming_not_flagged(self):
        result = self._compute([
            {"name": "Influenza", "date": "2025-10-06", "next_due": "2026-10-01"},
        ])
        flags = [i for i in result.insights if i["record_type"] == "InsightHypothesis"]
        self.assertEqual(flags, [])
        snapshot = next(i for i in result.insights if i.get("note_kind") == "vaccination_snapshot")
        self.assertEqual(snapshot["metadata"]["upcoming"], [{"name": "Influenza", "next_due": "2026-10-01"}])
        self.assertEqual(snapshot["metadata"]["overdue"], [])

    def test_snapshot_counts_and_notes(self):
        result = self._compute([
            {"name": "Influenza", "date": "2024-10-01", "next_due": "2025-10-01"},
            {"name": "Measles"},
        ])
        snapshot = next(i for i in result.insights if i.get("note_kind") == "vaccination_snapshot")
        self.assertEqual(snapshot["metadata"]["total"], 2)
        self.assertEqual(len(snapshot["metadata"]["overdue"]), 1)
        self.assertIn("1 overdue", result.notes[0])

    def test_empty_ledger_rejected(self):
        with self.assertRaises(ValueError):
            self._compute([])


class PersistTests(unittest.TestCase):
    def test_persist_writes_to_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.db"
            index.init_db(db)
            result = modules.get_module("vaccination").compute({
                "items": [{"name": "Influenza", "date": "2024-10-01", "next_due": "2025-10-01"}],
                "today": "2026-06-10",
            })
            written = vaccination.persist(result, db)
            self.assertEqual(written, len(result.metrics) + len(result.insights))
            stored = index.list_records_by_source(db, "vaccination")
            self.assertEqual(len(stored), written)


if __name__ == "__main__":
    unittest.main()
