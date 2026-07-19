"""Turn cautious insights and personal correlations into n-of-1 protocols.

An ``Insight`` says "here is a possible problem"; a ``Protocol`` says "here is
the one change to test and exactly how we'll know if it helped". This is what
moves a finding from a *weak personal signal* (C2) toward something trustworthy:
a minimal single-subject experiment (n-of-1), usually an ABAB switch with a
baseline and a pre-stated success criterion.

Design rules
------------
- One intervention per protocol. Change one thing at a time or the result is
  uninterpretable.
- A concrete, numeric success criterion stated up front (no moving goalposts).
- ``confidence_cap`` is C2 while the protocol is unfinished: until the switch
  actually plays out, the underlying belief stays a weak personal signal
  (canon: ``openhealth.evidence``). Completing the n-of-1 is what can lift it.
- A safety note on every protocol. This is self-observation, not treatment;
  red-flag symptoms route to a clinician.

Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import evidence
from . import insights as insights_mod

MAX_ACTIVE_PROTOCOLS = 3

# Default safety note shared by all protocols; some kinds strengthen it.
DEFAULT_SAFETY_NOTE = (
    "This is self-observation (n-of-1), not treatment. Change only one factor at a "
    "time. If you have worrying symptoms, see a doctor."
)
RED_STREAK_SAFETY_NOTE = (
    "A long run of low recovery together with symptoms (fever, pain, heavy fatigue) "
    "is a reason to see a doctor first, not to start an experiment."
)

# Behavior-id / category fragments that look like classic HRV suppressors. Used
# to point an HRV-downtrend protocol at a concrete trigger when correlations
# already implicate one.
# Behavior names in ``openhealth/data/journal_behaviors.json`` are Russian, so
# each English stem is paired with its Russian counterpart, written as escapes
# to keep this source ASCII:
#   \u0430\u043b\u043a\u043e\u0433 = "alcohol"   \u044d\u043a\u0440\u0430\u043d = "screen"
#   \u043f\u043e\u0437\u0434\u043d = "late"      \u043a\u043e\u0444\u0435\u0438\u043d = "caffeine"
_HRV_TRIGGER_HINTS = (
    "alcohol", "\u0430\u043b\u043a\u043e\u0433",
    "screen", "\u044d\u043a\u0440\u0430\u043d",
    "late", "\u043f\u043e\u0437\u0434\u043d",
    "caffeine", "\u043a\u043e\u0444\u0435\u0438\u043d",
)


@dataclass
class Protocol:
    """A single n-of-1 experiment proposal."""

    id: str
    hypothesis_ru: str
    intervention_ru: str                # exactly one change
    metric: str                         # what we measure
    baseline_days: int
    intervention_days: int
    schema: str                         # "ABAB" | "AB"
    success_criteria_ru: str            # concrete, numeric, pre-stated
    confidence_cap: evidence.Confidence = evidence.Confidence.C2
    safety_note_ru: str = DEFAULT_SAFETY_NOTE

    def to_dict(self) -> Dict[str, Any]:
        meta = evidence.CONFIDENCE_META[self.confidence_cap]
        return {
            "id": self.id,
            "hypothesis_ru": self.hypothesis_ru,
            "intervention_ru": self.intervention_ru,
            "metric": self.metric,
            "baseline_days": self.baseline_days,
            "intervention_days": self.intervention_days,
            "schema": self.schema,
            "success_criteria_ru": self.success_criteria_ru,
            "confidence_cap": self.confidence_cap.value,
            "confidence_cap_label": meta["label"],
            "safety_note_ru": self.safety_note_ru,
        }


# --- from a single insight ---------------------------------------------------

def _kind(insight: "insights_mod.Insight") -> str:
    return insight.data.get("kind") or insight.id.replace("insight-", "")


def _hrv_intervention(correlations: Optional[List[Dict[str, Any]]]) -> str:
    """Point the HRV protocol at a concrete trigger if one is implicated."""
    for c in correlations or []:
        meta = c.get("metadata", {})
        if meta.get("direction") != "negative":
            continue
        hay = "%s %s %s" % (
            meta.get("behavior_id", ""), meta.get("category", ""), c.get("title", "")
        )
        hay = hay.lower()
        if any(h in hay for h in _HRV_TRIGGER_HINTS):
            name = c.get("title", "").replace("Impact: ", "").strip() or "this factor"
            return "Drop '%s' for 7 days (in your data it goes with a decline)." % name
    return "Take 7 recovery-focused days: bedtime 30-45 minutes earlier, no evening alcohol."


def from_insight(
    insight: "insights_mod.Insight",
    correlations: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Protocol]:
    """Map one insight to a protocol template. Returns None if no template."""
    kind = _kind(insight)

    if kind == "sleep_debt":
        return Protocol(
            id="protocol-sleep_debt",
            hypothesis_ru="If the sleep shortfall goes away, mean recovery will rise.",
            intervention_ru="Go to bed 45 minutes earlier than usual.",
            metric="recovery",
            baseline_days=7,
            intervention_days=7,
            schema="ABAB",
            success_criteria_ru="Mean recovery in the early-bedtime phases (B) is higher "
                                 "than in the normal ones (A) by >= 5 points.",
        )

    if kind == "hrv_downtrend":
        return Protocol(
            id="protocol-hrv_downtrend",
            hypothesis_ru="Removing the main load on HRV will bring it back to your personal baseline.",
            intervention_ru=_hrv_intervention(correlations),
            metric="hrv",
            baseline_days=7,
            intervention_days=7,
            schema="ABAB",
            success_criteria_ru="7-day mean HRV in the intervention phase is >= 8% above "
                                 "the baseline phase (a return to baseline).",
        )

    if kind == "rhr_uptrend":
        return Protocol(
            id="protocol-rhr_uptrend",
            hypothesis_ru="Cutting evening load and alcohol will bring resting heart rate back to baseline.",
            intervention_ru="Drop evening alcohol and add 2 easy days per week.",
            metric="rhr",
            baseline_days=7,
            intervention_days=7,
            schema="ABAB",
            success_criteria_ru="7-day resting heart rate in the intervention phase is within 2 bpm of baseline.",
        )

    if kind == "recovery_red_streak":
        return Protocol(
            id="protocol-recovery_red_streak",
            hypothesis_ru="A week with recovery as the priority brings recovery out of the red zone.",
            intervention_ru="7 days with sleep and rest as the priority: early bedtime, no "
                            "intense training and no evening alcohol.",
            metric="recovery",
            baseline_days=7,
            intervention_days=7,
            schema="AB",
            success_criteria_ru="No red days in a row; mean recovery in phase B is higher than in A by >= 7 points.",
            safety_note_ru=RED_STREAK_SAFETY_NOTE,
        )

    if kind == "strain_recovery_mismatch":
        return Protocol(
            id="protocol-strain_recovery_mismatch",
            hypothesis_ru="If intensity is tied to morning recovery, recovery will improve.",
            intervention_ru="Plan intensity from morning recovery: if recovery < 50, keep it an easy day.",
            metric="recovery",
            baseline_days=7,
            intervention_days=7,
            schema="ABAB",
            success_criteria_ru="No days with strain >= 14 while recovery < 50; "
                                 "mean recovery in phase B is above A by >= 5 points.",
        )

    if kind == "weekend_pattern":
        return Protocol(
            id="protocol-weekend_pattern",
            hypothesis_ru="Levelling out weekend bedtimes removes the recovery dip.",
            intervention_ru="Keep your weekday bedtime on weekends (within 30 minutes).",
            metric="recovery",
            baseline_days=14,
            intervention_days=14,
            schema="ABAB",
            success_criteria_ru="The weekday-weekend gap in mean recovery becomes < 5 points.",
        )

    if kind == "sleep_consistency":
        return Protocol(
            id="protocol-sleep_consistency",
            hypothesis_ru="A steady sleep schedule matters more than perfect duration and lifts recovery.",
            intervention_ru="Fix your wake time (within 30 minutes) for 14 days, weekends included.",
            metric="sleep_h",
            baseline_days=14,
            intervention_days=14,
            schema="AB",
            success_criteria_ru="Standard deviation of sleep duration < 1.0h; "
                                 "mean recovery higher by >= 5 points.",
        )

    return None


# --- from a personal correlation ---------------------------------------------

def from_correlation(corr: Dict[str, Any]) -> Optional[Protocol]:
    """Build an ABAB verification protocol from a C2+ correlation insight.

    ``corr`` is a correlations-module insight dict (see
    ``openhealth.modules.correlations``): it carries ``metadata`` with
    ``behavior_id``, ``impact``, ``direction`` and ``confidence_grade``.
    """
    meta = corr.get("metadata", {})
    bid = meta.get("behavior_id") or "behavior"
    name = (corr.get("title", "") or "").replace("Impact: ", "").strip() or bid
    impact = abs(float(meta.get("impact", 0.0)))
    direction = meta.get("direction", "positive")

    if direction == "positive":
        intervention = "Deliberately do '%s' every day of the intervention phase." % name
        crit = ("Mean recovery in the phases with '%s' is higher than without it by >= %s points."
                % (name, _round_points(impact)))
        hypo = "If '%s' is done regularly, recovery will rise." % name
    else:
        intervention = "Remove '%s' for the intervention phase." % name
        crit = ("Mean recovery in the phases without '%s' is higher than with it by >= %s points."
                % (name, _round_points(impact)))
        hypo = "If '%s' is removed, recovery will rise." % name

    return Protocol(
        id="protocol-corr-%s" % bid,
        hypothesis_ru=hypo,
        intervention_ru=intervention,
        metric="recovery",
        baseline_days=7,
        intervention_days=7,
        schema="ABAB",
        success_criteria_ru=crit,
    )


def _round_points(x: float) -> str:
    # At least a 3-point bar so the test is not chasing noise.
    return "%d" % max(3, round(x))


# --- orchestration ------------------------------------------------------------

def build_protocols(
    insights: List["insights_mod.Insight"],
    correlations: Optional[List[Dict[str, Any]]] = None,
) -> List[Protocol]:
    """Build up to 3 active protocol suggestions, highest severity first.

    Insight-derived protocols are ranked by the severity and confidence of the
    insight; correlation-derived protocols slot in at "attention" weight, ranked
    by their confidence grade. Returns at most ``MAX_ACTIVE_PROTOCOLS``.
    """
    ranked: List[tuple] = []  # (severity_rank, -confidence, seq, protocol)
    seq = 0

    for ins in insights or []:
        proto = from_insight(ins, correlations=correlations)
        if proto is None:
            continue
        sev_rank = insights_mod._SEVERITY_RANK.get(ins.severity, 9)
        conf = evidence.confidence_to_numeric(ins.confidence)
        ranked.append((sev_rank, -conf, seq, proto))
        seq += 1

    seen_bids = set()
    for corr in correlations or []:
        meta = corr.get("metadata", {})
        grade = meta.get("confidence_grade", "C1")
        # Only C2 and above are worth a verification protocol.
        if grade in ("C1",):
            continue
        bid = meta.get("behavior_id")
        if bid in seen_bids:
            continue
        seen_bids.add(bid)
        proto = from_correlation(corr)
        if proto is None:
            continue
        conf = evidence.confidence_to_numeric(evidence.Confidence(grade))
        ranked.append((insights_mod._SEVERITY_RANK[insights_mod.ATTENTION], -conf, seq, proto))
        seq += 1

    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in ranked[:MAX_ACTIVE_PROTOCOLS]]


def protocols_to_dicts(protocols: List[Protocol]) -> List[Dict[str, Any]]:
    """Convenience: serialize protocols for JSON / the dashboard."""
    return [p.to_dict() for p in protocols]
