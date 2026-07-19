import unittest

from openhealth import workout_notes

# --- Russian fixture vocabulary ----------------------------------------------
#
# The parser accepts notes in English and Russian, and echoes the exercise name
# back verbatim, so these fixtures have to stay Cyrillic to prove that path.
# They are written as \u escapes to keep this file ASCII; the comment after each
# one is the English it stands for.
RU_PRESS = "\u0436\u0438\u043c"                  # "press"
RU_ROW = "\u0442\u044f\u0433\u0430"                    # "row"
RU_KG = "\u043a\u0433"                            # "kg"
RU_FOR = "\u043d\u0430"                           # "na" - "for"/"by"
RU_WEIGHT = "\u0432\u0435\u0441"                  # "ves" - "weight"
RU_BAR = "\u0433\u0440\u0438\u0444"              # "grif" - the bare olympic bar
RU_X = "\u0445"                                # Cyrillic "x", a multiplication sign
RU_CHEST_PRESS = "\u0413\u0440\u0443\u0434\u044c \u0436\u0438\u043c"    # "Chest press"
RU_CIRCUIT = "\u041a\u0440\u0443\u0433\u043e\u0432\u0430\u044f"      # "Circuit"
# "Cable fly to mid-chest on the black machine"
RU_FLY = ("\u0421\u0432\u0435\u0434\u0435\u043d\u0438\u0435 \u043d\u0430 \u0441\u0435\u0440\u0435\u0434\u0438\u043d\u0443 \u0433\u0440\u0443\u0434\u0438 \u0432 \u0442\u0440\u0435\u043d\u0430\u0436\u0435\u0440\u0435 \u0447\u0435\u0440\u043d\u043e\u043c")
RU_FLY_SHORT = "\u0421\u0432\u0435\u0434\u0435\u043d\u0438\u0435 \u0432 \u0442\u0440\u0435\u043d\u0430\u0436\u0435\u0440\u0435"  # "Cable fly on the machine"
RU_TIRED_NOTE = "\u0443\u0441\u0442\u0430\u043b, \u0431\u043e\u043b\u0435\u043b\u043e \u043f\u043b\u0435\u0447\u043e"  # "tired, shoulder hurt"

BIOS_LINE = (
    "%s %s/10/12.5/12.5/12.5 %s %s 25/15/10/10/7. "
    % (RU_CHEST_PRESS, RU_BAR, RU_WEIGHT, RU_FOR)
    + "%s: %s 22 %s %s 12/12/10" % (RU_CIRCUIT, RU_FLY, RU_KG, RU_FOR)
)


