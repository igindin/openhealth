"""Fast, science-based insight detectors over daily health series.

This is the "find the real problem" layer that sits on top of the raw daily
metrics (recovery, HRV, RHR, sleep hours, strain). Each detector turns a
described, numeric observation in the *user's own* data into a cautious
``Insight``: a finding phrased as a question with one concrete next step.

Honesty rules (canon: ``openhealth.evidence``)
----------------------------------------------
- Every finding here is a *personal pattern*, so its confidence is capped at
  **C2 ("weak signal")** until it survives an n-of-1 protocol. See
  ``openhealth.protocols``. Sparse data drops it to C1.
- ``severity`` (info | attention | warning) is a *separate axis* from
  ``confidence``: it says how loud the signal is in the data, not how sure we
  are about the cause. A loud signal we can't yet explain is a high-severity,
  low-confidence finding.
- Personal baselines, never population norms.
- ``warning`` findings always carry a "if symptoms accompany this, see a
  clinician" disclaimer. This is not diagnosis and not treatment advice.

Pure stdlib. The thresholds are constants with the reasoning inline so they can
be reviewed and tuned.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any, Dict, List, Optional, Tuple

from . import evidence, params

# --- severity ----------------------------------------------------------------

INFO = "info"
ATTENTION = "attention"
WARNING = "warning"

# Sort order: loudest first. Used by detect_insights() and protocol building.
_SEVERITY_RANK = {WARNING: 0, ATTENTION: 1, INFO: 2}

# Appended to the action of any WARNING finding. The system never diagnoses;
# a loud signal that could be illness must point at a human clinician.
WARNING_DISCLAIMER = (
    "If this comes together with symptoms (fever, pain, shortness of breath, "
    "lasting weakness), that is a reason to see a doctor rather than to run an "
    "experiment on yourself."
)

# --- thresholds (documented, tunable) ---------------------------------------

# Sleep debt. Default nightly target if the user gives no goal. 5h/week of
# accumulated shortfall (~43 min/night) is where short-sleep effects on
# autonomic recovery start showing up consistently in restriction studies.
DEFAULT_SLEEP_GOAL_H = 8.0
SLEEP_DEBT_WEEK_ATTENTION_H = 5.0   # >= 5h shortfall over 7 nights
SLEEP_DEBT_WEEK_WARNING_H = 10.0    # >= 10h (~1.4h/night) = marked chronic deficit

# HRV downtrend: 7d mean vs personal 14-28d baseline. A sustained drop in the
# HRV trend reflects rising load / under-recovery; 8% / 15% are pragmatic
# personal-trend bands (WHOOP/Welltory-style relative read, not a norm).
HRV_DROP_ATTENTION_PCT = 8.0
HRV_DROP_WARNING_PCT = 15.0

# RHR uptrend: 7d mean above baseline. A few bpm of sustained elevation is a
# classic early marker of stress / incipient illness / overreaching.
RHR_RISE_ATTENTION_BPM = 3.0
RHR_RISE_WARNING_BPM = 6.0

# Recovery "red" zone matches the dashboard's col()/word() helper (< 34).
RECOVERY_RED_MAX = 34
RED_STREAK_DAYS = 3                 # >= 3 red days in a row -> warning

# Strain/recovery mismatch: a hard day stacked on a depleted body.
STRAIN_HIGH = 14.0                  # WHOOP strain 0-21; >= 14 is a hard day
RECOVERY_LOW_FOR_STRAIN = 50        # recovery below this = body not ready
MISMATCH_WINDOW_DAYS = 14
MISMATCH_ATTENTION_COUNT = 2        # repeated twice in 14d = a pattern
MISMATCH_WARNING_COUNT = 3

# Weekday vs weekend systematic gap (recovery). Capped at C2 by spec.
WEEKEND_DIFF_POINTS = 5.0

# Sleep consistency: regularity matters more than hitting an ideal once.
SLEEP_CONSISTENCY_STDEV_H = 1.2

# Window sizes for trend detectors.
RECENT_WINDOW = 7
BASELINE_LO = 7                     # baseline excludes the most recent 7 days
BASELINE_HI = 28                    # ...and reaches back to 28 days
MIN_RECENT_POINTS = 5
MIN_BASELINE_POINTS = 7


# --- Insight record ----------------------------------------------------------

@dataclass
class Insight:
    """One cautious finding. Phrased as a prompt for review, never a verdict."""

    id: str
    title_ru: str
    severity: str                       # info | attention | warning
    confidence: evidence.Confidence     # capped at C2 for personal patterns
    evidence_text: str                  # the numbers, in plain language
    question_ru: str                    # "what to ask yourself"
    action_ru: str                      # one concrete next step
    metric: str = ""                    # primary metric this is about
    data: Dict[str, Any] = field(default_factory=dict)  # structured numbers
    refs: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Every warning carries the see-a-clinician disclaimer exactly once.
        if self.severity == WARNING and WARNING_DISCLAIMER not in self.action_ru:
            self.action_ru = "%s %s" % (self.action_ru.rstrip(), WARNING_DISCLAIMER)

    def to_dict(self) -> Dict[str, Any]:
        meta = evidence.CONFIDENCE_META[self.confidence]
        return {
            "id": self.id,
            "title_ru": self.title_ru,
            "severity": self.severity,
            "confidence": self.confidence.value,
            "confidence_label": meta["label"],
            "evidence_text": self.evidence_text,
            "question_ru": self.question_ru,
            "action_ru": self.action_ru,
            "metric": self.metric,
            "data": self.data,
            "refs": list(self.refs),
        }


# --- helpers -----------------------------------------------------------------

def _series(daily: Dict[str, Dict[str, Any]], key: str) -> List[Tuple[str, float]]:
    """Ascending [(date, value)] for one metric, skipping missing/None days."""
    out: List[Tuple[str, float]] = []
    for d in sorted(daily):
        row = daily.get(d) or {}
        v = row.get(key)
        if v is None:
            continue
        try:
            out.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    return out


def _recent_and_baseline(
    values: List[float],
) -> Tuple[List[float], List[float]]:
    """Split a chronological series into (recent 7d, 14-28d baseline)."""
    recent = values[-RECENT_WINDOW:]
    baseline = values[-BASELINE_HI:-BASELINE_LO] if len(values) > BASELINE_LO else []
    return recent, baseline


def _grade(strong: bool) -> evidence.Confidence:
    """Confidence for a personal pattern: C2 with ample data, else C1.

    Both run through ``cap_personal_pattern`` so the C2 ceiling for un-validated
    personal patterns is enforced in one place (canon).
    """
    raw = evidence.Confidence.C2 if strong else evidence.Confidence.C1
    return evidence.cap_personal_pattern(raw, validated_switches=0)


def _fmt(x: float, nd: int = 0) -> str:
    return ("%.*f" % (nd, x)).rstrip("0").rstrip(".") if nd else "%.0f" % x


def _param(param_id: str, fallback: float) -> float:
    """Effective (user-tunable) threshold; falls back to the module constant."""
    try:
        return params.get(param_id)
    except Exception:
        return fallback


def _with_trace(
    data: Dict[str, Any],
    threshold_used: float,
    observed_value: float,
    window: str,
    param_ids: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Attach the "how was this computed" trace (UI tooltip food).

    ``trace`` carries the threshold that fired, the observed value it was
    compared against and the data window. When any of the detector's params is
    user-overridden, ``params_overrides`` makes that visible on the record.
    """
    data["trace"] = {
        "threshold_used": threshold_used,
        "observed_value": observed_value,
        "window": window,
    }
    try:
        overrides = params.overrides_for(param_ids) if param_ids else {}
    except Exception:
        overrides = {}
    if overrides:
        data["params_overrides"] = overrides
    return data


