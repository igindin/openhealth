"""Tests for the weather connector (Open-Meteo).

No network: ``urlopen`` inside ``openhealth.connectors.weather`` is replaced
by a fake that serves a generated Open-Meteo-shaped payload (daily + hourly
arrays) and records every requested URL. Config tests redirect
``OPENHEALTH_HOME`` into a temp dir.

Run directly:  PYTHONPATH=$PWD python3 -m pytest tests/test_weather.py
"""

import json
import os
import stat
import unittest
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openhealth.connectors import weather
from openhealth.modules import correlations

# --------------------------------------------------------------------------- #
# Fake urlopen plumbing + payload builder
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(payload, calls):
    def fake(url, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        return _FakeResponse(payload)

    return fake


def make_payload(days):
    """Open-Meteo response shape from a list of per-day specs.

    Each spec: {"date", "t_min", "t_max", "t", "p", "h", "precip", "wind", "code"}
    where t/p/h are constant hourly temperature/pressure/humidity.
    """
    daily = {
        "time": [],
        "temperature_2m_max": [],
        "temperature_2m_min": [],
        "precipitation_sum": [],
        "wind_speed_10m_max": [],
        "weather_code": [],
    }
    hourly = {"time": [], "temperature_2m": [], "pressure_msl": [], "relative_humidity_2m": []}
    for spec in days:
        daily["time"].append(spec["date"])
        daily["temperature_2m_max"].append(spec.get("t_max", 20.0))
        daily["temperature_2m_min"].append(spec.get("t_min", 10.0))
        daily["precipitation_sum"].append(spec.get("precip", 0.0))
        daily["wind_speed_10m_max"].append(spec.get("wind", 15.0))
        daily["weather_code"].append(spec.get("code", 3))
        for hour in range(24):
            hourly["time"].append("%sT%02d:00" % (spec["date"], hour))
            hourly["temperature_2m"].append(spec.get("t", 15.0))
            hourly["pressure_msl"].append(spec.get("p", 1013.0))
            hourly["relative_humidity_2m"].append(spec.get("h", 60.0))
    return {"daily": daily, "hourly": hourly}


def _iso(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def make_day(**overrides):
    """A canonical day dict with calm defaults, for context/summary tests."""
    day = {
        "date": "2026-06-09",
        "t_min": 12.0,
        "t_max": 21.0,
        "t_mean": 16.0,
        "pressure_msl_mean": 1013.0,
        "pressure_change_24h": 1.0,
        "humidity_mean": 60.0,
        "precipitation_mm": 0.0,
        "wind_max": 15.0,
        "weather_code": 3,
    }
    day.update(overrides)
    return day


# --------------------------------------------------------------------------- #
# Fetch + parsing
# --------------------------------------------------------------------------- #


class FetchTest(unittest.TestCase):
    def test_fetch_day_parses_fields_and_pressure_change(self):
        payload = make_payload(
            [
                {"date": _iso(2), "p": 1015.0, "t": 14.0},
                {"date": _iso(1), "p": 1006.0, "t": 17.5, "t_min": 11.0, "t_max": 22.5,
                 "h": 72.0, "precip": 2.4, "wind": 31.0, "code": 61},
            ]
        )
        calls = []
        with patch.object(weather, "urlopen", _fake_urlopen(payload, calls)):
            day = weather.fetch_day(_iso(1), lat=52.37, lon=4.89)

        self.assertEqual(day["date"], _iso(1))
        self.assertEqual(day["t_min"], 11.0)
        self.assertEqual(day["t_max"], 22.5)
        self.assertEqual(day["t_mean"], 17.5)
        self.assertEqual(day["pressure_msl_mean"], 1006.0)
        self.assertEqual(day["pressure_change_24h"], -9.0)
        self.assertEqual(day["humidity_mean"], 72.0)
        self.assertEqual(day["precipitation_mm"], 2.4)
        self.assertEqual(day["wind_max"], 31.0)
        self.assertEqual(day["weather_code"], 61)
        # One request, 8s timeout, recent date -> forecast endpoint.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], weather.TIMEOUT_S)
        self.assertIn("api.open-meteo.com/v1/forecast", calls[0]["url"])

    def test_fetch_range_pads_start_and_returns_window(self):
        specs = [{"date": _iso(4), "p": 1010.0}]
        for ago, pressure in ((3, 1012.0), (2, 1004.0), (1, 1009.0)):
            specs.append({"date": _iso(ago), "p": pressure})
        calls = []
        with patch.object(weather, "urlopen", _fake_urlopen(make_payload(specs), calls)):
            days = weather.fetch_range(_iso(3), _iso(1), lat=52.37, lon=4.89)

        # The padding day is requested but not returned.
        self.assertIn("start_date=%s" % _iso(4), calls[0]["url"])
        self.assertEqual([d["date"] for d in days], [_iso(3), _iso(2), _iso(1)])
        # First day of the window still gets its 24h change from the padding day.
        self.assertEqual(days[0]["pressure_change_24h"], 2.0)
        self.assertEqual(days[1]["pressure_change_24h"], -8.0)
        self.assertEqual(days[2]["pressure_change_24h"], 5.0)

    def test_old_range_uses_archive_endpoint(self):
        old = _iso(120)
        calls = []
        with patch.object(weather, "urlopen", _fake_urlopen(make_payload([{"date": old}]), calls)):
            weather.fetch_range(old, old, lat=52.37, lon=4.89)
        self.assertIn("archive-api.open-meteo.com/v1/archive", calls[0]["url"])

    def test_bad_arguments(self):
        with self.assertRaises(weather.WeatherError):
            weather.fetch_range(_iso(1), _iso(3), lat=1.0, lon=1.0)  # end before start
        with self.assertRaises(weather.WeatherError):
            weather.fetch_range("not-a-date", _iso(1), lat=1.0, lon=1.0)
        with self.assertRaises(weather.WeatherError):
            weather.fetch_range(_iso(800), _iso(1), lat=1.0, lon=1.0)  # too long


# --------------------------------------------------------------------------- #
# Config: ~/.openhealth/weather.json
# --------------------------------------------------------------------------- #


class ConfigTest(unittest.TestCase):
    def test_set_and_load_roundtrip_with_0600(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OPENHEALTH_HOME": tmp}):
                path = weather.set_location(52.37, 4.89, "Amsterdam")
                self.assertTrue(path.is_file())
                mode = stat.S_IMODE(os.stat(path).st_mode)
                self.assertEqual(mode, 0o600)
                config = weather.load_location()
        self.assertEqual(config, {"lat": 52.37, "lon": 4.89, "label": "Amsterdam"})

    def test_set_location_validation(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OPENHEALTH_HOME": tmp}):
                for lat, lon, label in (
                    (91.0, 0.0, "x"),
                    (-90.5, 0.0, "x"),
                    (0.0, 180.5, "x"),
                    (0.0, -181.0, "x"),
                    ("abc", 0.0, "x"),
                    (0.0, 0.0, ""),
                    (0.0, 0.0, "y" * 81),
                ):
                    with self.assertRaises(weather.WeatherError):
                        weather.set_location(lat, lon, label)
                self.assertIsNone(weather.load_location())

    def test_boundary_coordinates_accepted(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OPENHEALTH_HOME": tmp}):
                weather.set_location(90, -180, "Pole")
                self.assertEqual(weather.load_location()["lat"], 90.0)

    def test_fetch_without_config_raises(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OPENHEALTH_HOME": tmp}):
                with self.assertRaises(weather.WeatherError):
                    weather.fetch_day(_iso(1))


# --------------------------------------------------------------------------- #
# Context flags: thresholds and grades
# --------------------------------------------------------------------------- #


class ContextTest(unittest.TestCase):
    def _flags(self, **overrides):
        return {f["flag"]: f for f in weather.weather_context(make_day(**overrides))}

    def test_pressure_drop_boundary(self):
        self.assertIn("pressure_drop", self._flags(pressure_change_24h=-8.0))
        self.assertNotIn("pressure_drop", self._flags(pressure_change_24h=-7.9))
        # Population evidence is observational at best: C3 and an honest message.
        flag = self._flags(pressure_change_24h=-9.0)["pressure_drop"]
        self.assertEqual(flag["grade"], "C3")
        self.assertFalse(flag["personal"])
        self.assertIn("weak", flag["message_ru"])

    def test_heat_boundary_and_grade(self):
        self.assertNotIn("heat", self._flags(t_max=29.9))
        flag = self._flags(t_max=30.0)["heat"]
        self.assertEqual(flag["grade"], "C4")  # heat vs sleep is established
        self.assertIn("sleep", flag["message_ru"].lower())

    def test_cold_humidity_rain_boundaries(self):
        self.assertIn("cold", self._flags(t_min=0.0))
        self.assertNotIn("cold", self._flags(t_min=0.1))
        self.assertIn("humidity", self._flags(humidity_mean=85.0))
        self.assertNotIn("humidity", self._flags(humidity_mean=84.9))
        self.assertIn("precipitation", self._flags(precipitation_mm=1.0))
        self.assertNotIn("precipitation", self._flags(precipitation_mm=0.9))
        for name in ("cold", "humidity"):
            self.assertEqual(self._flags(t_min=-3.0, humidity_mean=90.0)[name]["grade"], "C2")

    def test_missing_metrics_produce_no_flags(self):
        day = {key: None for key in make_day()}
        day["date"] = "2026-06-09"
        self.assertEqual(weather.weather_context(day), [])

    def test_susceptibility_grades(self):
        day = make_day(pressure_change_24h=-10.0)
        declared = weather.weather_context(day, susceptibility={"pressure_drop": "declared"})[0]
        self.assertEqual(declared["grade"], "C2")  # raw personal pattern is capped
        self.assertTrue(declared["personal"])
        validated = weather.weather_context(day, susceptibility={"pressure_drop": "validated"})[0]
        self.assertEqual(validated["grade"], "C3")  # survived switches -> hypothesis
        self.assertTrue(validated["personal"])
        self.assertIn("repeated", validated["message_ru"])


# --------------------------------------------------------------------------- #
# Dashboard one-liner
# --------------------------------------------------------------------------- #


class SummaryTest(unittest.TestCase):
    def test_pressure_drop_line(self):
        line = weather.day_summary_ru(make_day(t_mean=18.0, pressure_change_24h=-9.0))
        self.assertEqual(line, "18°, pressure falling -9 hPa — keep an eye on how you feel")

    def test_calm_day(self):
        self.assertEqual(weather.day_summary_ru(make_day()), "16°, calm weather — no weather flags")

    def test_combined_flags(self):
        line = weather.day_summary_ru(make_day(t_mean=31.0, t_max=33.0, precipitation_mm=4.2))
        self.assertIn("heat 33°", line)
        self.assertIn("precipitation 4.2 mm", line)


# --------------------------------------------------------------------------- #
# Bridge: Observation records + correlations input
# --------------------------------------------------------------------------- #


class BridgeTest(unittest.TestCase):
    def test_weather_observations_shape(self):
        records = weather.weather_observations([make_day(date="2026-06-08"), make_day(date="2026-06-09")])
        self.assertEqual(len(records), 2 * 8)  # 8 metrics per full day
        ids = {r["id"] for r in records}
        self.assertEqual(len(ids), len(records))
        for record in records:
            self.assertEqual(record["record_type"], "Observation")
            self.assertEqual(record["observation_kind"], "weather_daily")
            self.assertTrue(record["metric_name"].startswith("weather_"))
            self.assertIsInstance(record["value"], float)
            self.assertIn(record["date"], ("2026-06-08", "2026-06-09"))

    def test_weather_observations_skips_missing_values(self):
        day = make_day(pressure_change_24h=None, humidity_mean=None)
        metrics = {r["metric_name"] for r in weather.weather_observations([day])}
        self.assertNotIn("weather_pressure_change_24h", metrics)
        self.assertNotIn("weather_humidity_mean", metrics)
        self.assertIn("weather_t_mean", metrics)

    def test_weather_behaviors_feed_correlations(self):
        # 12 alternating-block days: drop days hover at recovery 50, calm at 70.
        days, recovery = [], {}
        for i in range(12):
            day_iso = (date(2026, 5, 1) + timedelta(days=i)).isoformat()
            drop = i in (0, 1, 2, 6, 7, 8)  # ABAB-ish blocks -> several switches
            days.append(make_day(date=day_iso, pressure_change_24h=-9.0 if drop else 0.5))
            recovery[day_iso] = 50.0 + (i % 3) if drop else 70.0 + (i % 3)

        behaviors = weather.weather_behaviors(days, recovery)
        by_id = {b["behavior_id"]: b for b in behaviors}
        self.assertEqual(len(by_id["weather_pressure_drop"]["pairs"]), 12)
        # Days without recovery are skipped.
        partial = weather.weather_behaviors(days, {days[0]["date"]: 60.0})
        self.assertEqual(len(partial[0]["pairs"]), 1)

        insights = correlations.analyze(behaviors)
        impacts = {i["metadata"]["behavior_id"]: i for i in insights}
        self.assertIn("weather_pressure_drop", impacts)
        insight = impacts["weather_pressure_drop"]
        self.assertEqual(insight["metadata"]["direction"], "negative")
        self.assertEqual(insight["metadata"]["n_yes"], 6)
        self.assertEqual(insight["metadata"]["n_no"], 6)


if __name__ == "__main__":
    unittest.main()
