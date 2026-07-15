import unittest

from openhealth import data_quality as dq


class FutureDateTests(unittest.TestCase):
    def test_future_date_flagged(self):
        recs = [
            {"name": "recovery", "value": 70, "date": "2030-01-01"},
            {"name": "recovery", "value": 60, "date": "2024-01-01"},
        ]
        report = dq.validate_records(recs, today="2024-06-01")
        future = [i for i in report["issues"] if i["kind"] == "future_date"]
        self.assertEqual(len(future), 1)
        self.assertEqual(future[0]["severity"], dq.SEV_HIGH)
        self.assertEqual(future[0]["date"], "2030-01-01")


class DuplicateTests(unittest.TestCase):
    def test_conflicting_duplicate_flagged(self):
        recs = [
            {"name": "hrv", "value": 80, "date": "2024-06-01"},
            {"name": "hrv", "value": 95, "date": "2024-06-01"},
        ]
        report = dq.validate_records(recs, today="2024-06-02")
        dups = [i for i in report["issues"] if i["kind"] == "duplicate"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["severity"], dq.SEV_MEDIUM)

    def test_identical_repeat_not_flagged(self):
        recs = [
            {"name": "hrv", "value": 80, "date": "2024-06-01"},
            {"name": "hrv", "value": 80, "date": "2024-06-01"},
        ]
        report = dq.validate_records(recs, today="2024-06-02")
        dups = [i for i in report["issues"] if i["kind"] == "duplicate"]
        self.assertEqual(dups, [])

    def test_per_event_metric_not_flagged(self):
        # Two workouts in one day each have their own strain — not a conflict.
        recs = [
            {"name": "strain", "value": 7.0, "date": "2024-06-01"},
            {"name": "strain", "value": 12.0, "date": "2024-06-01"},
        ]
        report = dq.validate_records(recs, today="2024-06-02")
        dups = [i for i in report["issues"] if i["kind"] == "duplicate"]
        self.assertEqual(dups, [])


class ImpossibleValueTests(unittest.TestCase):
    def test_hrv_out_of_bounds(self):
        recs = [{"name": "hrv", "value": 500, "date": "2024-06-01"}]
        report = dq.validate_records(recs, today="2024-06-02")
        bad = [i for i in report["issues"] if i["kind"] == "impossible_value"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["severity"], dq.SEV_HIGH)

    def test_rhr_too_low(self):
        recs = [{"name": "resting_heart_rate", "value": 10, "date": "2024-06-01"}]
        report = dq.validate_records(recs, today="2024-06-02")
        self.assertTrue(any(i["kind"] == "impossible_value" for i in report["issues"]))

    def test_sleep_over_16h(self):
        recs = [{"name": "sleep_h", "value": 18, "date": "2024-06-01"}]
        report = dq.validate_records(recs, today="2024-06-02")
        self.assertTrue(any(i["kind"] == "impossible_value" for i in report["issues"]))

    def test_normal_value_clean(self):
        recs = [
            {"name": "hrv", "value": 82, "date": "2024-06-01"},
            {"name": "rhr", "value": 55, "date": "2024-06-01"},
        ]
        report = dq.validate_records(recs, today="2024-06-02")
        self.assertEqual([i for i in report["issues"] if i["kind"] == "impossible_value"], [])

    def test_inclusive_percentage_endpoints_are_valid(self):
        # 0% and 100% are real WHOOP/Oura readings, not data errors: the bounds
        # are inclusive, so the endpoints must NOT be flagged.
        recs = [
            {"name": "recovery", "value": 100, "date": "2024-06-01"},
            {"name": "recovery", "value": 0, "date": "2024-06-02"},
            {"name": "sleep_performance", "value": 100, "date": "2024-06-03"},
            {"name": "spo2", "value": 100, "date": "2024-06-04"},
        ]
        report = dq.validate_records(recs, today="2024-06-05")
        self.assertEqual([i for i in report["issues"] if i["kind"] == "impossible_value"], [])


class GapTests(unittest.TestCase):
    def test_gap_in_series_flagged(self):
        recs = [
            {"name": "recovery", "value": 70, "date": "2024-06-01"},
            {"name": "recovery", "value": 65, "date": "2024-06-10"},  # 9-day gap
        ]
        report = dq.validate_records(recs, today="2024-06-11", gap_days=4)
        gaps = [i for i in report["issues"] if i["kind"] == "series_gap"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["severity"], dq.SEV_LOW)

    def test_no_gap_when_consecutive(self):
        recs = [
            {"name": "recovery", "value": 70, "date": "2024-06-01"},
            {"name": "recovery", "value": 65, "date": "2024-06-03"},
        ]
        report = dq.validate_records(recs, today="2024-06-04", gap_days=4)
        self.assertEqual([i for i in report["issues"] if i["kind"] == "series_gap"], [])


class UnitSuspicionTests(unittest.TestCase):
    def test_glucose_looks_like_mmol(self):
        # 5.5 with no unit reads as severe hypoglycaemia in mg/dL but is 99 mg/dL
        # after x18 -> almost certainly mmol/L.
        recs = [{"name": "glucose", "value": 5.5, "date": "2024-06-01"}]
        report = dq.validate_records(recs, today="2024-06-02")
        susp = [i for i in report["issues"] if i["kind"] == "unit_suspect"]
        self.assertEqual(len(susp), 1)
        self.assertEqual(susp[0]["severity"], dq.SEV_MEDIUM)

    def test_explicit_mgdl_not_suspect(self):
        recs = [{"name": "glucose", "value": 90, "unit": "mg/dL", "date": "2024-06-01"}]
        report = dq.validate_records(recs, today="2024-06-02")
        self.assertEqual([i for i in report["issues"] if i["kind"] == "unit_suspect"], [])


class ScoreTests(unittest.TestCase):
    def test_clean_data_scores_100(self):
        recs = [{"name": "hrv", "value": 82, "date": "2024-06-01"}]
        report = dq.validate_records(recs, today="2024-06-02")
        score = dq.quality_score(report)
        self.assertEqual(score["score"], 100)

    def test_score_penalizes_by_severity(self):
        recs = [
            {"name": "hrv", "value": 500, "date": "2024-06-01"},   # high (-12)
            {"name": "glucose", "value": 5.5, "date": "2024-06-01"},  # medium (-6)
        ]
        report = dq.validate_records(recs, today="2024-06-02")
        score = dq.quality_score(report)
        self.assertEqual(score["score"], 100 - 12 - 6)
        self.assertEqual(score["breakdown"][dq.SEV_HIGH]["count"], 1)
        self.assertEqual(score["breakdown"][dq.SEV_MEDIUM]["count"], 1)

    def test_score_floors_at_zero(self):
        recs = [{"name": "hrv", "value": 500, "date": "2024-06-01"} for _ in range(20)]
        report = dq.validate_records(recs, today="2024-06-02")
        score = dq.quality_score(report)
        self.assertEqual(score["score"], 0)


if __name__ == "__main__":
    unittest.main()