# --- detectors ---------------------------------------------------------------

def detect_sleep_debt(
    daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]
) -> Optional[Insight]:
    series = _series(daily, "sleep_h")
    if len(series) < MIN_RECENT_POINTS:
        return None
    goal = float(goals.get("sleep_h", _param("insights.sleep_goal_h", DEFAULT_SLEEP_GOAL_H)))
    attention_h = _param("insights.sleep_debt_week_attention_h", SLEEP_DEBT_WEEK_ATTENTION_H)
    warning_h = _param("insights.sleep_debt_week_warning_h", SLEEP_DEBT_WEEK_WARNING_H)
    last7 = [v for _, v in series[-7:]]
    last14 = [v for _, v in series[-14:]]
    debt7 = sum(max(0.0, goal - v) for v in last7)
    debt14 = sum(max(0.0, goal - v) for v in last14)
    if debt7 < attention_h:
        return None
    severity = WARNING if debt7 >= warning_h else ATTENTION
    mean7 = statistics.mean(last7)
    return Insight(
        id="insight-sleep_debt",
        title_ru="Accumulated sleep debt",
        severity=severity,
        confidence=_grade(len(last7) >= 6),
        evidence_text=(
            "Over the last %d nights with data, the shortfall against your %sh goal "
            "added up to %sh (on average %sh per night). Over 14 days - %sh."
            % (len(last7), _fmt(goal, 1), _fmt(debt7, 1), _fmt(mean7, 1), _fmt(debt14, 1))
        ),
        question_ru="What is getting in the way of going to bed on time this week - late screens, work, stress?",
        action_ru="Tonight, start winding down for bed 30-45 minutes earlier than usual.",
        metric="sleep_h",
        data=_with_trace(
            {
                "kind": "sleep_debt",
                "goal_h": round(goal, 1),
                "debt7_h": round(debt7, 1),
                "debt14_h": round(debt14, 1),
                "mean7_h": round(mean7, 1),
            },
            threshold_used=warning_h if severity == WARNING else attention_h,
            observed_value=round(debt7, 1),
            window="7d",
            param_ids=(
                "insights.sleep_goal_h",
                "insights.sleep_debt_week_attention_h",
                "insights.sleep_debt_week_warning_h",
            ),
        ),
        refs=["Sleep deficit lowers parasympathetic tone and recovery "
              "(sleep-restriction literature)."],
    )