class ParseFormatsTests(unittest.TestCase):
    def test_ru_compact_multiplication(self):
        out = workout_notes.parse_workout_note("%s 40%s\u00d710" % (RU_PRESS, RU_KG))
        self.assertEqual(len(out["exercises"]), 1)
        ex = out["exercises"][0]
        self.assertEqual(ex["exercise"], RU_PRESS)
        self.assertEqual(ex["sets"], [{"weight_kg": 40.0, "reps": 10}])
        self.assertEqual(out["notes"], [])

    def test_en_format(self):
        out = workout_notes.parse_workout_note("bench 40kg x10")
        ex = out["exercises"][0]
        self.assertEqual(ex["exercise"], "bench")
        self.assertEqual(ex["sets"], [{"weight_kg": 40.0, "reps": 10}])

    def test_bare_weight_reps_without_exercise(self):
        out = workout_notes.parse_workout_note("20 %s x 25" % RU_KG)
        ex = out["exercises"][0]
        self.assertEqual(ex["exercise"], "")
        self.assertEqual(ex["sets"], [{"weight_kg": 20.0, "reps": 25}])

    def test_fixed_weight_rep_list(self):
        out = workout_notes.parse_workout_note(
            "%s 22 %s %s 12/12/10" % (RU_FLY_SHORT, RU_KG, RU_FOR)
        )
        ex = out["exercises"][0]
        self.assertEqual(ex["exercise"], RU_FLY_SHORT)
        self.assertEqual([s["reps"] for s in ex["sets"]], [12, 12, 10])
        self.assertTrue(all(s["weight_kg"] == 22.0 for s in ex["sets"]))

    def test_comma_separated_sets(self):
        out = workout_notes.parse_workout_note(
            "%s 40%s\u00d710, 45%s\u00d78" % (RU_PRESS, RU_KG, RU_KG)
        )
        ex = out["exercises"][0]
        self.assertEqual(ex["exercise"], RU_PRESS)
        self.assertEqual(ex["sets"], [{"weight_kg": 40.0, "reps": 10}, {"weight_kg": 45.0, "reps": 8}])

    def test_cyrillic_x_and_decimal_comma(self):
        out = workout_notes.parse_workout_note("%s 12,5 %s %s 12" % (RU_ROW, RU_KG, RU_X))
        ex = out["exercises"][0]
        self.assertEqual(ex["exercise"], RU_ROW)
        self.assertEqual(ex["sets"], [{"weight_kg": 12.5, "reps": 12}])

    def test_bios_battle_line(self):
        out = workout_notes.parse_workout_note(BIOS_LINE)
        self.assertEqual(len(out["exercises"]), 2)
        self.assertEqual(out["notes"], [])

        press = out["exercises"][0]
        self.assertEqual(press["exercise"], RU_CHEST_PRESS)
        self.assertEqual(len(press["sets"]), 5)
        # The bare bar is counted as 20 kg, with BAR_LABEL preserved on the set.
        self.assertEqual(
            press["sets"][0],
            {"weight_kg": 20.0, "label": workout_notes.BAR_LABEL, "reps": 25},
        )
        self.assertEqual([s["weight_kg"] for s in press["sets"]], [20.0, 10.0, 12.5, 12.5, 12.5])
        self.assertEqual([s["reps"] for s in press["sets"]], [25, 15, 10, 10, 7])

        fly = out["exercises"][1]
        # The "Circuit:" prefix is context; the exercise is what follows the colon.
        self.assertEqual(fly["exercise"], RU_FLY)
        self.assertEqual([s["reps"] for s in fly["sets"]], [12, 12, 10])
        self.assertTrue(all(s["weight_kg"] == 22.0 for s in fly["sets"]))

    def test_unknown_lines_go_to_notes(self):
        out = workout_notes.parse_workout_note(
            "%s 40%s x10\n%s" % (RU_PRESS, RU_KG, RU_TIRED_NOTE)
        )
        self.assertEqual(len(out["exercises"]), 1)
        self.assertEqual(out["notes"], [RU_TIRED_NOTE])

    def test_never_raises_on_garbage(self):
        for text in ("", "   ", None, "...", "a/b/c %s x/y" % RU_FOR, "12345"):
            out = workout_notes.parse_workout_note(text)
            self.assertIn("exercises", out)
            self.assertIn("notes", out)

    def test_unpaired_lists_warn_not_crash(self):
        out = workout_notes.parse_workout_note("%s 40/45/50 %s 10/8" % (RU_PRESS, RU_FOR))
        ex = out["exercises"][0]
        self.assertEqual(len(ex["sets"]), 2)
        self.assertTrue(ex["warnings"])


class SummarizeTests(unittest.TestCase):
    def test_volume_and_top_exercises(self):
        parsed = workout_notes.parse_workout_note(BIOS_LINE)
        summary = workout_notes.summarize_workouts(parsed)
        # Press: 20*25 + 10*15 + 12.5*(10+10+7) = 987.5; fly: 22*34 = 748.
        self.assertEqual(summary["total_volume_kg"], 1735.5)
        self.assertEqual(summary["exercise_count"], 2)
        self.assertEqual(summary["set_count"], 8)
        self.assertEqual(summary["top_exercises"][0], RU_CHEST_PRESS)
        by_name = {e["exercise"]: e for e in summary["exercises"]}
        self.assertEqual(by_name[RU_CHEST_PRESS]["volume_kg"], 987.5)
        self.assertEqual(by_name[RU_FLY]["volume_kg"], 748.0)

    def test_accepts_bare_exercise_list_and_empty(self):
        summary = workout_notes.summarize_workouts([])
        self.assertEqual(summary["total_volume_kg"], 0)
        summary = workout_notes.summarize_workouts(
            [{"exercise": RU_PRESS, "sets": [{"weight_kg": 40.0, "reps": 10}]}]
        )
        self.assertEqual(summary["total_volume_kg"], 400.0)


if __name__ == "__main__":
    unittest.main()
