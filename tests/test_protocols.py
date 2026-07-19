"""Tests for openhealth.protocols — n-of-1 protocol generation.

No network, no DB. Covers: each insight kind maps to a protocol template;
correlations (C2+) become ABAB verification protocols; ranking and the
max-3 cap; empty input.
"""

import re
import unittest

from openhealth import evidence, insights, protocols


def _insight(kind, severity=insights.ATTENTION, confidence=evidence.Confidence.C2):
    return insights.Insight(
        id="insight-%s" % kind,
        title_ru="t",
        severity=severity,
        confidence=confidence,
        evidence_text="e",
        question_ru="q",
        action_ru="a",
        metric="recovery",
        data={"kind": kind},
    )


def _correlation(bid, impact, direction, grade="C3", title=None):
    return {
        "id": "insight-correlation-%s" % bid,
        "title": title or ("Impact: %s" % bid),
        "metadata": {
            "behavior_id": bid,
            "impact": impact,
            "direction": direction,
            "confidence_grade": grade,
        },
    }


# Behavior names in ``openhealth/data/journal_behaviors.json`` are Russian, so a
# correlation title arrives in Cyrillic. Kept as an escape to hold this source
# ASCII while still proving the Russian trigger stems fire.
RU_ALCOHOL = "\u0430\u043b\u043a\u043e\u0433\u043e\u043b\u044c"  # "alcohol"

ALL_KINDS = [
    "sleep_debt", "hrv_downtrend", "rhr_uptrend", "recovery_red_streak",
    "strain_recovery_mismatch", "weekend_pattern", "sleep_consistency",
]


class FromInsightTests(unittest.TestCase):
    def test_every_kind_maps_to_a_protocol(self):
        for kind in ALL_KINDS:
            p = protocols.from_insight(_insight(kind))
            self.assertIsNotNone(p, kind)
            self.assertIn(p.schema, ("AB", "ABAB"), kind)
            self.assertEqual(p.confidence_cap, evidence.Confidence.C2, kind)
            self.assertTrue(p.safety_note_ru, kind)
            # Success criterion must carry a concrete number.
            self.assertTrue(re.search(r"\d", p.success_criteria_ru), kind)

    def test_unknown_kind_returns_none(self):
        self.assertIsNone(protocols.from_insight(_insight("nope")))

    def test_red_streak_has_stronger_safety_note(self):
        p = protocols.from_insight(_insight("recovery_red_streak", severity=insights.WARNING))
        self.assertEqual(p.safety_note_ru, protocols.RED_STREAK_SAFETY_NOTE)

    def test_hrv_protocol_points_at_correlation_trigger(self):
        corr = [_correlation("substances.alcohol", -8.0, "negative", grade="C3",
                              title="Impact: %s" % RU_ALCOHOL)]
        p = protocols.from_insight(_insight("hrv_downtrend"), correlations=corr)
        self.assertIn(RU_ALCOHOL, p.intervention_ru)

    def test_hrv_protocol_generic_without_trigger(self):
        p = protocols.from_insight(_insight("hrv_downtrend"), correlations=[])
        self.assertIn("recovery-focused", p.intervention_ru)


class FromCorrelationTests(unittest.TestCase):
    def test_positive_direction(self):
        p = protocols.from_correlation(_correlation("recovery_activities.meditation", 6.0, "positive"))
        self.assertEqual(p.schema, "ABAB")
        self.assertIn("every day of the intervention phase", p.intervention_ru)
        self.assertIn("6", p.success_criteria_ru)

    def test_negative_direction(self):
        p = protocols.from_correlation(_correlation("substances.alcohol", 9.0, "negative"))
        self.assertIn("Remove", p.intervention_ru)

    def test_success_criterion_floored_at_three(self):
        p = protocols.from_correlation(_correlation("x.y", 1.0, "positive"))
        self.assertIn(">= 3", p.success_criteria_ru)


class BuildProtocolsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(protocols.build_protocols([], None), [])
        self.assertEqual(protocols.build_protocols([], []), [])

    def test_max_three(self):
        ins = [_insight(k) for k in ALL_KINDS]
        out = protocols.build_protocols(ins, [])
        self.assertEqual(len(out), protocols.MAX_ACTIVE_PROTOCOLS)

    def test_warning_ranked_before_attention(self):
        ins = [
            _insight("weekend_pattern", severity=insights.ATTENTION),
            _insight("recovery_red_streak", severity=insights.WARNING),
        ]
        out = protocols.build_protocols(ins, [])
        self.assertEqual(out[0].id, "protocol-recovery_red_streak")

    def test_correlation_included_when_c2_plus(self):
        out = protocols.build_protocols([], [_correlation("a.b", 7.0, "positive", grade="C2")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].id, "protocol-corr-a.b")

    def test_correlation_excluded_when_c1(self):
        out = protocols.build_protocols([], [_correlation("a.b", 7.0, "positive", grade="C1")])
        self.assertEqual(out, [])

    def test_correlation_dedup_by_behavior(self):
        corrs = [
            _correlation("a.b", 7.0, "positive", grade="C3"),
            _correlation("a.b", 5.0, "positive", grade="C2"),
        ]
        out = protocols.build_protocols([], corrs)
        self.assertEqual(len(out), 1)

    def test_to_dict_shape(self):
        p = protocols.build_protocols([_insight("sleep_debt")], [])[0]
        d = p.to_dict()
        for key in ("id", "hypothesis_ru", "intervention_ru", "metric", "baseline_days",
                    "intervention_days", "schema", "success_criteria_ru",
                    "confidence_cap", "confidence_cap_label", "safety_note_ru"):
            self.assertIn(key, d)
        self.assertEqual(d["confidence_cap"], "C2")


if __name__ == "__main__":
    unittest.main()
