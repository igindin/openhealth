"""Weather connector — Open-Meteo daily context as a recovery co-factor.

Weather is treated exactly like travel or calendar load: a *context* signal
that may explain a recovery dip, never a diagnosis. This connector pulls
daily weather for the configured home location from Open-Meteo (free, no API
key, ~10k requests/day) and turns it into three things:

1. ``fetch_day`` / ``fetch_range`` — canonical day dicts (temperature,
   mean sea-level pressure + its 24h change, humidity, precipitation, wind).
2. ``weather_context`` — cautious, evidence-graded flags with human-readable
   messages ("pressure dropping 9 hPa — possible bad day for the
   weather-sensitive"), honest about how weak the population evidence is.
3. ``weather_observations`` / ``weather_behaviors`` — records shaped for the
   index and for ``openhealth.modules.correlations.analyze`` so the same
   engine that answers "does alcohol hurt my recovery?" can answer "do
   pressure-drop days hurt my recovery?" on personal data.

Location config lives in ``~/.openhealth/weather.json`` (file mode 0600,
dir 0700, overridable via ``OPENHEALTH_HOME``) and never leaves the machine.
Pure stdlib; network calls go through one ``urlopen`` with an 8s timeout.
"""

import json
import os
from datetime import date as date_class
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import urlopen

from .. import evidence

SOURCE = "weather"
SOURCE_ID = "weather"
CONFIG_FILE = "weather.json"
TIMEOUT_S = 8

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# The forecast endpoint serves roughly the last 92 days + 16 ahead; anything
# older comes from the ERA5 archive endpoint. We switch a bit before the edge.
FORECAST_PAST_DAYS = 85
MAX_RANGE_DAYS = 370

DAILY_VARS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,"
    "weather_code,sunrise,sunset,daylight_duration,uv_index_max"
)
HOURLY_VARS = "temperature_2m,pressure_msl,relative_humidity_2m"

# --- thresholds (referenced by tests and by the dashboard contract) ----------
#
# PRESSURE_DROP_HPA: >=8 hPa/24h is a "rapid fall" in synoptic terms (a strong
#   front). Population evidence linking falls to headaches/joint pain is
#   observational and mixed — C3 at best; a raw personal pattern is C2.
# HEAT_T_MAX_C: >=30°C daytime heat degrading sleep that night is consistent
#   across sleep studies — C4 ("likely"), the strongest claim we make here.
# COLD_T_MIN_C: <=0°C is plain context; recovery impact unproven (C2).
# HUMIDITY_HIGH_PCT: >=85% mainly matters combined with warmth; alone weak (C2).
# RAIN_MM: >=1 mm — enough rain to displace an outdoor-walk habit (C2 context).
PRESSURE_DROP_HPA = 8.0
HEAT_T_MAX_C = 30.0
COLD_T_MIN_C = 0.0
HUMIDITY_HIGH_PCT = 85.0
RAIN_MM = 1.0


class WeatherError(RuntimeError):
    """Raised on bad config, bad arguments or an unusable API response."""


# --------------------------------------------------------------------------- #
# Config: ~/.openhealth/weather.json  {"lat": .., "lon": .., "label": ..}
# --------------------------------------------------------------------------- #


def config_home() -> Path:
    """``~/.openhealth``, overridable via ``OPENHEALTH_HOME`` (tests, portability)."""
    return Path(os.environ.get("OPENHEALTH_HOME") or "~/.openhealth").expanduser()


def weather_config_path() -> Path:
    return config_home() / CONFIG_FILE


