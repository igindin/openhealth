"""Correlations module — personal behavior <-> recovery impact ("what affects you").

This is the payoff of the journal + recovery loop: for each behavior the person
logs, compare their average recovery on *yes* days against *no* days over a
personal baseline window (60-90 days), the same idea as WHOOP's "Impacts".

Guardrails, on purpose:

- A behavior is only analyzed once it has **at least 5 yes days and 5 no days**
  in the window (mirrors WHOOP's threshold). Below that, signal is too thin.
- Each result is a *personal pattern*, so confidence is capped via
  ``openhealth.evidence``: a raw association is at best a weak signal (C2). It
  can rise to a hypothesis (C3) only once the behavior has flipped on/off enough
  times to look like a minimal n-of-1 (ABAB) design — never higher from
  correlation alone.
- Every insight is phrased as a concrete **action prompt with a confidence
  grade**, not a bare number: "On days you did X your recovery averaged +N — try
  Y and watch what happens", framed as a question at C3 and below.

The whole computation is transparent, local and inspectable. Correlation in
personal data is not causation; nothing here diagnoses. Pure stdlib.
"""

from typing import Any, Dict, List, Optional

from .. import evidence, params
from .. import journal_behaviors as catalog
from .base import ModuleResult, register

ALGO_VERSION = "behavior_impact@v1"

DEFAULT_WINDOW_DAYS = 90
MIN_YES_DAYS = 5
MIN_NO_DAYS = 5

# Impact magnitude (recovery points) -> qualitative size label.
SMALL_IMPACT = 3.0
MODERATE_IMPACT = 7.0

# User-tunable params (openhealth.params); constants above stay the defaults.
_PARAM_IDS = (
    "correlations.min_yes_days",
    "correlations.min_no_days",
    "correlations.window_days",
    "correlations.lag_days",
)


def _param(param_id: str, fallback: float) -> float:
    """Effective tunable value; any registry problem falls back to the constant."""
    try:
        return params.get(param_id)
    except Exception:
        return fallback


def _switch_count(flags_by_day: List[bool]) -> int:
    """How many times the behavior flipped yes<->no across the ordered days.

    A proxy for how 'n-of-1 / ABAB' the natural data already is: more genuine
    switches => a personal pattern earns a bit more trust (still capped at C3).
    """
    switches = 0
    prev: Optional[bool] = None
    for f in flags_by_day:
        if prev is not None and f != prev:
            switches += 1
        prev = f
    return switches


