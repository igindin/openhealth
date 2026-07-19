import unittest

from openhealth import privacy


class PrivacyTests(unittest.TestCase):
    def test_pseudonymize_stable_and_irreversible(self):
        a = privacy.pseudonymize("user-1", "salt")
        b = privacy.pseudonymize("user-1", "salt")
        c = privacy.pseudonymize("user-1", "other-salt")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("p_"))
        self.assertNotIn("user-1", a)

    def test_strip_removes_pii_and_blanks_free_text(self):
        rec = {
            "id": "x", "record_type": "ContextNote", "value": 7,
            "location": "Budapest", "people": ["Marina"], "author": "ilya",
            "captured_at": "2024-06-01T08:30:00Z",
            "metadata": {"body": "secret notes about a name", "source_kind": "messages"},
        }
        out = privacy.strip_pii(rec)
        self.assertNotIn("location", out)
        self.assertNotIn("people", out)
        self.assertNotIn("author", out)
        self.assertNotIn("body", out["metadata"])
        self.assertEqual(out["metadata"]["source_kind"], "messages")
        self.assertEqual(out["captured_at"], "2024-06-01")  # coarsened to date
        self.assertEqual(out["value"], 7)

    def test_validator_catches_email_and_fields(self):
        ok, reasons = privacy.is_anonymized({"summary": "reach me at a@b.com"})
        self.assertFalse(ok)
        self.assertTrue(any("email" in r for r in reasons))
        ok2, reasons2 = privacy.is_anonymized({"location": "Berlin"})
        self.assertFalse(ok2)

    def test_anonymize_for_share_passes_validation(self):
        rec = {
            "id": "obs-1", "source_id": "messages-ilya", "owner": "ilya",
            "value": 64, "metadata": {"body": "free text", "flag": "normal"},
            "location": "Budapest",
        }
        shared = privacy.anonymize_for_share(rec, salt="test-salt")
        ok, reasons = privacy.is_anonymized(shared)
        self.assertTrue(ok, reasons)
        self.assertTrue(shared["source_id"].startswith("p_"))
        self.assertTrue(shared["owner"].startswith("p_"))


if __name__ == "__main__":
    unittest.main()