def set_location(lat: Any, lon: Any, label: str) -> Path:
    """Validate and persist the home location privately (dir 0700, file 0600)."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        raise WeatherError("lat/lon must be numbers") from None
    if not (-90.0 <= lat_f <= 90.0):
        raise WeatherError("lat out of range [-90, 90]: %s" % lat)
    if not (-180.0 <= lon_f <= 180.0):
        raise WeatherError("lon out of range [-180, 180]: %s" % lon)
    if not isinstance(label, str) or not label.strip() or len(label) > 80:
        raise WeatherError("label must be a non-empty string up to 80 chars")

    home = config_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    path = weather_config_path()
    tmp = path.with_name(path.name + ".tmp")
    payload = {"lat": lat_f, "lon": lon_f, "label": label.strip()}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


def geocode_city(name: str, count: int = 1, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """City name → {"lat", "lon", "label"} via open-meteo geocoding (no API key).

    For the "home city" UI: set_location(**geocode_city("Amsterdam")).
    Returns None when nothing was found or the network failed (the caller
    decides how to report it).
    """
    name = (name or "").strip()
    if not name or len(name) > 80:
        return None
    from urllib.parse import urlencode
    from urllib.request import urlopen

    # "language": "ru" is deliberate: it makes the geocoder accept and return
    # Russian-language place names. Changing it would drop that support.
    url = GEOCODING_URL + "?" + urlencode({"name": name, "count": count, "language": "ru", "format": "json"})
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 (https, fixed host)
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    results = (payload or {}).get("results") or []
    if not results:
        return None
    top = results[0]
    label = ", ".join(x for x in (top.get("name"), top.get("country")) if x)
    try:
        return {"lat": float(top["latitude"]), "lon": float(top["longitude"]), "label": label}
    except (KeyError, TypeError, ValueError):
        return None


def load_location() -> Optional[Dict[str, Any]]:
    """``{"lat", "lon", "label"}`` or None when absent/unreadable/invalid."""
    path = weather_config_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        lat = float(raw["lat"])
        lon = float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {"lat": lat, "lon": lon, "label": str(raw.get("label") or "")}


# --------------------------------------------------------------------------- #
# Fetch: Open-Meteo forecast / archive
# --------------------------------------------------------------------------- #


def _parse_iso_date(raw: str) -> date_class:
    try:
        return date_class.fromisoformat(str(raw))
    except ValueError:
        raise WeatherError("bad ISO date: %r" % raw) from None


def _endpoint_for(start: date_class) -> str:
    """Forecast API covers the recent past; older ranges go to the archive."""
    if (date_class.today() - start).days > FORECAST_PAST_DAYS:
        return ARCHIVE_URL
    return FORECAST_URL


def _fetch_json(url: str) -> Dict[str, Any]:
    with urlopen(url, timeout=TIMEOUT_S) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise WeatherError("unexpected Open-Meteo response shape")
    return payload


def _at(values: List[Any], i: int) -> Optional[float]:
    if i >= len(values) or values[i] is None:
        return None
    try:
        return float(values[i])
    except (TypeError, ValueError):
        return None


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


_DAY_KEYS = (
    "t_min",
    "t_max",
    "t_mean",
    "pressure_msl_mean",
    "pressure_change_24h",
    "humidity_mean",
    "precipitation_mm",
    "wind_max",
    "sunrise",
    "sunset",
    "daylight_h",
    "uv_index_max",
    "weather_code",
)


def _blank_day(day: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"date": day}
    for key in _DAY_KEYS:
        entry[key] = None
    return entry


def _aggregate(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Open-Meteo daily+hourly arrays -> {date: canonical day dict}."""
    days: Dict[str, Dict[str, Any]] = {}

    daily = payload.get("daily") or {}
    for i, day in enumerate(daily.get("time") or []):
        day = str(day)[:10]
        entry = days.setdefault(day, _blank_day(day))
        entry["t_max"] = _at(daily.get("temperature_2m_max") or [], i)
        entry["t_min"] = _at(daily.get("temperature_2m_min") or [], i)
        entry["precipitation_mm"] = _at(daily.get("precipitation_sum") or [], i)
        entry["wind_max"] = _at(daily.get("wind_speed_10m_max") or [], i)
        code = _at(daily.get("weather_code") or [], i)
        entry["weather_code"] = int(code) if code is not None else None
        # External factors: daylight and UV (open-meteo daily).
        # sunrise/sunset are ISO strings; _at() would choke trying to float()
        # them, so we read those values raw.
        def _at_str(values, idx):
            return str(values[idx])[11:16] if idx < len(values) and values[idx] else None

        entry["sunrise"] = _at_str(daily.get("sunrise") or [], i)
        entry["sunset"] = _at_str(daily.get("sunset") or [], i)
        dl = _at(daily.get("daylight_duration") or [], i)  # seconds
        entry["daylight_h"] = round(dl / 3600.0, 1) if dl is not None else None
        uv = _at(daily.get("uv_index_max") or [], i)
        entry["uv_index_max"] = round(uv, 1) if uv is not None else None

    # Daily means for pressure/humidity/temperature are computed from hourly
    # arrays ourselves: both endpoints serve these reliably, while the daily
    # "*_mean" variables differ between forecast and archive.
    hourly = payload.get("hourly") or {}
    acc: Dict[str, Dict[str, List[float]]] = {}
    for i, stamp in enumerate(hourly.get("time") or []):
        day = str(stamp)[:10]
        slot = acc.setdefault(day, {"t": [], "p": [], "h": []})
        for key, name in (("t", "temperature_2m"), ("p", "pressure_msl"), ("h", "relative_humidity_2m")):
            value = _at(hourly.get(name) or [], i)
            if value is not None:
                slot[key].append(value)
    for day, slot in acc.items():
        entry = days.setdefault(day, _blank_day(day))
        entry["t_mean"] = _mean(slot["t"])
        entry["pressure_msl_mean"] = _mean(slot["p"])
        entry["humidity_mean"] = _mean(slot["h"])

    ordered = sorted(days)
    for prev, cur in zip(ordered, ordered[1:]):
        p0 = days[prev].get("pressure_msl_mean")
        p1 = days[cur].get("pressure_msl_mean")
        if p0 is not None and p1 is not None:
            days[cur]["pressure_change_24h"] = round(p1 - p0, 1)
    return days