def detect_hrv_downtrend(daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]) -> Optional[Insight]:
    series = _series(daily, "hrv")
    values = [v for _, v in series]
    recent, baseline = _recent_and_baseline(values)
    if len(recent) < MIN_RECENT_POINTS or len(baseline) < MIN_BASELINE_POINTS:
        return None
    base_mean = statistics.mean(baseline)
    if base_mean <= 0:
        return None
    recent_mean = statistics.mean(recent)
    attention_pct = _param("insights.hrv_drop_attention_pct", HRV_DROP_ATTENTION_PCT)
    warning_pct = _param("insights.hrv_drop_warning_pct", HRV_DROP_WARNING_PCT)
    drop_pct = (base_mean - recent_mean) / base_mean * 100.0
    if drop_pct < attention_pct:
        return None
    severity = WARNING if drop_pct >= warning_pct else ATTENTION
    return Insight(
        id="insight-hrv_downtrend",
        title_ru="HRV below your personal baseline",
        severity=severity,
        confidence=_grade(len(baseline) >= 14),
        evidence_text=(
            "7-day mean HRV is %s ms against your baseline of %s ms (14-28 days) - "
            "lower by %s%%." % (_fmt(recent_mean), _fmt(base_mean), _fmt(drop_pct))
        ),
        question_ru="What changed over the last 1-2 weeks - training load, sleep, alcohol, illness or stress?",
        action_ru="Take 2-3 easy days and watch whether HRV comes back to baseline.",
        metric="hrv",
        data=_with_trace(
            {
                "kind": "hrv_downtrend",
                "recent_mean": round(recent_mean, 1),
                "baseline_mean": round(base_mean, 1),
                "drop_pct": round(drop_pct, 1),
            },
            threshold_used=warning_pct if severity == WARNING else attention_pct,
            observed_value=round(drop_pct, 1),
            window="7d vs 14-28d",
            param_ids=("insights.hrv_drop_attention_pct", "insights.hrv_drop_warning_pct"),
        ),
        refs=["A falling HRV trend reflects rising load and under-recovery "
              "(HRV monitoring literature)."],
    )


