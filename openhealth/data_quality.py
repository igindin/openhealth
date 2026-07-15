"""Data-quality validation for any health record set (not only labs).

WHY
---
Before the system reasons over a person's data it should know how trustworthy
that data is. A duplicated row, a date in the future, a physiologically
impossible HRV, a long gap in a daily series, or a value that looks like it was
recorded in the wrong unit will all quietly distort a trend or a hypothesis. This
module surfaces those problems *as questions for review*, never as silent fixes —
it does not edit or drop data, it only reports.

It works on the same light record dicts the rest of the pipeline uses:
``{"name"/"marker"/"metric_name": str, "value": number|str, "unit": str|None,
"date": "YYYY-MM-DD"|None}``. Lab markers, WHOOP daily metrics, journal numbers —
all the same shape. Pure stdlib, zero external deps (core rule).

SEVERITY
--------
- ``high``    — almost certainly wrong (impossible value, future date).
- ``medium``  — likely a problem (conflicting duplicate, unit mismatch).
- ``low``     — worth a glance (a gap in a series).
Every issue carries a Russian message + a Russian suggestion; nothing is a
diagnosis or an instruction to change health behaviour.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date
from typing import Dict, List, Optional, Tuple

SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"


# --- physiologic plausibility bounds ---------------------------------------
#
# (low, high) INCLUSIVE hard bounds for common daily/vital metrics, in their usual
# unit. A value strictly below low or strictly above high is physiologically
# implausible for a living person and almost always a data error. The endpoints
# themselves are VALID: recovery/sleep of exactly 0% or 100% and spo2 of 100% are
# real readings, not errors. Keyed by a lowercased metric name; aliases map
# several spellings to one key.

PLAUSIBLE_BOUNDS: Dict[str, Tuple[float, float, str]] = {
    # metric_key: (low, high, unit/comment)
    "hrv": (1.0, 300.0, "ms rMSSD"),            # HRV outside 1-300 ms is not real
    "rhr": (25.0, 120.0, "bpm"),                # resting HR; <25 or >120 is implausible at rest
    "recovery": (0.0, 100.0, "%"),              # WHOOP recovery is a percentage
    "strain": (0.0, 21.0, "0-21"),              # WHOOP strain scale
    "sleep_h": (0.0, 16.0, "hours"),            # >16 h of sleep in a day is implausible
    "spo2": (50.0, 100.0, "%"),                 # oxygen saturation
    "glucose": (1.0, 40.0, "mmol/L or low mg/dL guard"),  # see note below
    "temperature": (30.0, 45.0, "°C"),          # body temperature
    "weight_kg": (20.0, 400.0, "kg"),
}

# Aliases -> canonical plausibility key (lowercased substring match on the name).
_METRIC_ALIASES: Dict[str, str] = {
    "hrv_rmssd_milli": "hrv", "hrv_rmssd": "hrv", "hrv": "hrv",
    "resting_heart_rate": "rhr", "rhr": "rhr", "resting hr": "rhr",
    "recovery_score": "recovery", "recovery": "recovery",
    "strain": "strain",
    "sleep_performance": "recovery",  # a percentage, treated like recovery bounds
    "sleep_h": "sleep_h", "sleep_hours": "sleep_h", "sleep duration": "sleep_h",
    "spo2": "spo2", "oxygen": "spo2",
    "temperature": "temperature", "temp": "temperature",
    "weight": "weight_kg", "weight_kg": "weight_kg",
}

# Key daily metrics whose continuity we check for gaps, and the gap threshold.
GAP_METRICS = ("recovery", "hrv", "rhr")
DEFAULT_GAP_DAYS = 4

# Metrics that should legitimately appear at most ONCE per day. Only these are
# checked for conflicting duplicates — otherwise a person with two workouts (each
# with its own strain / heart rate) or two naps in a day would be falsely flagged.
# Per-event metrics (workout strain, kilojoule, per-sleep efficiency) are excluded
# by omission: many-per-day is normal for them.
DAILY_UNIQUE_METRICS = {
    "recovery_score", "recovery", "hrv_rmssd", "hrv_rmssd_milli", "hrv",
    "resting_heart_rate", "rhr", "skin_temp_celsius", "respiratory_rate",
    # Lab markers are once-per-draw; a same-day conflict is a real problem.
    "glucose", "hba1c", "ldl", "hdl", "triglycerides", "total cholesterol",
    "ldl cholesterol", "hdl cholesterol", "vitamin d (25-oh)", "ferritin",
    "tsh", "c-reactive protein", "insulin (fasting)", "serum iron",
    "transferrin", "free t3", "free t4", "folate", "homocysteine",
    "vitamin b12", "creatinine", "sodium", "potassium",
}


def _metric_name(rec: Dict[str, object]) -> str:
    return str(rec.get("name") or rec.get("marker") or rec.get("metric_name") or "")


def _metric_key(name: str) -> Optional[str]:
    """Resolve a metric name to a plausibility/gap key, or None if unknown."""
    lowered = name.strip().lower()
    if lowered in _METRIC_ALIASES:
        return _METRIC_ALIASES[lowered]
    for alias, key in _METRIC_ALIASES.items():
        if alias in lowered:
            return key
    return None


def _record_date(rec: Dict[str, object]) -> Optional[str]:
    d = rec.get("date") or rec.get("captured_at") or rec.get("start_date")
    if not d:
        return None
    return str(d)[:10]


def _as_float(raw: object) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().lstrip("<>≤≥").strip()
    if "," in s and "." not in s and s.count(",") == 1:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _issue(severity: str, metric: str, date: Optional[str], message_ru: str,
           suggestion_ru: str, kind: str) -> Dict[str, object]:
    return {
        "kind": kind,
        "severity": severity,
        "metric": metric,
        "date": date,
        "message_ru": message_ru,
        "suggestion_ru": suggestion_ru,
    }


# --- individual checks ------------------------------------------------------

def _check_future_dates(records: List[Dict[str, object]], today: str) -> List[Dict[str, object]]:
    """Dates strictly after ``today`` are impossible for a recorded measurement."""
    issues: List[Dict[str, object]] = []
    try:
        today_d = _date.fromisoformat(today[:10])
    except ValueError:
        return issues
    for rec in records:
        d = _record_date(rec)
        if not d:
            continue
        try:
            rec_d = _date.fromisoformat(d)
        except ValueError:
            continue
        if rec_d > today_d:
            issues.append(_issue(
                SEV_HIGH, _metric_name(rec), d,
                f"Дата записи {d} в будущем (сегодня {today[:10]}).",
                "Проверить дату источника — вероятно опечатка или часовой пояс.",
                "future_date",
            ))
    return issues


def _check_duplicates(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Same metric + same date appearing twice with *different* values.

    Identical repeats are harmless (idempotent re-ingest); conflicting values on
    the same day are the real problem — which one is true?
    """
    issues: List[Dict[str, object]] = []
    seen: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for rec in records:
        d = _record_date(rec)
        name = _metric_name(rec).strip().lower()
        val = _as_float(rec.get("value"))
        if not d or not name or val is None:
            continue
        # Only metrics that should occur once per day can "conflict"; per-event
        # metrics (multiple workouts / naps) legitimately repeat within a day.
        if name not in DAILY_UNIQUE_METRICS:
            continue
        seen[(name, d)].append(val)
    for (name, d), values in seen.items():
        distinct = sorted(set(round(v, 6) for v in values))
        if len(distinct) > 1:
            issues.append(_issue(
                SEV_MEDIUM, name, d,
                f"Одна метрика '{name}' на {d} записана с разными значениями: "
                f"{', '.join(str(x) for x in distinct)}.",
                "Оставить один источник истины на дату или пометить какой верный.",
                "duplicate",
            ))
    return issues