def _has_data(day: Dict[str, Any]) -> bool:
    return any(day.get(key) is not None for key in ("t_min", "t_max", "t_mean", "pressure_msl_mean"))


def fetch_range(
    start: str,
    end: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Daily weather dicts for [start, end] inclusive, sorted by date.

    Coordinates default to the configured home location. One extra day is
    requested before ``start`` so ``pressure_change_24h`` is defined for the
    first day too. Days the API has no data for are dropped.
    """
    start_d = _parse_iso_date(start)
    end_d = _parse_iso_date(end)
    if end_d < start_d:
        raise WeatherError("end date %s is before start date %s" % (end, start))
    if (end_d - start_d).days > MAX_RANGE_DAYS:
        raise WeatherError("range longer than %d days; split the request" % MAX_RANGE_DAYS)

    if lat is None or lon is None:
        config = load_location()
        if config is None:
            raise WeatherError("location is not configured; call set_location(lat, lon, label) first")
        lat, lon = config["lat"], config["lon"]

    padded_start = start_d - timedelta(days=1)
    url = _endpoint_for(padded_start) + "?" + urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": padded_start.isoformat(),
            "end_date": end_d.isoformat(),
            "daily": DAILY_VARS,
            "hourly": HOURLY_VARS,
            "timezone": "auto",
        }
    )
    days = _aggregate(_fetch_json(url))
    return [
        days[day]
        for day in sorted(days)
        if start <= day <= end and _has_data(days[day])
    ]


def fetch_day(date_value: str, lat: Optional[float] = None, lon: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """One canonical day dict (or None when the API has nothing for that day)."""
    days = fetch_range(date_value, date_value, lat=lat, lon=lon)
    return days[0] if days else None


# --------------------------------------------------------------------------- #
# Context flags: cautious, graded, honest about evidence
# --------------------------------------------------------------------------- #


def _flag_grade(
    flag: str, susceptibility: Optional[Dict[str, str]], population: evidence.Confidence
) -> Tuple[evidence.Confidence, bool]:
    """Grade a flag. Personal susceptibility entries follow the n-of-1 cap.

    ``susceptibility`` maps flag name -> "declared" | "validated". A declared
    sensitivity is a raw personal pattern (capped at C2); one that survived
    repeated on/off observation may rise to C3. Without a personal entry the
    flag carries the population-level grade.
    """
    status = (susceptibility or {}).get(flag)
    if status == "validated":
        return evidence.cap_personal_pattern(evidence.Confidence.C3, validated_switches=1), True
    if status == "declared":
        return evidence.cap_personal_pattern(evidence.Confidence.C3, validated_switches=0), True
    return population, False


def weather_context(
    day: Dict[str, Any], susceptibility: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    """Evidence-graded flags for one day dict.

    Returns ``[{"flag", "grade", "personal", "value", "message_ru"}, ...]``.
    Every message states the uncertainty in plain language; nothing here is a
    diagnosis or an instruction, only context for reading the recovery number.
    """
    flags: List[Dict[str, Any]] = []

    pressure_change = day.get("pressure_change_24h")
    if pressure_change is not None and pressure_change <= -PRESSURE_DROP_HPA:
        grade, personal = _flag_grade("pressure_drop", susceptibility, evidence.Confidence.C3)
        message = (
            "Pressure is falling %.0f hPa over 24h — a sharp swing. "
            "Population-level evidence on weather sensitivity is weak and mixed, "
            "so this is a hypothesis, not a fact." % abs(pressure_change)
        )
        if personal:
            message += (
                " You have noted a sensitivity to pressure: a personal pattern, "
                + ("it has already repeated — worth checking." if grade == evidence.Confidence.C3 else "still a weak signal.")
            )
        flags.append(
            {
                "flag": "pressure_drop",
                "grade": grade.value,
                "personal": personal,
                "value": pressure_change,
                "message_ru": message,
            }
        )

    t_max = day.get("t_max")
    if t_max is not None and t_max >= HEAT_T_MAX_C:
        grade, personal = _flag_grade("heat", susceptibility, evidence.Confidence.C4)
        flags.append(
            {
                "flag": "heat",
                "grade": grade.value,
                "personal": personal,
                "value": t_max,
                "message_ru": (
                    "Heat: max %.0f°. Warmth worsens sleep — that is a consistent finding "
                    "in sleep research, so overnight recovery may dip." % t_max
                ),
            }
        )

    t_min = day.get("t_min")
    if t_min is not None and t_min <= COLD_T_MIN_C:
        grade, personal = _flag_grade("cold", susceptibility, evidence.Confidence.C2)
        flags.append(
            {
                "flag": "cold",
                "grade": grade.value,
                "personal": personal,
                "value": t_min,
                "message_ru": (
                    "Cold: min %.0f°. The effect on recovery is unproven — "
                    "just context for the day." % t_min
                ),
            }
        )

    humidity = day.get("humidity_mean")
    if humidity is not None and humidity >= HUMIDITY_HIGH_PCT:
        grade, personal = _flag_grade("humidity", susceptibility, evidence.Confidence.C2)
        flags.append(
            {
                "flag": "humidity",
                "grade": grade.value,
                "personal": personal,
                "value": humidity,
                "message_ru": (
                    "High humidity (%.0f%%). Combined with warmth it may interfere with sleep; "
                    "on its own it is a weak signal." % humidity
                ),
            }
        )

    precipitation = day.get("precipitation_mm")
    if precipitation is not None and precipitation >= RAIN_MM:
        grade, personal = _flag_grade("precipitation", susceptibility, evidence.Confidence.C2)
        flags.append(
            {
                "flag": "precipitation",
                "grade": grade.value,
                "personal": personal,
                "value": precipitation,
                "message_ru": (
                    "Precipitation %.1f mm — your usual walk may have been skipped. Keep that "
                    "in mind when you read tomorrow's recovery." % precipitation
                ),
            }
        )

    return flags


def day_summary_ru(day: Dict[str, Any]) -> str:
    """One dashboard line, e.g. "18°, pressure falling -9 hPa — keep an eye on how you feel"."""
    temperature = day.get("t_mean") if day.get("t_mean") is not None else day.get("t_max")
    head = "%d°" % round(temperature) if temperature is not None else "weather"

    bits: List[str] = []
    pressure_change = day.get("pressure_change_24h")
    if pressure_change is not None and pressure_change <= -PRESSURE_DROP_HPA:
        bits.append("pressure falling %d hPa — keep an eye on how you feel" % round(pressure_change))
    elif pressure_change is not None and pressure_change >= PRESSURE_DROP_HPA:
        bits.append("pressure rising +%d hPa" % round(pressure_change))
    t_max = day.get("t_max")
    if t_max is not None and t_max >= HEAT_T_MAX_C:
        bits.append("heat %d° — sleep may suffer" % round(t_max))
    t_min = day.get("t_min")
    if t_min is not None and t_min <= COLD_T_MIN_C:
        bits.append("cold, min %d°" % round(t_min))
    humidity = day.get("humidity_mean")
    if humidity is not None and humidity >= HUMIDITY_HIGH_PCT:
        bits.append("humidity %d%%" % round(humidity))
    precipitation = day.get("precipitation_mm")
    if precipitation is not None and precipitation >= RAIN_MM:
        bits.append("precipitation %.1f mm" % precipitation)

    if not bits:
        return "%s, calm weather — no weather flags" % head
    return "%s, %s" % (head, "; ".join(bits))


# --------------------------------------------------------------------------- #
# Bridge to the index and the correlations engine
# --------------------------------------------------------------------------- #

# metric_name -> (day dict key, unit)
_OBS_METRICS: Dict[str, Tuple[str, str]] = {
    "weather_t_mean": ("t_mean", "c"),
    "weather_t_max": ("t_max", "c"),
    "weather_t_min": ("t_min", "c"),
    "weather_pressure_msl_mean": ("pressure_msl_mean", "hPa"),
    "weather_pressure_change_24h": ("pressure_change_24h", "hPa"),
    "weather_humidity_mean": ("humidity_mean", "%"),
    "weather_precipitation_mm": ("precipitation_mm", "mm"),
    "weather_wind_max": ("wind_max", "km/h"),
}


def weather_observations(range_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Day dicts -> Observation-shaped records for the index (one per metric/day).

    ``observation_kind`` is ``weather_daily`` and metric names carry the
    ``weather_`` prefix, so weather context never collides with body signals.
    """
    records: List[Dict[str, Any]] = []
    for day in range_data:
        date_value = day.get("date")
        if not date_value:
            continue
        for metric, (key, unit) in _OBS_METRICS.items():
            value = day.get(key)
            if value is None:
                continue
            records.append(
                {
                    "id": "obs-weather-%s-%s" % (metric, date_value),
                    "record_type": "Observation",
                    "source_id": SOURCE_ID,
                    "title": "%s (%s)" % (metric.replace("_", " "), date_value),
                    "summary": "%s = %s %s" % (metric, value, unit),
                    "artifact_ids": [],
                    "evidence_class": "contextual",
                    "confidence": 0.85,
                    "date": date_value,
                    "tags": [SOURCE, "environment"],
                    "metadata": {"connector": SOURCE, "weather_code": day.get("weather_code")},
                    "observation_kind": "weather_daily",
                    "metric_name": metric,
                    "value": float(value),
                    "unit": unit,
                }
            )
    return records


# behavior_id -> (name_ru, day dict key it depends on, yes-predicate)
_BEHAVIOR_FLAGS: Dict[str, Tuple[str, str, Callable[[float], bool]]] = {
    "weather_pressure_drop": (
        "Day with a sharp pressure drop",
        "pressure_change_24h",
        lambda v: v <= -PRESSURE_DROP_HPA,
    ),
    "weather_heat": ("Hot day (>=30°)", "t_max", lambda v: v >= HEAT_T_MAX_C),
    "weather_cold": ("Cold day (<=0°)", "t_min", lambda v: v <= COLD_T_MIN_C),
    "weather_high_humidity": ("Very humid day (>=85%)", "humidity_mean", lambda v: v >= HUMIDITY_HIGH_PCT),
    "weather_rain": ("Rainy day (>=1 mm)", "precipitation_mm", lambda v: v >= RAIN_MM),
}


def weather_behaviors(
    range_data: List[Dict[str, Any]], recovery_by_day: Dict[str, float]
) -> List[Dict[str, Any]]:
    """Day dicts + recovery map -> input for ``modules.correlations.analyze``.

    Each weather factor becomes a boolean "behavior" (pressure-drop day yes/no,
    heat day yes/no, ...) paired with that day's recovery score, so the same
    5-yes/5-no guarded engine computes "pressure drops -> recovery drops?"
    on personal data. Days without recovery or without the metric are skipped.
    """
    behaviors: List[Dict[str, Any]] = []
    for behavior_id, (name, key, predicate) in _BEHAVIOR_FLAGS.items():
        pairs: List[Dict[str, Any]] = []
        for day in range_data:
            date_value = day.get("date")
            recovery = recovery_by_day.get(date_value) if date_value else None
            value = day.get(key)
            if date_value is None or recovery is None or value is None:
                continue
            pairs.append({"date": date_value, "yes": bool(predicate(value)), "recovery": float(recovery)})
        behaviors.append(
            {
                "behavior_id": behavior_id,
                "behavior_name": name,
                "category": "weather",
                "pairs": pairs,
            }
        )
    return behaviors