def detect_rhr_uptrend(daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]) -> Optional[Insight]:
    series = _series(daily, "rhr")
    values = [v for _, v in series]
    recent, baseline = _recent_and_baseline(values)
    if len(recent) < MIN_RECENT_POINTS or len(baseline) < MIN_BASELINE_POINTS:
        return None
    base_mean = statistics.mean(baseline)
    recent_mean = statistics.mean(recent)
    attention_bpm = _param("insights.rhr_rise_attention_bpm", RHR_RISE_ATTENTION_BPM)
    warning_bpm = _param("insights.rhr_rise_warning_bpm", RHR_RISE_WARNING_BPM)
    rise = recent_mean - base_mean
    if rise < attention_bpm:
        return None
    severity = WARNING if rise >= warning_bpm else ATTENTION
    return Insight(
        id="insight-rhr_uptrend",
        title_ru="Resting heart rate above baseline",
        severity=severity,
        confidence=_grade(len(baseline) >= 14),
        evidence_text=(
            "7-day resting heart rate is %s bpm against a baseline of %s bpm - "
            "higher by %s bpm." % (_fmt(recent_mean), _fmt(base_mean), _fmt(rise, 1))
        ),
        question_ru="Was there short sleep, alcohol, the start of an illness or a jump in load on those days?",
        action_ru="Watch it for 3-4 days and add recovery; if the rate stays high, reduce the load.",
        metric="rhr",
        data=_with_trace(
            {
                "kind": "rhr_uptrend",
                "recent_mean": round(recent_mean, 1),
                "baseline_mean": round(base_mean, 1),
                "rise_bpm": round(rise, 1),
            },
            threshold_used=warning_bpm if severity == WARNING else attention_bpm,
            observed_value=round(rise, 1),
            window="7d vs 14-28d",
            param_ids=("insights.rhr_rise_attention_bpm", "insights.rhr_rise_warning_bpm"),
        ),
        refs=["A sustained rise in resting heart rate is an early marker of stress, "
              "illness or overreaching."],
    )


def detect_recovery_red_streak(daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]) -> Optional[Insight]:
    series = _series(daily, "recovery")
    streak_days = int(_param("insights.red_streak_days", RED_STREAK_DAYS))
    if len(series) < streak_days:
        return None
    # Longest run of consecutive (by available points) red days, recent-weighted.
    best_len = 0
    best_end = 0
    cur = 0
    for i, (_, v) in enumerate(series):
        if v < RECOVERY_RED_MAX:
            cur += 1
            if cur >= best_len:
                best_len = cur
                best_end = i
        else:
            cur = 0
    if best_len < streak_days:
        return None
    streak = series[best_end - best_len + 1: best_end + 1]
    vals = ", ".join(_fmt(v) for _, v in streak)
    span = "%s - %s" % (streak[0][0], streak[-1][0])
    return Insight(
        id="insight-recovery_red_streak",
        title_ru="Run of red recovery days",
        severity=WARNING,
        confidence=_grade(True),
        evidence_text=(
            "%d days in a row with recovery in the red zone (<%d): %s (%s)."
            % (best_len, RECOVERY_RED_MAX, vals, span)
        ),
        question_ru="Does this line up with illness, heavy stress or a sharp increase in load?",
        action_ru="Make sleep and rest the priority today, with no intense training.",
        metric="recovery",
        data=_with_trace(
            {
                "kind": "recovery_red_streak",
                "streak_len": best_len,
                "values": [v for _, v in streak],
                "span": span,
            },
            threshold_used=streak_days,
            observed_value=best_len,
            window="%dd series" % len(series),
            param_ids=("insights.red_streak_days",),
        ),
    )


