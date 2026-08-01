"""Evidence grading and safety checks for OpenHealth.

This module encodes a deliberately simple confidence scale (C1-C5) inspired by
established evidence-grading systems (GRADE, Oxford CEBM, SORT, USPSTF) but
simplified for a personal, beginner-facing health system.

Design rules baked in here:
- A pattern derived purely from a user's own data defaults to at most C3
  ("hypothesis to test") until it survives an n-of-1 style repeated switch.
- Anything at C3 or below must be phrased as a question, not a statement.
- Red-flag findings short-circuit hypothesis generation and route the user to
  a clinician. The system never diagnoses or prescribes.

Nothing in this module is medical advice. It structures uncertainty so the rest
of the system can stay honest about what it does and does not know.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class Confidence(str, Enum):
    """Five-level confidence scale. Higher = more trustworthy."""

    C5 = "C5"  # Established: matches guideline / systematic review of RCTs
    C4 = "C4"  # Likely: consistent RCTs / large cohorts (GRADE moderate)
    C3 = "C3"  # Hypothesis: observational + plausible mechanism (GRADE low)
    C2 = "C2"  # Weak signal: little/conflicting data, or a raw personal pattern
    C1 = "C1"  # Speculation: mechanism / opinion / single case only


# Human-readable labels and the framing each level licenses. Levels at or below
# C3 are framed as questions; C4/C5 may be stated (still never as a diagnosis).
CONFIDENCE_META: Dict[Confidence, Dict[str, object]] = {
    Confidence.C5: {
        "label": "Established",
        "blurb": "This is a well-established fact.",
        "frame_as_question": False,
        "numeric": 0.9,
    },
    Confidence.C4: {
        "label": "Likely",
        "blurb": "Probably true, but confirm with a clinician.",
        "frame_as_question": False,
        "numeric": 0.7,
    },
    Confidence.C3: {
        "label": "Hypothesis",
        "blurb": "A hypothesis worth testing, not a conclusion.",
        "frame_as_question": True,
        "numeric": 0.45,
    },
    Confidence.C2: {
        "label": "Weak signal",
        "blurb": "A raw observation. More data needed before trusting it.",
        "frame_as_question": True,
        "numeric": 0.3,
    },
    Confidence.C1: {
        "label": "Speculation",
        "blurb": "An idea only. Do not rely on it.",
        "frame_as_question": True,
        "numeric": 0.15,
    },
}


def confidence_to_numeric(level: Confidence) -> float:
    """Map a confidence level to the 0-1 score used by record dataclasses."""

    return float(CONFIDENCE_META[level]["numeric"])


def numeric_to_confidence(score: float) -> Confidence:
    """Bucket a 0-1 numeric score into the nearest confidence level."""

    if score >= 0.85:
        return Confidence.C5
    if score >= 0.6:
        return Confidence.C4
    if score >= 0.4:
        return Confidence.C3
    if score >= 0.25:
        return Confidence.C2
    return Confidence.C1


def cap_personal_pattern(level: Confidence, validated_switches: int = 0) -> Confidence:
    """Cap confidence for a pattern derived from the user's own data.

    A personal pattern cannot exceed C3 until it has survived at least one
    repeated on/off switch with a baseline (a minimal n-of-1 design). Until
    then it stays at C2 ("weak signal"). This is the guardrail that stops a
    beginner from mistaking a coincidence for a cause.
    """

    order = [Confidence.C1, Confidence.C2, Confidence.C3, Confidence.C4, Confidence.C5]
    if validated_switches < 1:
        # Not yet validated: never above "weak signal".
        return min(level, Confidence.C2, key=order.index)
    # One or more validated switches: may rise to a hypothesis, no higher.
    return min(level, Confidence.C3, key=order.index)


def frame_statement(text: str, level: Confidence) -> str:
    """Render a finding with framing appropriate to its confidence level.

    At C3 and below the finding is phrased as an open question so the user
    treats it as something to investigate rather than a fact.
    """

    meta = CONFIDENCE_META[level]
    tag = "[%s %s]" % (level.value, meta["label"])
    if meta["frame_as_question"]:
        return "%s Possible pattern to check: %s What else could explain it?" % (tag, text)
    return "%s %s" % (tag, text)


# --- Red flags -------------------------------------------------------------
#
# When any of these is present the system must stop interpreting and route the
# user to professional care. Keep this list conservative and explicit.


@dataclass
class RedFlag:
    code: str
    message: str
    urgency: str  # "emergency" | "urgent" | "out-of-scope"


# Keyword triggers found in free-text intake (lowercased substring match).
SYMPTOM_RED_FLAGS: Dict[str, RedFlag] = {
    "chest pain": RedFlag("chest_pain", "Chest pain can be an emergency. Seek urgent medical care now.", "emergency"),
    "боль в груди": RedFlag(
        "chest_pain",
        "Боль в груди может требовать экстренной помощи. Обратитесь за срочной медицинской помощью.",
        "emergency",
    ),
    "shortness of breath": RedFlag("dyspnea", "Trouble breathing needs urgent medical attention.", "emergency"),
    "не могу дышать": RedFlag(
        "dyspnea",
        "Затруднение дыхания требует срочной медицинской помощи.",
        "emergency",
    ),
    "не хватает воздуха": RedFlag(
        "dyspnea",
        "Затруднение дыхания требует срочной медицинской помощи.",
        "emergency",
    ),
    "одышка": RedFlag(
        "dyspnea",
        "Затруднение дыхания требует срочной медицинской помощи.",
        "emergency",
    ),
    "fainting": RedFlag("syncope", "Fainting should be assessed by a clinician promptly.", "urgent"),
    "обморок": RedFlag(
        "syncope",
        "Обморок следует как можно скорее обсудить с врачом.",
        "urgent",
    ),
    "потерял сознание": RedFlag(
        "syncope",
        "Потерю сознания следует как можно скорее обсудить с врачом.",
        "urgent",
    ),
    "потеряла сознание": RedFlag(
        "syncope",
        "Потерю сознания следует как можно скорее обсудить с врачом.",
        "urgent",
    ),
    "numbness": RedFlag(
        "focal_neuro",
        "One-sided weakness or numbness can signal a stroke. Seek emergency care.",
        "emergency",
    ),
    "онемение": RedFlag(
        "focal_neuro",
        "Внезапное онемение или слабость с одной стороны могут требовать экстренной помощи.",
        "emergency",
    ),
    "suicidal": RedFlag(
        "mental_health_crisis",
        "Please reach out to a crisis line or emergency services now.",
        "emergency",
    ),
    "суицид": RedFlag(
        "mental_health_crisis",
        "Пожалуйста, прямо сейчас обратитесь за экстренной профессиональной помощью.",
        "emergency",
    ),
    "хочу умереть": RedFlag(
        "mental_health_crisis",
        "Пожалуйста, прямо сейчас обратитесь за экстренной профессиональной помощью.",
        "emergency",
    ),
    "blood in stool": RedFlag("gi_bleed", "Blood in stool should be evaluated by a clinician.", "urgent"),
    "кровь в стуле": RedFlag(
        "gi_bleed",
        "Кровь в стуле следует как можно скорее обсудить с врачом.",
        "urgent",
    ),
    "coughing blood": RedFlag("hemoptysis", "Coughing up blood needs urgent medical assessment.", "urgent"),
    "кашляю кровью": RedFlag(
        "hemoptysis",
        "Кашель с кровью требует срочной медицинской оценки.",
        "urgent",
    ),
    "unexplained weight loss": RedFlag(
        "weight_loss",
        "Unexplained weight loss should be checked by a clinician.",
        "urgent",
    ),
    "необъяснимая потеря веса": RedFlag(
        "weight_loss",
        "Необъяснимую потерю веса следует обсудить с врачом.",
        "urgent",
    ),
}


# Critical lab thresholds (value in the marker's conventional unit). These mark
# values a lab would itself flag as "critical/panic". Comparison is inclusive.
CRITICAL_LAB_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "glucose": {"low": 50.0, "high": 300.0},        # mg/dL
    "potassium": {"low": 2.5, "high": 6.0},          # mmol/L
    "hemoglobin": {"low": 7.0, "high": 20.0},        # g/dL
    "platelets": {"low": 20.0, "high": 1000.0},      # x10^9/L
    "sodium": {"low": 120.0, "high": 160.0},         # mmol/L
}


def scan_text_red_flags(text: Optional[str]) -> List[RedFlag]:
    """Return red flags triggered by keywords in free text."""

    if not text:
        return []
    lowered = text.lower()
    hits: List[RedFlag] = []
    for needle, flag in SYMPTOM_RED_FLAGS.items():
        if needle in lowered:
            hits.append(flag)
    return hits


def check_critical_lab(marker_key: str, value: Optional[float]) -> Optional[RedFlag]:
    """Return a red flag if a lab value crosses a critical threshold."""

    if value is None:
        return None
    thresholds = CRITICAL_LAB_THRESHOLDS.get(marker_key)
    if not thresholds:
        return None
    if value <= thresholds["low"] or value >= thresholds["high"]:
        return RedFlag(
            code="critical_lab_%s" % marker_key,
            message=(
                "%s value (%s) is in the critical range. Contact a clinician promptly; "
                "do not wait for the system to interpret it." % (marker_key, value)
            ),
            urgency="urgent",
        )
    return None