def _check_impossible(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Values outside hard physiologic bounds for a known metric."""
    issues: List[Dict[str, object]] = []
    for rec in records:
        name = _metric_name(rec)
        key = _metric_key(name)
        if key is None or key not in PLAUSIBLE_BOUNDS:
            continue
        val = _as_float(rec.get("value"))
        if val is None:
            continue
        low, high, comment = PLAUSIBLE_BOUNDS[key]
        # Bounds are inclusive: only STRICTLY outside is implausible. This keeps
        # legitimate endpoints (recovery/sleep 0% or 100%, spo2 100%) clean.
        if val < low or val > high:
            issues.append(_issue(
                SEV_HIGH, name, _record_date(rec),
                f"Значение {name}={val} вне физиологичных границ ({low}-{high} {comment}).",
                "Проверить единицы и источник; такое значение почти наверняка ошибка ввода.",
                "impossible_value",
            ))
    return issues


def _check_gaps(records: List[Dict[str, object]], gap_days: int) -> List[Dict[str, object]]:
    """Gaps longer than ``gap_days`` in a key daily metric's dated series."""
    issues: List[Dict[str, object]] = []
    by_metric: Dict[str, List[str]] = defaultdict(list)
    for rec in records:
        key = _metric_key(_metric_name(rec))
        if key not in GAP_METRICS:
            continue
        d = _record_date(rec)
        if d:
            by_metric[key].append(d)
    for metric, dates in by_metric.items():
        parsed = sorted({_date.fromisoformat(d) for d in dates if _safe_iso(d)})
        for a, b in zip(parsed, parsed[1:]):
            gap = (b - a).days
            if gap > gap_days:
                issues.append(_issue(
                    SEV_LOW, metric, b.isoformat(),
                    f"Разрыв {gap} дн. в серии '{metric}' между {a.isoformat()} и "
                    f"{b.isoformat()}.",
                    "Возможен пропуск синхронизации устройства; тренд за этот период неполный.",
                    "series_gap",
                ))
    return issues


def _safe_iso(d: str) -> bool:
    try:
        _date.fromisoformat(d)
        return True
    except ValueError:
        return False


# Markers whose unit can plausibly be reported in two scales differing ~x18
# (mg/dL <-> mmol/L for glucose & cholesterol). If a value lands far outside the
# expected mg/dL range but would be sensible after x18, the unit is suspect.
_UNIT_X18_HINTS: Dict[str, Tuple[float, float]] = {
    # name-substring: (typical mg/dL low, typical mg/dL high)
    "glucose": (50.0, 300.0),
    "cholesterol": (80.0, 320.0),
    "ldl": (30.0, 250.0),
    "hdl": (20.0, 120.0),
}


def _check_unit_suspicion(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Heuristic: a glucose/cholesterol value that looks like mmol/L (x18 off).

    Example: glucose 5.5 with no unit reads as mg/dL = severe hypoglycaemia, but
    5.5 * 18 = 99 mg/dL is normal — so the value was almost certainly mmol/L. We
    flag it for review rather than silently converting.
    """
    issues: List[Dict[str, object]] = []
    for rec in records:
        name = _metric_name(rec).strip().lower()
        match_key = next((k for k in _UNIT_X18_HINTS if k in name), None)
        if match_key is None:
            continue
        unit = str(rec.get("unit") or "").strip().lower()
        # Only suspect when no explicit mg/dL unit was given.
        if "mg" in unit:
            continue
        val = _as_float(rec.get("value"))
        if val is None:
            continue
        lo, hi = _UNIT_X18_HINTS[match_key]
        # Value far below the mg/dL floor but sensible after x18 -> looks mmol/L.
        if val < lo / 3.0 and lo <= val * 18.0 <= hi:
            issues.append(_issue(
                SEV_MEDIUM, name, _record_date(rec),
                f"Значение {name}={val} похоже на ммоль/л: ×18 = {round(val * 18.0)} мг/дл "
                f"(в диапазоне нормы), а как мг/дл оно нереалистично низкое.",
                "Уточнить единицы измерения (ммоль/л vs мг/дл) у источника.",
                "unit_suspect",
            ))
    return issues


# --- top-level report -------------------------------------------------------

def validate_records(
    records: List[Dict[str, object]],
    today: Optional[str] = None,
    gap_days: int = DEFAULT_GAP_DAYS,
) -> Dict[str, object]:
    """Run every data-quality check and return a structured report.

    Returns ``{"issues": [...], "counts": {kind: n}, "by_severity": {...},
    "checked": n}``. ``issues`` entries are ``{kind, severity, metric, date,
    message_ru, suggestion_ru}``. Nothing in the data is modified.
    """
    today = today or _date.today().isoformat()
    issues: List[Dict[str, object]] = []
    issues += _check_future_dates(records, today)
    issues += _check_duplicates(records)
    issues += _check_impossible(records)
    issues += _check_gaps(records, gap_days)
    issues += _check_unit_suspicion(records)

    counts: Dict[str, int] = defaultdict(int)
    by_severity: Dict[str, int] = defaultdict(int)
    for it in issues:
        counts[str(it["kind"])] += 1
        by_severity[str(it["severity"])] += 1

    # Stable ordering: high severity first, then by kind, then date.
    sev_rank = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2}
    issues.sort(key=lambda it: (sev_rank.get(str(it["severity"]), 9),
                                str(it["kind"]), str(it["date"] or "")))

    return {
        "checked": len(records),
        "issues": issues,
        "counts": dict(counts),
        "by_severity": dict(by_severity),
    }


# Severity weights for the score: each issue subtracts from a perfect 100.
_SEVERITY_PENALTY = {SEV_HIGH: 12, SEV_MEDIUM: 6, SEV_LOW: 2}


def quality_score(report: Dict[str, object]) -> Dict[str, object]:
    """Turn a report into a 0-100 score with a per-severity breakdown.

    100 = no issues found. Each issue subtracts a severity-weighted penalty; the
    score floors at 0. The breakdown shows how many points each severity removed,
    so the number is explainable, never a black box.
    """
    issues = report.get("issues", []) or []
    breakdown: Dict[str, Dict[str, int]] = {}
    total_penalty = 0
    counts: Dict[str, int] = defaultdict(int)
    for it in issues:
        counts[str(it["severity"])] += 1
    for sev, penalty in _SEVERITY_PENALTY.items():
        n = counts.get(sev, 0)
        removed = n * penalty
        total_penalty += removed
        breakdown[sev] = {"count": n, "points_removed": removed}

    score = max(0, 100 - total_penalty)
    if score >= 90:
        verdict = "Данные выглядят чистыми."
    elif score >= 70:
        verdict = "Есть мелкие вопросы к данным, тренды в целом надёжны."
    elif score >= 40:
        verdict = "Заметные проблемы качества — проверить перед выводами."
    else:
        verdict = "Много проблем в данных — выводам пока доверять рано."

    return {
        "score": score,
        "breakdown": breakdown,
        "total_issues": len(issues),
        "verdict_ru": verdict,
    }