def detect_strain_recovery_mismatch(daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]) -> Optional[Insight]:
    dates = sorted(daily)[-MISMATCH_WINDOW_DAYS:]
    hits: List[str] = []
    for d in dates:
        row = daily.get(d) or {}
        strain = row.get("strain")
        recovery = row.get("recovery")
        if strain is None or recovery is None:
            continue
        if float(strain) >= STRAIN_HIGH and float(recovery) < RECOVERY_LOW_FOR_STRAIN:
            hits.append(d)
    if len(hits) < MISMATCH_ATTENTION_COUNT:
        return None
    severity = WARNING if len(hits) >= MISMATCH_WARNING_COUNT else ATTENTION
    return Insight(
        id="insight-strain_recovery_mismatch",
        title_ru="Hard load on top of low recovery",
        severity=severity,
        confidence=_grade(len(hits) >= MISMATCH_WARNING_COUNT),
        evidence_text=(
            "Over %d days, hard load landed on a low-recovery day %d times "
            "(strain >= %s on a day with recovery < %d): %s."
            % (MISMATCH_WINDOW_DAYS, len(hits), _fmt(STRAIN_HIGH),
               RECOVERY_LOW_FOR_STRAIN, ", ".join(hits))
        ),
        question_ru="Are workouts following how your body is doing, or the calendar regardless?",
        action_ru="On days with recovery < 50, swap intense work for light activity; decide in the morning based on recovery.",
        metric="recovery",
        data=_with_trace(
            {
                "kind": "strain_recovery_mismatch",
                "count": len(hits),
                "dates": hits,
                "strain_high": STRAIN_HIGH,
                "recovery_low": RECOVERY_LOW_FOR_STRAIN,
            },
            threshold_used=MISMATCH_WARNING_COUNT if severity == WARNING else MISMATCH_ATTENTION_COUNT,
            observed_value=len(hits),
            window="%dd" % MISMATCH_WINDOW_DAYS,
        ),
    )


def detect_weekend_pattern(daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]) -> Optional[Insight]:
    weekday_vals: List[float] = []
    weekend_vals: List[float] = []
    for d, row in daily.items():
        v = (row or {}).get("recovery")
        if v is None:
            continue
        try:
            wd = _date.fromisoformat(d).weekday()
        except ValueError:
            continue
        (weekend_vals if wd >= 5 else weekday_vals).append(float(v))
    if len(weekday_vals) < 2 or len(weekend_vals) < 2:
        return None
    wkday = statistics.mean(weekday_vals)
    wkend = statistics.mean(weekend_vals)
    diff_points = _param("insights.weekend_diff_points", WEEKEND_DIFF_POINTS)
    diff = wkday - wkend  # positive => weekends dip
    if abs(diff) < diff_points:
        return None
    dip = diff > 0
    # Capped at C2 by spec; this is a coarse calendar split.
    conf = _grade(len(weekend_vals) >= 4 and len(weekday_vals) >= 4)
    return Insight(
        id="insight-weekend_pattern",
        title_ru="Recovery dips on weekends" if dip else "Recovery is higher on weekends",
        severity=ATTENTION if dip else INFO,
        confidence=conf,
        evidence_text=(
            "Recovery averages %s on weekends against %s on weekdays (a gap of %s points)."
            % (_fmt(wkend), _fmt(wkday), _fmt(abs(diff)))
        ),
        question_ru="What is different on weekends - alcohol, a later bedtime, a shifted schedule?"
        if dip else "What is it about weekdays that holds recovery back compared to weekends?",
        action_ru="Try keeping the same bedtime on weekends as on weekdays and compare recovery."
        if dip else "Carry what works on weekends (sleep, the pace of the day) over into weekdays.",
        metric="recovery",
        data=_with_trace(
            {
                "kind": "weekend_pattern",
                "weekday_mean": round(wkday, 1),
                "weekend_mean": round(wkend, 1),
                "diff": round(diff, 1),
                "dip": dip,
            },
            threshold_used=diff_points,
            observed_value=round(abs(diff), 1),
            window="weekdays vs weekends",
            param_ids=("insights.weekend_diff_points",),
        ),
    )


