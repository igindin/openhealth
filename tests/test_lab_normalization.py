import unittest

from openhealth import lab_normalization as ln
from openhealth import reference_ranges

# Unit spellings exactly as printed on Russian-language lab reports. These are
# parser INPUTS, not display text: the point of these tests is to prove that a
# Cyrillic unit off a real RU lab PDF still resolves to its canonical UCUM token.
# Written as escapes to keep this file ASCII.
CYR_MMOL_L = "\u043c\u043c\u043e\u043b\u044c/\u043b"  # Cyrillic "mmol/L"
CYR_MG_DL = "\u043c\u0433/\u0434\u043b"               # Cyrillic "mg/dL"
CYR_PMOL_L = "\u043f\u043c\u043e\u043b\u044c/\u043b"  # Cyrillic "pmol/L"


class CanonicalUnitTests(unittest.TestCase):
    def test_ascii_and_case_variants(self):
        self.assertEqual(ln.canonical_unit("mg/dL"), "mg/dL")
        self.assertEqual(ln.canonical_unit("MG/DL"), "mg/dL")
        self.assertEqual(ln.canonical_unit(" mmol/l "), "mmol/L")

    def test_cyrillic_variants(self):
        self.assertEqual(ln.canonical_unit(CYR_MMOL_L), "mmol/L")
        self.assertEqual(ln.canonical_unit(CYR_MG_DL), "mg/dL")
        self.assertEqual(ln.canonical_unit(CYR_PMOL_L), "pmol/L")

    def test_empty_and_unknown(self):
        self.assertIsNone(ln.canonical_unit(None))
        self.assertIsNone(ln.canonical_unit("   "))
        # Unknown unit is returned unchanged (caller decides), not dropped.
        self.assertEqual(ln.canonical_unit("furlongs"), "furlongs")


class ParseNumericTests(unittest.TestCase):
    def test_plain_numbers(self):
        self.assertEqual(ln.parse_numeric(13.5), (13.5, None))
        self.assertEqual(ln.parse_numeric(7), (7.0, None))
        self.assertEqual(ln.parse_numeric("5.55"), (5.55, None))

    def test_comma_decimal(self):
        self.assertEqual(ln.parse_numeric("13,5"), (13.5, None))

    def test_qualifiers(self):
        self.assertEqual(ln.parse_numeric("<0.01"), (0.01, "<"))
        self.assertEqual(ln.parse_numeric(">100"), (100.0, ">"))
        self.assertEqual(ln.parse_numeric("≤2,5"), (2.5, "<="))

    def test_thousands_space_separator(self):
        self.assertEqual(ln.parse_numeric("1 500"), (1500.0, None))

    def test_empty_and_garbage(self):
        self.assertEqual(ln.parse_numeric(None), (None, None))
        self.assertEqual(ln.parse_numeric(""), (None, None))
        self.assertEqual(ln.parse_numeric("pending"), (None, None))
        # bool must not be read as a numeric value
        self.assertEqual(ln.parse_numeric(True), (None, None))


class ToConventionalTests(unittest.TestCase):
    def test_si_to_conventional_glucose(self):
        spec = reference_ranges.MARKERS["glucose"]
        # 5.55 mmol/L / 0.0555 = 100 mg/dL
        value, converted = ln.to_conventional(spec, 5.55, "mmol/L")
        self.assertTrue(converted)
        self.assertAlmostEqual(value, 100.0, places=1)

    def test_conventional_passes_through(self):
        spec = reference_ranges.MARKERS["glucose"]
        value, converted = ln.to_conventional(spec, 100.0, "mg/dL")
        self.assertFalse(converted)
        self.assertEqual(value, 100.0)

    def test_no_unit_assumed_conventional(self):
        spec = reference_ranges.MARKERS["glucose"]
        value, converted = ln.to_conventional(spec, 99.0, None)
        self.assertFalse(converted)
        self.assertEqual(value, 99.0)


class NormalizeMarkerTests(unittest.TestCase):
    def test_si_glucose_becomes_conventional_and_flags_correctly(self):
        # A report in SI (mmol/L), as common outside the US.
        out = ln.normalize_marker("Glucose", "5,55", CYR_MMOL_L)
        self.assertEqual(out["marker_key"], "glucose")
        self.assertAlmostEqual(out["value"], 100.0, places=1)
        self.assertEqual(out["unit"], "mg/dL")
        self.assertTrue(out["unit_recognised"])
        self.assertIn("converted_from", out)

        # The canonical value must now assess the same as a native mg/dL value.
        assessment = reference_ranges.assess_marker(
            "Glucose", value=out["value"], unit=out["unit"]
        )
        self.assertEqual(assessment["flag"], "high")  # 100 > fallback 99

    def test_qualifier_preserved(self):
        out = ln.normalize_marker("CRP", "<0.3", "mg/L")
        self.assertEqual(out["qualifier"], "<")
        self.assertEqual(out["value"], 0.3)

    def test_unknown_marker_returns_none(self):
        self.assertIsNone(ln.normalize_marker("Unobtainium", 1.0, "mg/dL"))

    def test_unrecognised_unit_passes_value_through(self):
        out = ln.normalize_marker("Glucose", 100.0, "furlongs")
        self.assertFalse(out["unit_recognised"])
        # Value untouched, not fabricated into a conversion.
        self.assertEqual(out["value"], 100.0)


class NormalizePanelTests(unittest.TestCase):
    def test_mixed_panel_recognised_and_raw(self):
        markers = [
            {"name": "Glucose", "value": "5,55", "unit": "mmol/L"},
            {"name": "Hemoglobin", "value": "145", "unit": "g/L"},
            {"name": "Mystery Marker", "value": "42", "unit": "mg/dL"},
        ]
        out = ln.normalize_panel(markers)
        self.assertEqual(len(out), 3)

        glucose = next(m for m in out if m["marker_key"] == "glucose")
        self.assertAlmostEqual(glucose["value"], 100.0, places=1)

        # Hemoglobin g/L -> g/dL (145 / 10 = 14.5)
        hgb = next(m for m in out if m["marker_key"] == "hemoglobin")
        self.assertAlmostEqual(hgb["value"], 14.5, places=2)

        mystery = next(m for m in out if m.get("raw"))
        self.assertIsNone(mystery["marker_key"])
        self.assertEqual(mystery["value"], 42.0)
        self.assertFalse(mystery["unit_recognised"])


if __name__ == "__main__":
    unittest.main()
