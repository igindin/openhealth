"""Blood-panel analysis on top of reference_ranges + clinical_optima.

WHERE THIS SITS
---------------
- ``reference_ranges`` answers "is this value normal for a lab?" (one value).
- ``clinical_optima`` answers "is this value optimal?" (one value, dual verdict).
- ``lab_panel`` (this module) works across *many records over time*: it builds a
  marker's history, groups markers into clinical panels, computes derived indices
  (ratios a single marker cannot express), and tells the user when a value is old
  enough to consider re-testing.

It never diagnoses. Every interpretation is framed as "discuss with a clinician",
carries a confidence grade (C1-C5 from ``evidence``) where one applies, and reuses
the standing optimum disclaimer. Pure stdlib, zero external deps (core rule).

RECORD CONTRACT
---------------
A "record" here is a light dict (the same shape an Observation payload exposes):
``{"name"/"marker"/"metric_name": str, "value": number|str, "unit": str|None,
"date": "YYYY-MM-DD"|None, "sex": "male"/"female"|None}``. Records are normalized
through ``lab_normalization`` before interpretation, so mixed units (SI vs
conventional, comma decimals, ``<0.01`` qualifiers) all collapse to the marker's
conventional unit first.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import clinical_optima, lab_normalization, reference_ranges
from .clinical_optima import (
    OPTIMUM_DISCLAIMER,
    SUBOPTIMAL_HIGH,
    SUBOPTIMAL_LOW,
)
from .evidence import Confidence

DISCUSS = "Discuss with a clinician; this is not a diagnosis."


# --- record helpers ---------------------------------------------------------

def _marker_name(rec: Dict[str, object]) -> str:
    return str(rec.get("name") or rec.get("marker") or rec.get("metric_name") or "")


def _record_date(rec: Dict[str, object]) -> Optional[str]:
    d = rec.get("date") or rec.get("captured_at") or rec.get("start_date")
    if not d:
        return None
    return str(d)[:10]


def _normalize_value(rec: Dict[str, object]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """Return ``(value_conventional, unit_conventional, marker_key)`` for a record.

    Uses ``lab_normalization.normalize_marker`` so SI values and comma decimals
    are canonicalized first. Unrecognised markers come back ``(value, unit, None)``.
    """
    name = _marker_name(rec)
    normalized = lab_normalization.normalize_marker(name, rec.get("value"), rec.get("unit"))
    if normalized is None:
        value, _qual = lab_normalization.parse_numeric(rec.get("value"))
        unit = lab_normalization.canonical_unit(rec.get("unit"))
        return value, unit, None
    return (
        normalized["value"],  # type: ignore[return-value]
        str(normalized["unit"]) if normalized["unit"] is not None else None,
        str(normalized["marker_key"]),
    )


# --- marker history + trend -------------------------------------------------

# A value "improves" when it moves toward its optimal band. We read the band's
# direction from clinical_optima so the trend label is correct per marker
# (lower LDL improves; higher HDL improves; a range marker improves toward the
# middle).

TREND_IMPROVING = "improving"
TREND_WORSENING = "worsening"
TREND_STABLE = "stable"
TREND_UNKNOWN = "unknown"


def _optimal_midpoint(opt: clinical_optima.OptimalRange, sex: Optional[str]) -> Optional[float]:
    low, high = clinical_optima._resolve_optimal_bounds(opt, sex)
    if low is not None and high is not None:
        return (low + high) / 2.0
    if high is not None:  # lower-is-better: target is the ceiling
        return high
    if low is not None:  # higher-is-better: target is the floor
        return low
    return None


def _trend_direction(
    marker_key: str,
    prev: Optional[float],
    last: Optional[float],
    sex: Optional[str],
    *,
    stable_eps: float = 1e-9,
) -> str:
    """Direction of the last step relative to the marker's optimal target.

    Returns improving / worsening / stable / unknown. "Improving" means the value
    moved closer to (or stayed inside) the optimal band; "worsening" means it
    moved away. With no optimal range defined we cannot say, so ``unknown``.
    """
    if prev is None or last is None:
        return TREND_UNKNOWN
    opt = clinical_optima.get_optimal_range(marker_key)
    if opt is None:
        return TREND_UNKNOWN
    target = _optimal_midpoint(opt, sex)
    if target is None:
        return TREND_UNKNOWN
    prev_dist = abs(prev - target)
    last_dist = abs(last - target)
    if abs(last_dist - prev_dist) <= stable_eps:
        return TREND_STABLE
    return TREND_IMPROVING if last_dist < prev_dist else TREND_WORSENING


def marker_history(
    records: List[Dict[str, object]],
    marker: str,
    sex: Optional[str] = None,
) -> Dict[str, object]:
    """Chronology of one marker across records, with trend and last delta.

    Returns:
    - ``marker_key`` / ``display_name`` / ``unit`` — resolved identity (or the
      raw name + ``marker_key=None`` if unrecognised).
    - ``points`` — ``[{date, value, unit, qualifier}]`` sorted by date ascending
      (undated points kept at the front, in input order, so nothing is dropped).
    - ``latest`` / ``previous`` — the two most recent values.
    - ``delta`` — ``latest - previous`` (None if fewer than two points).
    - ``trend`` — improving / worsening / stable / unknown vs the optimal target.
    - ``optimal_status`` — where the latest value sits vs the optimal band.
    """
    spec = reference_ranges.match_marker(marker)
    marker_key = spec.key if spec else None
    display_name = spec.display_name if spec else marker
    unit = spec.unit if spec else None

    points: List[Dict[str, object]] = []
    for rec in records:
        value, rec_unit, rec_key = _normalize_value(rec)
        # Keep only rows that resolve to the same marker we asked for.
        if marker_key is not None:
            if rec_key != marker_key:
                continue
        elif _marker_name(rec).strip().lower() != marker.strip().lower():
            continue
        if value is None:
            continue
        _v, qual = lab_normalization.parse_numeric(rec.get("value"))
        points.append({
            "date": _record_date(rec),
            "value": value,
            "unit": rec_unit or unit,
            "qualifier": qual,
        })

    # Sort: dated points by date ascending; undated points stay first in order.
    dated = sorted([p for p in points if p["date"]], key=lambda p: str(p["date"]))
    undated = [p for p in points if not p["date"]]
    ordered = undated + dated

    latest = ordered[-1]["value"] if ordered else None
    previous = ordered[-2]["value"] if len(ordered) >= 2 else None
    delta = None
    if isinstance(latest, (int, float)) and isinstance(previous, (int, float)):
        delta = round(latest - previous, 6)

    trend = _trend_direction(
        str(marker_key), previous if isinstance(previous, (int, float)) else None,
        latest if isinstance(latest, (int, float)) else None, sex,
    ) if marker_key else TREND_UNKNOWN

    optimal_status = None
    if marker_key and isinstance(latest, (int, float)):
        opt = clinical_optima.get_optimal_range(marker_key)
        if opt is not None:
            optimal_status = clinical_optima.classify_optimal(opt, latest, sex=sex)

    return {
        "marker_key": marker_key,
        "display_name": display_name,
        "unit": unit,
        "points": ordered,
        "latest": latest,
        "previous": previous,
        "delta": delta,
        "trend": trend,
        "optimal_status": optimal_status,
        "disclaimer": OPTIMUM_DISCLAIMER,
    }


# --- panels -----------------------------------------------------------------
#
# Clinically meaningful groupings. Each panel lists the marker slugs (from
# reference_ranges.MARKERS) that belong to it. A panel's status summarizes the
# member markers that have data.

PANELS: Dict[str, Dict[str, object]] = {
    "lipids": {
        "label_ru": "Lipids",
        "markers": ["ldl", "hdl", "triglycerides", "total_cholesterol"],
        "discuss_ru": "Lipids are read together (LDL/HDL/TG/total), not one at a time; "
                      "discuss cardiovascular risk with a clinician.",
    },
    "glycemia": {
        "label_ru": "Glycemia",
        "markers": ["glucose", "hba1c"],
        "discuss_ru": "Fasting glucose and HbA1c together show carbohydrate metabolism; "
                      "insulin adds the insulin-resistance picture (when it was measured).",
    },
    "iron": {
        "label_ru": "Iron",
        "markers": ["ferritin"],
        "discuss_ru": "Ferritin is an acute-phase protein, so read it together with CRP; "
                      "inflammation pushes it up.",
    },
    "thyroid": {
        "label_ru": "Thyroid",
        "markers": ["tsh"],
        "discuss_ru": "TSH is a screening marker; T3/T4 refine the picture when it is off.",
    },
    "inflammation": {
        "label_ru": "Inflammation",
        "markers": ["crp"],
        "discuss_ru": "CRP reflects acute/chronic inflammation; "
                      "an acute illness makes the result uninformative.",
    },
    "vitamins": {
        "label_ru": "Vitamins",
        "markers": ["vitamin_d", "b12"],
        "discuss_ru": "Vitamin D and B12 deficiencies are common and correctable; "
                      "discuss dosing and re-testing with a clinician.",
    },
    "kidney": {
        "label_ru": "Kidney (basic)",
        "markers": ["creatinine", "sodium", "potassium"],
        "discuss_ru": "Creatinine + electrolytes are a basic look at kidney function; "
                      "eGFR needs a calculation based on age/sex.",
    },
}

PANEL_ALL_OPTIMAL = "all_optimal"      # every measured marker is in the optimal band
PANEL_HAS_OFF = "has_off_target"       # at least one measured marker is off target
PANEL_NO_DATA = "no_data"              # no records for any marker in this panel


def panel_summary(
    records: List[Dict[str, object]],
    sex: Optional[str] = None,
) -> List[Dict[str, object]]:
    """Summarize each clinical panel from a record set.

    For every panel: which member markers have data, their latest value +
    reference flag + optimal status, the panel-level status (all optimal / has
    off-target / no data), and a non-diagnostic ``what_to_discuss`` line.
    """
    out: List[Dict[str, object]] = []
    for panel_key, panel in PANELS.items():
        marker_rows: List[Dict[str, object]] = []
        any_data = False
        any_off = False
        for marker_key in panel["markers"]:  # type: ignore[index]
            spec = reference_ranges.MARKERS[marker_key]
            hist = marker_history(records, spec.display_name, sex=sex)
            latest = hist["latest"]
            if not isinstance(latest, (int, float)):
                marker_rows.append({
                    "marker_key": marker_key,
                    "display_name": spec.display_name,
                    "value": None,
                    "has_data": False,
                })
                continue
            any_data = True
            assessment = clinical_optima.assess_optima(
                spec.display_name, float(latest), sex=sex,
            )
            opt_status = assessment["optimal_status"] if assessment else None
            ref_flag = assessment["reference_status"] if assessment else "unknown"
            off = ref_flag in ("low", "high") or opt_status in (SUBOPTIMAL_LOW, SUBOPTIMAL_HIGH)
            if off:
                any_off = True
            marker_rows.append({
                "marker_key": marker_key,
                "display_name": spec.display_name,
                "value": latest,
                "unit": hist["unit"],
                "has_data": True,
                "reference_status": ref_flag,
                "optimal_status": opt_status,
                "trend": hist["trend"],
                "red_flag": assessment["red_flag"] if assessment else None,
            })

        if not any_data:
            status = PANEL_NO_DATA
        elif any_off:
            status = PANEL_HAS_OFF
        else:
            status = PANEL_ALL_OPTIMAL

        out.append({
            "panel": panel_key,
            "label_ru": panel["label_ru"],
            "status": status,
            "markers": marker_rows,
            "what_to_discuss": panel["discuss_ru"],
            "disclaimer": OPTIMUM_DISCLAIMER,
        })
    return out


# --- derived indices --------------------------------------------------------
#
# Ratios / combinations a single marker cannot express. Each carries its formula,
# a cautious interpretation, a confidence grade, and the standing "discuss"
# framing. None of these is a diagnosis.


def _latest_values(records: List[Dict[str, object]], sex: Optional[str]) -> Dict[str, float]:
    """Map of marker_key -> latest numeric value across the record set."""
    out: Dict[str, float] = {}
    for marker_key, spec in reference_ranges.MARKERS.items():
        hist = marker_history(records, spec.display_name, sex=sex)
        if isinstance(hist["latest"], (int, float)):
            out[marker_key] = float(hist["latest"])
    return out


def _index(
    key: str, label_ru: str, value: Optional[float], unit: str,
    formula: str, interpretation_ru: str, confidence: Confidence,
) -> Dict[str, object]:
    return {
        "index": key,
        "label_ru": label_ru,
        "value": None if value is None else round(value, 3),
        "unit": unit,
        "formula": formula,
        "interpretation_ru": interpretation_ru,
        "confidence": confidence.value,
        "discuss_ru": DISCUSS,
    }


def derived_indices(
    records: List[Dict[str, object]],
    sex: Optional[str] = None,
) -> List[Dict[str, object]]:
    """Compute lipid / insulin-resistance derived indices from latest values.

    Only emits an index when its required markers are present. Each result
    carries the literal formula, a cautious interpretation, and a C-grade.
    """
    v = _latest_values(records, sex)
    out: List[Dict[str, object]] = []

    # LDL/HDL ratio — atherogenic balance.
    if "ldl" in v and "hdl" in v and v["hdl"] > 0:
        ratio = v["ldl"] / v["hdl"]
        if ratio < 2.0:
            interp = "Below ~2.0 is usually considered a favourable balance."
        elif ratio < 3.5:
            interp = "Borderline zone (~2.0-3.5); read it alongside overall risk."
        else:
            interp = "Above ~3.5 is associated with elevated risk; discuss with a clinician."
        out.append(_index(
            "ldl_hdl_ratio", "LDL / HDL", ratio, "ratio",
            "LDL ÷ HDL", interp, Confidence.C3,
        ))

    # TG/HDL ratio — surrogate for insulin resistance (mg/dL convention).
    if "triglycerides" in v and "hdl" in v and v["hdl"] > 0:
        ratio = v["triglycerides"] / v["hdl"]
        if ratio < 2.0:
            interp = "Below ~2.0 makes insulin resistance unlikely (mg/dL convention)."
        elif ratio < 3.0:
            interp = "Borderline zone (~2.0-3.0); read it alongside glucose/HbA1c."
        else:
            interp = "Above ~3.0 is associated with insulin resistance; discuss with a clinician."
        out.append(_index(
            "tg_hdl_ratio", "TG / HDL", ratio, "ratio (mg/dL)",
            "Triglycerides ÷ HDL (in mg/dL)", interp, Confidence.C3,
        ))

    # Non-HDL cholesterol — total minus HDL.
    if "total_cholesterol" in v and "hdl" in v:
        non_hdl = v["total_cholesterol"] - v["hdl"]
        if non_hdl < 130.0:
            interp = "Below ~130 mg/dL is usually considered desirable."
        elif non_hdl < 160.0:
            interp = "Borderline zone (~130-160 mg/dL)."
        else:
            interp = "Above ~160 mg/dL — discuss the risk with a clinician."
        out.append(_index(
            "non_hdl_cholesterol", "Non-HDL cholesterol", non_hdl, "mg/dL",
            "Total cholesterol − HDL", interp, Confidence.C3,
        ))

    # HOMA-IR — fasting glucose (mg/dL) * insulin (uIU/mL) / 405.
    if "glucose" in v and "insulin" in v:
        homa = v["glucose"] * v["insulin"] / 405.0
        if homa < 1.0:
            interp = "Below ~1.0 usually suggests good insulin sensitivity."
        elif homa < 2.0:
            interp = "An early signal (~1.0-2.0); watch how it moves over time."
        else:
            interp = "Above ~2.0 is associated with insulin resistance; discuss with a clinician."
        out.append(_index(
            "homa_ir", "HOMA-IR", homa, "index",
            "Glucose(mg/dL) × Insulin(uIU/mL) ÷ 405", interp, Confidence.C3,
        ))

    return out


# --- re-test cadence --------------------------------------------------------
#
# Common-practice re-test intervals (months). Orientation only, C3: actual
# cadence depends on the clinician and the person's risk.

RECHECK_MONTHS: Dict[str, int] = {
    "ldl": 12, "hdl": 12, "triglycerides": 12, "total_cholesterol": 12,
    "glucose": 12, "hba1c": 6,
    "vitamin_d": 6, "b12": 12,
    "ferritin": 9, "tsh": 12, "crp": 12,
    "creatinine": 12, "sodium": 12, "potassium": 12,
}

# Days per month for the simple age math (no calendar dependency needed).
_DAYS_PER_MONTH = 30.4


def _days_between(last_date: str, today: str) -> Optional[int]:
    """Whole days between two ``YYYY-MM-DD`` strings, or None if unparseable.

    Uses ``datetime`` from the stdlib only when both dates parse.
    """
    from datetime import date as _date

    try:
        a = _date.fromisoformat(last_date[:10])
        b = _date.fromisoformat(today[:10])
    except ValueError:
        return None
    return (b - a).days


def next_checkup_hint(
    marker: str,
    last_date: Optional[str],
    today: Optional[str] = None,
) -> Dict[str, object]:
    """Re-test cadence hint for a marker given when it was last measured.

    Returns the recommended interval (months), how many days have passed, and a
    ``due`` flag (True when the value is older than the interval). When the marker
    is unknown or there is no recommended interval, ``interval_months`` is None and
    ``due`` is None (we do not guess). Dates are ``YYYY-MM-DD``.
    """
    from datetime import date as _date

    spec = reference_ranges.match_marker(marker)
    marker_key = spec.key if spec else None
    interval = RECHECK_MONTHS.get(marker_key) if marker_key else None

    today = today or _date.today().isoformat()
    days = _days_between(last_date, today) if last_date else None

    due: Optional[bool] = None
    note: str
    if interval is None:
        note = "There is no standard re-test interval for this marker."
    elif days is None:
        note = f"Suggested interval ~{interval} mo.; the date of the last test is unknown."
    else:
        threshold_days = interval * _DAYS_PER_MONTH
        due = days >= threshold_days
        if due:
            note = (f"~{days} days have passed (≥ {interval} mo.) — worth discussing a "
                    f"re-test with a clinician.")
        else:
            months_left = max(0, round((threshold_days - days) / _DAYS_PER_MONTH, 1))
            note = (f"~{days} days have passed; ~{months_left} mo. still to go before the "
                    f"typical ~{interval} mo. interval.")

    return {
        "marker_key": marker_key,
        "display_name": spec.display_name if spec else marker,
        "interval_months": interval,
        "days_since": days,
        "last_date": last_date,
        "due": due,
        "note_ru": note,
        "confidence": Confidence.C3.value,
    }