def detect_sleep_consistency(daily: Dict[str, Dict[str, Any]], goals: Dict[str, Any]) -> Optional[Insight]:
    series = _series(daily, "sleep_h")
    values = [v for _, v in series[-14:]]
    if len(values) < MIN_RECENT_POINTS:
        return None
    sd = statistics.pstdev(values)
    stdev_threshold = _param("insights.sleep_consistency_stdev_h", SLEEP_CONSISTENCY_STDEV_H)
    if sd <= stdev_threshold:
        return None
    return Insight(
        id="insight-sleep_consistency",
        title_ru="Inconsistent sleep duration",
        severity=ATTENTION,
        confidence=_grade(len(values) >= 10),
        evidence_text=(
            "Sleep duration varies widely across %d nights - standard deviation "
            "%sh (from %s to %sh). Consistency matters more than one perfect night."
            % (len(values), _fmt(sd, 1), _fmt(min(values), 1), _fmt(max(values), 1))
        ),
        question_ru="Could you narrow the spread of bedtime and wake time, even if total duration is not ideal?",
        action_ru="Fix your wake time for 14 days (within a 30-minute window), weekends included.",
        metric="sleep_h",
        data=_with_trace(
            {
                "kind": "sleep_consistency",
                "stdev_h": round(sd, 2),
                "min_h": round(min(values), 1),
                "max_h": round(max(values), 1),
                "n": len(values),
            },
            threshold_used=stdev_threshold,
            observed_value=round(sd, 2),
            window="14 nights",
            param_ids=("insights.sleep_consistency_stdev_h",),
        ),
        refs=["Sleep regularity is tied to health outcomes more strongly than any "
              "single night's duration (sleep regularity literature)."],
    )


# Ordered: trend/medical-leaning detectors first, calendar pattern last.
_DETECTORS = (
    detect_sleep_debt,
    detect_hrv_downtrend,
    detect_rhr_uptrend,
    detect_recovery_red_streak,
    detect_strain_recovery_mismatch,
    detect_weekend_pattern,
    detect_sleep_consistency,
)


def detect_insights(
    daily: Dict[str, Dict[str, Any]], goals: Optional[Dict[str, Any]] = None
) -> List[Insight]:
    """Run all detectors over the daily series.

    ``daily`` maps ISO date -> {recovery, hrv, rhr, sleep_h, strain} (any
    subset). ``goals`` is optional (e.g. {"sleep_h": 7.5}). Returns insights
    sorted by severity (warning first), then by confidence. Empty / malformed
    input yields an empty list, never an exception.
    """
    if not daily:
        return []
    g = goals or {}
    found: List[Insight] = []
    for det in _DETECTORS:
        try:
            ins = det(daily, g)
        except Exception:
            # A single bad detector must never sink the whole pass.
            ins = None
        if ins is not None:
            found.append(ins)
    found.sort(
        key=lambda i: (
            _SEVERITY_RANK.get(i.severity, 9),
            -evidence.confidence_to_numeric(i.confidence),
        )
    )
    return found


def insights_to_dicts(insights: List[Insight]) -> List[Dict[str, Any]]:
    """Convenience: serialize a list of insights for JSON / the dashboard."""
    return [i.to_dict() for i in insights]
