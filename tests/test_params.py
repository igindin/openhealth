import json
import os
import tempfile
import unittest
from pathlib import Path

from openhealth import insights, params
from openhealth.modules import correlations, recovery

REPO_ROOT = Path(__file__).resolve().parents[1]


class ParamsHomeMixin(unittest.TestCase):
    """Isolate every test in a fresh OPENHEALTH_HOME (no real overrides leak in)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("OPENHEALTH_HOME")
        os.environ["OPENHEALTH_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("OPENHEALTH_HOME", None)
        else:
            os.environ["OPENHEALTH_HOME"] = self._old_home
        self._tmp.cleanup()


class RegistryValidityTests(ParamsHomeMixin):
    REQUIRED_KEYS = ("default", "min", "max", "step", "label_ru", "unit", "where", "affects", "doc", "group", "wired")

    def test_every_entry_is_complete_and_in_range(self):
        self.assertTrue(params.REGISTRY)
        for pid, spec in params.REGISTRY.items():
            for key in self.REQUIRED_KEYS:
                self.assertIn(key, spec, "%s missing %s" % (pid, key))
            self.assertLessEqual(spec["min"], spec["default"], pid)
            self.assertLessEqual(spec["default"], spec["max"], pid)
            self.assertGreater(spec["step"], 0, pid)
            self.assertTrue(spec["label_ru"].strip(), pid)
            self.assertTrue(spec["where"].strip(), pid)
            self.assertTrue(spec["affects"], pid)

    def test_doc_paths_exist(self):
        for pid, spec in params.REGISTRY.items():
            self.assertTrue((REPO_ROOT / spec["doc"]).is_file(), "%s doc missing: %s" % (pid, spec["doc"]))

    def test_registry_covers_the_promised_minimum(self):
        for pid in (
            "recovery.weights.hrv", "recovery.weights.rhr", "recovery.weights.respiratory",
            "recovery.weights.sleep", "recovery.baseline_window_days", "recovery.hrv_full_swing_sd",
            "recovery.sleep_need_h",
            "correlations.min_yes_days", "correlations.min_no_days", "correlations.window_days",
            "insights.sleep_goal_h", "insights.sleep_debt_week_attention_h", "insights.hrv_drop_attention_pct",
            "insights.rhr_rise_attention_bpm", "insights.red_streak_days", "insights.weekend_diff_points",
            "insights.sleep_consistency_stdev_h",
            "weather.pressure_change_hpa", "weather.heat_apparent_max_c",
            "day_load.weights.busy_hours", "day_load.weights.meetings", "day_load.weights.no_recovery_gap",
        ):
            self.assertIn(pid, params.REGISTRY)

    def test_defaults_match_module_constants(self):
        # The registry must not silently fork the engine's documented defaults.
        self.assertEqual(params.REGISTRY["recovery.weights.hrv"]["default"], recovery.RECOVERY_WEIGHTS["hrv"])
        self.assertEqual(
            params.REGISTRY["recovery.baseline_window_days"]["default"], recovery.DEFAULT_BASELINE_WINDOW_DAYS
        )
        self.assertEqual(params.REGISTRY["recovery.hrv_full_swing_sd"]["default"], recovery._HRV_FULL_SWING_SD)
        self.assertEqual(params.REGISTRY["recovery.sleep_need_h"]["default"], recovery.DEFAULT_SLEEP_NEED_H)
        self.assertEqual(params.REGISTRY["correlations.min_yes_days"]["default"], correlations.MIN_YES_DAYS)
        self.assertEqual(params.REGISTRY["correlations.window_days"]["default"], correlations.DEFAULT_WINDOW_DAYS)
        self.assertEqual(
            params.REGISTRY["insights.hrv_drop_attention_pct"]["default"], insights.HRV_DROP_ATTENTION_PCT
        )
        self.assertEqual(params.REGISTRY["insights.red_streak_days"]["default"], insights.RED_STREAK_DAYS)


class GetSetResetTests(ParamsHomeMixin):
    def test_get_returns_default_without_file(self):
        self.assertFalse(params.params_path().is_file())
        self.assertEqual(params.get("recovery.baseline_window_days"), 28)
        self.assertEqual(params.get("recovery.weights.hrv"), 0.60)

    def test_get_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            params.get("nope.nope")

    def test_set_persists_and_get_reads_override(self):
        params.set("correlations.min_yes_days", 3)
        self.assertEqual(params.get("correlations.min_yes_days"), 3)
        # 0600 on the file (private by construction).
        mode = params.params_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_set_validates_range_and_type(self):
        with self.assertRaises(ValueError):
            params.set("correlations.min_yes_days", 99)
        with self.assertRaises(ValueError):
            params.set("recovery.weights.hrv", -1)
        with self.assertRaises(ValueError):
            params.set("recovery.weights.hrv", "a lot")
        with self.assertRaises(KeyError):
            params.set("nope.nope", 1)

    def test_set_coerces_int_params(self):
        self.assertEqual(params.set("insights.red_streak_days", 4.0), 4)
        self.assertIsInstance(params.get("insights.red_streak_days"), int)

    def test_set_back_to_default_clears_override(self):
        params.set("correlations.min_yes_days", 3)
        params.set("correlations.min_yes_days", 5)  # the default
        self.assertEqual(params.load_overrides(), {})

    def test_reset_one_and_all(self):
        params.set("correlations.min_yes_days", 3)
        params.set("correlations.min_no_days", 4)
        self.assertEqual(params.reset("correlations.min_yes_days"), 1)
        self.assertEqual(params.get("correlations.min_yes_days"), 5)
        self.assertEqual(params.get("correlations.min_no_days"), 4)
        self.assertEqual(params.reset(), 1)  # drops the rest
        self.assertEqual(params.load_overrides(), {})
        self.assertFalse(params.params_path().is_file())
        with self.assertRaises(KeyError):
            params.reset("nope.nope")

    def test_corrupt_or_alien_file_is_ignored(self):
        home = Path(self._tmp.name)
        (home / "params.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(params.get("recovery.weights.hrv"), 0.60)
        (home / "params.json").write_text(
            json.dumps({"unknown.param": 1, "recovery.weights.hrv": 99.0, "correlations.min_no_days": 4}),
            encoding="utf-8",
        )
        # Unknown id and out-of-range value are dropped; the valid one survives.
        self.assertEqual(params.load_overrides(), {"correlations.min_no_days": 4})
        self.assertEqual(params.get("recovery.weights.hrv"), 0.60)


class ListAllAndHelpersTests(ParamsHomeMixin):
    def test_list_all_reports_value_default_overridden(self):
        params.set("insights.hrv_drop_attention_pct", 12)
        rows = {row["id"]: row for row in params.list_all()}
        self.assertEqual(set(rows), set(params.REGISTRY))
        changed = rows["insights.hrv_drop_attention_pct"]
        self.assertTrue(changed["overridden"])
        self.assertEqual(changed["value"], 12)
        self.assertEqual(changed["default"], 8.0)
        untouched = rows["recovery.weights.hrv"]
        self.assertFalse(untouched["overridden"])
        self.assertEqual(untouched["value"], untouched["default"])
        for key in ("label_ru", "unit", "min", "max", "step", "where", "affects", "doc", "group", "wired"):
            self.assertIn(key, untouched)

    def test_recovery_weight_normalization_rule(self):
        # Weights are relative: shares always renormalize to exactly 1.
        params.set("recovery.weights.hrv", 0.90)
        norm = params.recovery_weights_normalized()
        self.assertAlmostEqual(sum(norm.values()), 1.0, places=9)
        self.assertGreater(norm["hrv"], 0.60)
        rows = {row["id"]: row for row in params.list_all()}
        shares = [rows[w]["normalized_share"] for w in params.RECOVERY_WEIGHT_IDS]
        self.assertAlmostEqual(sum(shares), 1.0, places=2)

    def test_overrides_for_and_stamp(self):
        self.assertEqual(params.overrides_for(("recovery.weights.hrv",)), {})
        self.assertEqual(params.stamp("x@v1", {}), "x@v1")
        params.set("recovery.weights.hrv", 0.90)
        ov = params.overrides_for(("recovery.weights.hrv", "recovery.weights.rhr"))
        self.assertEqual(ov, {"recovery.weights.hrv": 0.90})
        self.assertEqual(params.stamp("x@v1", ov), "x@v1+custom")
        self.assertEqual(params.stamp("x@v1+custom", ov), "x@v1+custom")


class RecoveryOverrideEffectTests(ParamsHomeMixin):
    def _score(self):
        return recovery.recovery_score(
            hrv_ms=60.0, baseline_hrv_ms=50.0, rhr_bpm=55.0, baseline_rhr_bpm=50.0,
            sleep_performance_pct=90.0, baseline_hrv_ln_sd=0.15,
        )

    def test_default_run_has_no_custom_stamp(self):
        rec = self._score()
        self.assertEqual(rec["algo_version"], "recovery_score@v3")
        self.assertNotIn("params_overrides", rec)

    def test_weight_override_changes_score_and_is_stamped(self):
        before = self._score()
        params.set("recovery.weights.hrv", 0.90)
        after = self._score()
        self.assertNotEqual(before["score"], after["score"])
        self.assertEqual(after["algo_version"], "recovery_score@v3+custom")
        self.assertEqual(after["params_overrides"], {"recovery.weights.hrv": 0.90})
        # Normalization rule: effective shares still sum to 1.
        self.assertAlmostEqual(sum(after["weights_used"].values()), 1.0, places=3)

    def test_full_swing_override_changes_hrv_component(self):
        before = recovery.hrv_component(60.0, 50.0, baseline_ln_sd=0.15)
        params.set("recovery.hrv_full_swing_sd", 1.0)
        after = recovery.hrv_component(60.0, 50.0, baseline_ln_sd=0.15)
        self.assertGreater(after, before)  # smaller full swing -> steeper response

    def test_sleep_need_override_reaches_sleep_debt_metric(self):
        module = recovery.RecoveryModule()
        payload = {
            "date": "2026-06-01", "hrv_ms": 55.0, "baseline_hrv_ms": 50.0, "actual_sleep_h": 7.0,
        }
        default_result = module.compute(dict(payload))
        debt = next(m for m in default_result.metrics if m["metric_name"] == "sleep_debt_h")
        self.assertEqual(debt["metadata"]["need_h"], 8.0)
        self.assertNotIn("params_overrides", debt["metadata"])

        params.set("recovery.sleep_need_h", 9.0)
        custom_result = module.compute(dict(payload))
        debt = next(m for m in custom_result.metrics if m["metric_name"] == "sleep_debt_h")
        self.assertEqual(debt["metadata"]["need_h"], 9.0)
        self.assertEqual(debt["metadata"]["sleep_debt_h"], 2.0)
        self.assertEqual(debt["metadata"]["params_overrides"], {"recovery.sleep_need_h": 9.0})
        self.assertEqual(debt["metadata"]["algo_version"], "sleep_debt@v2+custom")


class CorrelationsOverrideEffectTests(ParamsHomeMixin):
    @staticmethod
    def _pairs(n_yes, n_no):
        pairs = []
        for i in range(n_yes):
            pairs.append({"date": "2026-05-%02d" % (i + 1), "yes": True, "recovery": 80.0})
        for i in range(n_no):
            pairs.append({"date": "2026-05-%02d" % (i + 15), "yes": False, "recovery": 60.0})
        return pairs

    def test_threshold_override_admits_thin_behavior(self):
        pairs = self._pairs(4, 5)
        self.assertIsNone(correlations.behavior_impact(pairs))  # default 5/5
        params.set("correlations.min_yes_days", 3)
        stats = correlations.behavior_impact(pairs)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["min_yes_required"], 3)
        self.assertEqual(stats["min_no_required"], 5)

    def test_insight_carries_trace_and_override_stamp(self):
        behaviors = [{"behavior_id": "alcohol", "behavior_name": "Alcohol", "pairs": self._pairs(6, 6)}]
        default_meta = correlations.analyze(behaviors)[0]["metadata"]
        self.assertEqual(default_meta["algo_version"], "behavior_impact@v1")
        self.assertNotIn("params_overrides", default_meta)
        trace = default_meta["compute_trace"]
        self.assertEqual(trace["yes_days"], 6)
        self.assertEqual(trace["no_days"], 6)
        self.assertEqual(trace["avg_yes"], 80.0)
        self.assertEqual(trace["avg_no"], 60.0)
        self.assertEqual(trace["window_days"], 90)
        self.assertEqual(trace["min_yes_required"], 5)
        self.assertEqual(trace["min_no_required"], 5)

        params.set("correlations.min_yes_days", 3)
        custom_meta = correlations.analyze(behaviors)[0]["metadata"]
        self.assertEqual(custom_meta["algo_version"], "behavior_impact@v1+custom")
        self.assertEqual(custom_meta["params_overrides"], {"correlations.min_yes_days": 3})
        self.assertEqual(custom_meta["compute_trace"]["min_yes_required"], 3)

    def test_compute_uses_window_param(self):
        params.set("correlations.window_days", 60)
        module = correlations.CorrelationsModule()
        result = module.compute({"behaviors": [
            {"behavior_id": "alcohol", "pairs": self._pairs(6, 6)},
        ]})
        self.assertEqual(result.insights[0]["metadata"]["window_days"], 60)


class InsightsOverrideEffectTests(ParamsHomeMixin):
    @staticmethod
    def _hrv_daily(drop_pct):
        # 28 days of HRV: baseline 50 ms, last 7 days dropped by drop_pct.
        daily = {}
        for i in range(1, 29):
            value = 50.0 * (1 - drop_pct / 100.0) if i > 21 else 50.0
            daily["2026-05-%02d" % i] = {"hrv": value}
        return daily

    def test_default_trace_is_present(self):
        ins = insights.detect_hrv_downtrend(self._hrv_daily(10.0), {})
        self.assertIsNotNone(ins)
        trace = ins.data["trace"]
        self.assertEqual(trace["threshold_used"], 8.0)
        self.assertAlmostEqual(trace["observed_value"], 10.0, places=1)
        self.assertEqual(trace["window"], "7d vs 14-28d")
        self.assertNotIn("params_overrides", ins.data)
        # Trace survives serialization for the UI tooltip.
        self.assertIn("trace", ins.to_dict()["data"])

    def test_hrv_threshold_override_changes_detection(self):
        daily = self._hrv_daily(10.0)
        self.assertIsNotNone(insights.detect_hrv_downtrend(daily, {}))
        params.set("insights.hrv_drop_attention_pct", 12)
        self.assertIsNone(insights.detect_hrv_downtrend(daily, {}))

    def test_hrv_warning_threshold_override_raises_severity(self):
        daily = self._hrv_daily(10.0)
        self.assertEqual(insights.detect_hrv_downtrend(daily, {}).severity, insights.ATTENTION)
        params.set("insights.hrv_drop_warning_pct", 9)
        ins = insights.detect_hrv_downtrend(daily, {})
        self.assertEqual(ins.severity, insights.WARNING)
        self.assertEqual(ins.data["trace"]["threshold_used"], 9)
        self.assertEqual(ins.data["params_overrides"], {"insights.hrv_drop_warning_pct": 9})

    def test_sleep_goal_param_feeds_sleep_debt_detector(self):
        daily = {"2026-05-%02d" % i: {"sleep_h": 7.5} for i in range(1, 8)}
        self.assertIsNone(insights.detect_sleep_debt(daily, {}))  # 3.5h < 5h
        params.set("insights.sleep_goal_h", 9.0)  # debt becomes 10.5h
        ins = insights.detect_sleep_debt(daily, {})
        self.assertIsNotNone(ins)
        self.assertEqual(ins.severity, insights.WARNING)
        self.assertEqual(ins.data["goal_h"], 9.0)
        self.assertEqual(ins.data["trace"]["observed_value"], 10.5)
        # An explicit user goal still wins over the param.
        self.assertIsNone(insights.detect_sleep_debt(daily, {"sleep_h": 7.5}))

    def test_red_streak_length_override(self):
        daily = {"2026-05-%02d" % i: {"recovery": 30.0 if i <= 2 else 70.0} for i in range(1, 8)}
        self.assertIsNone(insights.detect_recovery_red_streak(daily, {}))  # 2 < 3
        params.set("insights.red_streak_days", 2)
        ins = insights.detect_recovery_red_streak(daily, {})
        self.assertIsNotNone(ins)
        self.assertEqual(ins.data["trace"], {"threshold_used": 2, "observed_value": 2, "window": "7d series"})

    def test_weekend_and_consistency_traces(self):
        weekend_daily = {
            "2026-06-01": {"recovery": 70}, "2026-06-02": {"recovery": 72},  # Mon, Tue
            "2026-06-06": {"recovery": 55}, "2026-06-07": {"recovery": 57},  # Sat, Sun
        }
        ins = insights.detect_weekend_pattern(weekend_daily, {})
        self.assertIsNotNone(ins)
        self.assertEqual(ins.data["trace"]["threshold_used"], 5.0)
        sleep_daily = {"2026-05-%02d" % i: {"sleep_h": 5.0 + (i % 2) * 4.0} for i in range(1, 15)}
        ins = insights.detect_sleep_consistency(sleep_daily, {})
        self.assertIsNotNone(ins)
        self.assertEqual(ins.data["trace"]["threshold_used"], 1.2)
        self.assertEqual(ins.data["trace"]["window"], "14 nights")


if __name__ == "__main__":
    unittest.main()