def behavior_impact(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Compute the recovery impact of one behavior.

    ``pairs`` is a list of ``{"date", "recovery", "yes": bool}`` for the days
    this behavior was logged *and* a recovery score exists. Returns None when
    the 5-yes / 5-no threshold (tunable via ``openhealth.params``) is not met.
    """
    min_yes = int(_param("correlations.min_yes_days", MIN_YES_DAYS))
    min_no = int(_param("correlations.min_no_days", MIN_NO_DAYS))
    ordered = sorted((p for p in pairs if p.get("recovery") is not None), key=lambda p: p["date"])
    yes = [p["recovery"] for p in ordered if p["yes"]]
    no = [p["recovery"] for p in ordered if not p["yes"]]
    if len(yes) < min_yes or len(no) < min_no:
        return None

    mean_yes = sum(yes) / len(yes)
    mean_no = sum(no) / len(no)
    impact = mean_yes - mean_no
    direction = "positive" if impact >= 0 else "negative"
    magnitude = abs(impact)
    if magnitude < SMALL_IMPACT:
        size = "negligible"
    elif magnitude < MODERATE_IMPACT:
        size = "small"
    else:
        size = "moderate"

    return {
        "n_yes": len(yes),
        "n_no": len(no),
        "min_yes_required": min_yes,
        "min_no_required": min_no,
        "mean_recovery_yes": round(mean_yes, 1),
        "mean_recovery_no": round(mean_no, 1),
        "impact": round(impact, 1),
        "direction": direction,
        "size": size,
        "switches": _switch_count([p["yes"] for p in ordered]),
    }


def _confidence_for(stats: Dict[str, Any]) -> evidence.Confidence:
    """Cap confidence for a personal correlation.

    Negligible impact stays C1. Otherwise it is a weak personal signal (C2);
    if the behavior flipped on/off at least twice (a minimal repeated switch),
    it may rise to a hypothesis (C3). Never higher from correlation alone.
    """
    if stats["size"] == "negligible":
        return evidence.Confidence.C1
    validated_switches = 1 if stats["switches"] >= 2 else 0
    return evidence.cap_personal_pattern(evidence.Confidence.C3, validated_switches=validated_switches)


def _action_text(behavior_name: str, stats: Dict[str, Any]) -> str:
    """Turn an impact into a concrete action prompt (not a bare number)."""
    pts = abs(stats["impact"])
    if stats["direction"] == "positive":
        return (
            "On days you did '%s' your recovery averaged %.0f vs %.0f without it "
            "(+%.0f points). Try doing '%s' deliberately for a week and watch your recovery."
            % (behavior_name, stats["mean_recovery_yes"], stats["mean_recovery_no"], pts, behavior_name)
        )
    return (
        "On days you did '%s' your recovery averaged %.0f vs %.0f without it "
        "(-%.0f points). Try cutting '%s' for a week and watch your recovery."
        % (behavior_name, stats["mean_recovery_yes"], stats["mean_recovery_no"], pts, behavior_name)
    )


def analyze(behaviors: List[Dict[str, Any]], window_days: int = DEFAULT_WINDOW_DAYS) -> List[Dict[str, Any]]:
    """Compute impacts + graded action insights for a set of behaviors.

    ``behaviors`` is a list of ``{"behavior_id", "behavior_name"(opt),
    "category"(opt), "pairs": [...]}``. Returns ``InsightHypothesis`` dicts
    (and a ``PatternAlert``-free output), sorted by impact magnitude, only for
    behaviors that clear the 5/5 threshold and are non-negligible.
    """
    insights: List[Dict[str, Any]] = []
    overrides = params.overrides_for(_PARAM_IDS)
    for entry in behaviors:
        stats = behavior_impact(entry.get("pairs", []))
        if stats is None:
            continue
        bid = entry["behavior_id"]
        meta_behavior = catalog.get_behavior(bid)
        name = entry.get("behavior_name") or (meta_behavior["name"] if meta_behavior else bid)
        category = entry.get("category") or (meta_behavior["category"] if meta_behavior else "unknown")

        conf = _confidence_for(stats)
        if conf == evidence.Confidence.C1:
            # Negligible impact: not worth surfacing as an action.
            continue

        action = _action_text(name, stats)
        # "How was this computed" trace — UI tooltip food. Same numbers as the
        # stats block, under stable names, plus the thresholds actually used.
        lag_days = int(entry.get("lag_days") or 0)
        compute_trace = {
            "yes_days": stats["n_yes"],
            "no_days": stats["n_no"],
            "avg_yes": stats["mean_recovery_yes"],
            "avg_no": stats["mean_recovery_no"],
            "window_days": window_days,
            "lag_days": lag_days,
            "min_yes_required": stats["min_yes_required"],
            "min_no_required": stats["min_no_required"],
        }
        metadata: Dict[str, Any] = {
            **stats,
            "behavior_id": bid,
            "category": category,
            "window_days": window_days,
            "lag_days": lag_days,
            "confidence_grade": conf.value,
            "compute_trace": compute_trace,
            "algo_version": params.stamp(ALGO_VERSION, overrides),
        }
        if overrides:
            metadata["params_overrides"] = overrides
        insights.append({
            "id": "insight-correlation-%s" % bid,
            "record_type": "InsightHypothesis",
            "source_id": "correlations",
            "title": "Impact: %s" % name,
            "summary": evidence.frame_statement(action, conf),
            "artifact_ids": [],
            "evidence_class": "derived-hypothesis",
            "confidence": evidence.confidence_to_numeric(conf),
            "tags": ["correlations", category, "review-needed", stats["direction"]],
            "metadata": metadata,
            "statement": action,
            "evidence_record_ids": [],
            "open_questions": [
                "What else changed on those days (sleep, alcohol, stress, travel)?",
                "Would a deliberate on/off week make the signal clearer?",
            ],
        })

    insights.sort(key=lambda i: abs(i["metadata"]["impact"]), reverse=True)
    return insights


# --- index reader: build behavior/recovery pairs from indexed records ------

def from_index(db_path, window_days: Optional[int] = None, as_of: Optional[str] = None,
               lag_days: Optional[int] = None) -> List[Dict[str, Any]]:
    """Assemble per-behavior recovery pairs from indexed journal + recovery data.

    Reads only through the index API. Pairs a journal day D (boolean entries)
    with the recovery score of day ``D + lag_days``. Recovery is a morning
    metric, so an evening behaviour on day D shows up in the NEXT morning's
    recovery (``lag_days=1``); ``lag_days=0`` keeps the same-day pairing. Days
    without a recovery score on the lagged day are dropped. ``window_days`` and
    ``lag_days`` default to the user-tunable ``correlations.*`` params.
    """
    from datetime import date as _d
    from datetime import datetime, timedelta, timezone

    from .. import index

    if window_days is None:
        window_days = int(_param("correlations.window_days", DEFAULT_WINDOW_DAYS))
    if lag_days is None:
        lag_days = int(_param("correlations.lag_days", 0))
    lag_days = max(0, int(lag_days))

    end = _d.fromisoformat(as_of) if as_of else datetime.now(timezone.utc).date()

    observations = index.list_records(db_path, "Observation")

    # day -> recovery score
    recovery_by_day: Dict[str, float] = {}
    for r in observations:
        if r.get("observation_kind") == "recovery_score" and r.get("date") and r.get("value") is not None:
            recovery_by_day[r["date"]] = float(r["value"])

    # behavior_id -> {category, days: {date: bool}}
    by_behavior: Dict[str, Dict[str, Any]] = {}
    for r in observations:
        if r.get("observation_kind") != "journal_entry":
            continue
        value = r.get("value")
        if not isinstance(value, bool):
            continue  # only boolean behaviors are correlated here
        day = r.get("date")
        if not day:
            continue
        try:
            rd = _d.fromisoformat(day)
        except ValueError:
            continue
        if not (0 <= (end - rd).days < window_days):
            continue
        meta = r.get("metadata", {})
        bid = meta.get("behavior_id") or r.get("metric_name")
        if not bid:
            continue
        slot = by_behavior.setdefault(bid, {"category": meta.get("category", "unknown"), "days": {}})
        slot["days"][day] = value

    behaviors: List[Dict[str, Any]] = []
    for bid, slot in by_behavior.items():
        pairs = []
        for day, yes in slot["days"].items():
            try:
                rec_day = (_d.fromisoformat(day) + timedelta(days=lag_days)).isoformat()
            except ValueError:
                continue
            if rec_day in recovery_by_day:
                pairs.append({"date": day, "recovery_date": rec_day, "yes": yes,
                              "recovery": recovery_by_day[rec_day]})
        behaviors.append({"behavior_id": bid, "category": slot["category"],
                          "pairs": pairs, "lag_days": lag_days})
    return behaviors


# --- module ----------------------------------------------------------------

class CorrelationsModule:
    id = "correlations"
    name = "Correlations — behavior impact on recovery"
    domain = "correlations"
    summary = "Compares average recovery on yes vs no days per behavior (5/5 threshold) into graded action prompts."

    def schema(self) -> Dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "CorrelationsInput",
            "type": "object",
            "required": ["behaviors"],
            "properties": {
                "window_days": {"type": "integer", "default": DEFAULT_WINDOW_DAYS},
                "behaviors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["behavior_id", "pairs"],
                        "properties": {
                            "behavior_id": {"type": "string"},
                            "behavior_name": {"type": "string"},
                            "category": {"type": "string"},
                            "pairs": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["date", "yes"],
                                    "properties": {
                                        "date": {"type": "string"},
                                        "yes": {"type": "boolean"},
                                        "recovery": {"type": "number"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

    def compute(self, payload: Dict[str, Any]) -> ModuleResult:
        behaviors = payload.get("behaviors") or []
        window_days = payload.get("window_days")
        if window_days is None:
            window_days = _param("correlations.window_days", DEFAULT_WINDOW_DAYS)
        window_days = int(window_days)
        insights = analyze(behaviors, window_days=window_days)

        min_yes = int(_param("correlations.min_yes_days", MIN_YES_DAYS))
        min_no = int(_param("correlations.min_no_days", MIN_NO_DAYS))
        analyzed = sum(1 for b in behaviors if behavior_impact(b.get("pairs", [])) is not None)
        notes = [
            "analyzed %d/%d behaviors meeting the %d-yes/%d-no threshold; %d actionable impact(s)"
            % (analyzed, len(behaviors), min_yes, min_no, len(insights))
        ]
        return ModuleResult(metrics=[], insights=insights, notes=notes)


def persist(result: ModuleResult, db_path) -> int:
    """Write correlation insights into the SQLite index. Returns count written."""
    from .. import index

    written = 0
    for rec in list(result.insights):
        index.upsert_record(db_path, rec)
        written += 1
    return written


register(CorrelationsModule())
