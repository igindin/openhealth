"""Anti-drift tests for docs/methodology/*.md.

Every methodology page must exist, follow the strict section format the
dashboard parser relies on, and — for five key constants — carry the SAME
values as the live code. If a constant changes in code without the md being
updated, these tests go red.
"""

import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openhealth.connectors import ics_calendar
from openhealth.connectors import weather as weather_connector
from openhealth.modules import recovery, vo2max

DOCS = Path(__file__).resolve().parent.parent / "docs" / "methodology"

# Every parameter page (README and the shared evidence doc are extra).
EXPECTED_FILES = [
    "recovery.md",
    "correlations.md",
    "hrv.md",
    "rhr.md",
    "strain.md",
    "sleep.md",
    "vo2max.md",
    "circadian.md",
    "insights.md",
    "protocols.md",
    "biological-age.md",
    "day-load.md",
    "weather-flags.md",
    "data-quality.md",
]

REQUIRED_SECTIONS = (
    "## What this is",
    "## Formula / algorithm",
    "## Parameters (code constants)",
    "## Sources and confidence",
    "## Known limitations",
)


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _table_value(md_text: str, code_marker: str):
    """Value cell of the parameter-table row whose 'where in code' mentions marker.

    Rows look like: | parameter | value | where in code | why |
    Returns the first number found in the 'value' cell, as float.
    """
    for line in md_text.splitlines():
        if not line.strip().startswith("|") or code_marker not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        match = re.search(r"-?\d+(?:\.\d+)?", cells[1])
        if match:
            return float(match.group(0))
    return None


class MethodologyFilesExistTests(unittest.TestCase):
    def test_readme_exists(self):
        self.assertTrue((DOCS / "README.md").is_file())

    def test_all_parameter_pages_exist(self):
        for name in EXPECTED_FILES:
            with self.subTest(name=name):
                self.assertTrue((DOCS / name).is_file(), "missing %s" % name)


class MethodologyFormatTests(unittest.TestCase):
    """Strict format so the dashboard 'Methodology' page can parse the files."""

    def test_pages_have_title_and_header_line(self):
        for name in EXPECTED_FILES:
            with self.subTest(name=name):
                text = _read(name)
                lines = text.splitlines()
                self.assertTrue(lines[0].startswith("# "), "%s: first line must be '# <title>'" % name)
                self.assertRegex(
                    text,
                    r"(?m)^> algo_version: .+ · data source: .+ · editability: .+$",
                    "%s: missing the '> algo_version: ...' header line" % name,
                )

    def test_pages_have_required_sections(self):
        for name in EXPECTED_FILES:
            text = _read(name)
            for section in REQUIRED_SECTIONS:
                with self.subTest(name=name, section=section):
                    self.assertIn(section, text, "%s: missing section %r" % (name, section))

    def test_readme_indexes_every_page(self):
        readme = _read("README.md")
        for name in EXPECTED_FILES:
            with self.subTest(name=name):
                self.assertIn(name, readme, "README.md does not index %s" % name)


class MethodologyAntiDriftTests(unittest.TestCase):
    """Five key constants: the md tables must match the live code."""

    def test_recovery_weights_match_code(self):
        text = _read("recovery.md")
        for component in ("hrv", "rhr", "respiratory", "sleep"):
            with self.subTest(component=component):
                documented = _table_value(text, 'RECOVERY_WEIGHTS["%s"]' % component)
                self.assertIsNotNone(documented, "recovery.md: no table row for weight %r" % component)
                self.assertAlmostEqual(documented, recovery.RECOVERY_WEIGHTS[component], places=6)

    def test_recovery_baseline_window_matches_code(self):
        documented = _table_value(_read("recovery.md"), "DEFAULT_BASELINE_WINDOW_DAYS")
        self.assertIsNotNone(documented, "recovery.md: no table row for DEFAULT_BASELINE_WINDOW_DAYS")
        self.assertEqual(int(documented), recovery.DEFAULT_BASELINE_WINDOW_DAYS)

    def test_uth_coefficient_matches_code(self):
        documented = _table_value(_read("vo2max.md"), "UTH_COEFFICIENT")
        self.assertIsNotNone(documented, "vo2max.md: no table row for UTH_COEFFICIENT")
        self.assertAlmostEqual(documented, vo2max.UTH_COEFFICIENT, places=6)

    def test_pressure_drop_threshold_matches_code(self):
        documented = _table_value(_read("weather-flags.md"), "PRESSURE_DROP_HPA")
        self.assertIsNotNone(documented, "weather-flags.md: no table row for PRESSURE_DROP_HPA")
        self.assertAlmostEqual(documented, weather_connector.PRESSURE_DROP_HPA, places=6)

    def test_day_load_busy_weight_matches_code(self):
        """The 70-point busy-hours weight is a literal in day_load();
        verify it behaviorally: one 8h meeting saturates exactly that part."""
        documented = _table_value(_read("day-load.md"), "day_load")
        self.assertIsNotNone(documented, "day-load.md: no table row for the busy-hours weight")

        day = "2026-06-01"
        tz = timezone.utc
        start = datetime(2026, 6, 1, 9, 0, tzinfo=tz)
        end = start + timedelta(hours=ics_calendar.WORKDAY_HOURS)
        events = [
            {
                "start_iso": start.isoformat(),
                "end_iso": end.isoformat(),
                "summary": "full workday block",
                "all_day": False,
            }
        ]
        load = ics_calendar.day_load(events, day)
        self.assertEqual(int(documented), load["score_parts"]["busy_hours"])

    def test_extra_documented_constants_match_code(self):
        """Cheap extra guards: a handful of secondary constants."""
        recovery_md = _read("recovery.md")
        self.assertAlmostEqual(
            _table_value(recovery_md, "_HRV_FULL_SWING_SD"), recovery._HRV_FULL_SWING_SD, places=6
        )
        self.assertAlmostEqual(
            _table_value(recovery_md, "_RHR_FULL_SWING"), recovery._RHR_FULL_SWING, places=6
        )
        sleep_md = _read("sleep.md")
        self.assertAlmostEqual(
            _table_value(sleep_md, "DEFAULT_SLEEP_NEED_H"), recovery.DEFAULT_SLEEP_NEED_H, places=6
        )
        self.assertEqual(
            int(_table_value(sleep_md, "DEFAULT_SLEEP_DEBT_WINDOW_NIGHTS")),
            recovery.DEFAULT_SLEEP_DEBT_WINDOW_NIGHTS,
        )
        weather_md = _read("weather-flags.md")
        self.assertAlmostEqual(
            _table_value(weather_md, "HEAT_T_MAX_C"), weather_connector.HEAT_T_MAX_C, places=6
        )


if __name__ == "__main__":
    unittest.main()
