import json
import tempfile
import unittest
from pathlib import Path

from openhealth import index, nutrition_label


def _nutrient(label, value, unit="գ"):
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "raw_row_text": "%s %s %s" % (label, value, unit),
    }


def _energy(value, label="Էներգետիկ արժեք", unit="կկալ"):
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "raw_row_text": "%s՝ %s %s" % (label, value, unit),
    }


def _sync_raw_label(payload):
    rows = [payload["product_name_original"]]
    if payload.get("basis_text"):
        rows.append(payload["basis_text"])
    if payload.get("package_raw_row_text"):
        rows.append(payload["package_raw_row_text"])
    if payload.get("serving_raw_row_text"):
        rows.append(payload["serving_raw_row_text"])
    energy = payload.get("energy")
    if isinstance(energy, dict) and energy.get("raw_row_text"):
        rows.append(energy["raw_row_text"])
    rows.extend(row["raw_row_text"] for row in payload.get("nutrients") or [] if row.get("raw_row_text"))
    payload["raw_label_text"] = "\n".join(rows)
    return payload


def _armenian_label(**overrides):
    payload = {
        "mode": "nutrition_label",
        "product_name_original": "Կանաչ ոլոռի կրեմ-ապուր",
        "product_name_ru": "Крем-суп из зелёного горошка",
        "language": "hy",
        "raw_label_text": "",
        "nutrition_basis": "per_container",
        "basis_text": "ամբողջ փաթեթի համար",
        "package_amount": 220,
        "package_unit": "գ",
        "package_raw_row_text": "Զտաքաշ՝ 220 գ",
        "serving_amount": None,
        "serving_unit": None,
        "serving_raw_row_text": None,
        "energy": _energy(305.4),
        "nutrients": [
            _nutrient("Սպ.", 17.3),
            _nutrient("Ճ.", 15.8),
            _nutrient("Ածխ.", 23.5),
        ],
        "confidence": "high",
        "uncertainties": [],
        "provenance": {"provider": "synthetic-test", "model": "none"},
    }
    payload.update(overrides)
    if "package_raw_row_text" not in overrides:
        payload["package_raw_row_text"] = (
            "Զտաքաշ՝ %s %s" % (payload["package_amount"], payload["package_unit"])
            if payload.get("package_amount") and payload.get("package_unit")
            else None
        )
    if "serving_raw_row_text" not in overrides:
        payload["serving_raw_row_text"] = (
            "Չափաբաժին՝ %s %s" % (payload["serving_amount"], payload["serving_unit"])
            if payload.get("serving_amount") and payload.get("serving_unit")
            else None
        )
    if "raw_label_text" not in overrides:
        _sync_raw_label(payload)
    return payload


def _unknown_macro_label(**overrides):
    return _armenian_label(
        nutrients=[
            _nutrient("ც", 17.3),
            _nutrient("ცხ", 15.8),
            _nutrient("ნახ", 23.5),
        ],
        **overrides,
    )


def _mapping_confirmation(challenge, **overrides):
    confirmation = {
        "schema_version": 1,
        "source": "user_reply",
        "confirmation_id": "synthetic-reply-1",
        "text": "Подтверждаю БЖУ",
        "artifact_ids": ["synthetic-reply-artifact-1"],
        "challenge_sha256": challenge["challenge_sha256"],
    }
    confirmation.update(overrides)
    return confirmation


class LabelNormalizationTests(unittest.TestCase):
    def test_armenian_rows_map_without_model_field_order(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label())

        self.assertEqual(
            label["declared"],
            {"kcal": 305.4, "protein_g": 17.3, "fat_g": 15.8, "carb_g": 23.5},
        )
        self.assertEqual(label["basis"], nutrition_label.BASIS_PER_CONTAINER)
        self.assertEqual(label["title"], "Կանաչ ոլոռի կրեմ-ապուր")
        self.assertEqual(label["product_name_ru"], "Крем-суп из зелёного горошка")
        self.assertEqual(label["package"]["amount"], 220.0)
        self.assertEqual(label["package"]["unit"], "g")
        self.assertEqual(label["package"]["raw_unit"], "գ")
        self.assertEqual(label["package"]["raw_row_text"], "Զտաքաշ՝ 220 գ")
        self.assertEqual(label["confidence_level"], "C2")
        self.assertEqual(label["model_confidence"], "high")
        self.assertEqual(label["validation"]["status"], "consistent")
        self.assertEqual(label["basis_verification"]["status"], "verified")
        self.assertEqual(len(label["provenance"]["extraction_sha256"]), 64)
        self.assertEqual(len(label["provenance"]["provenance_sha256"]), 64)

        round_tripped = json.loads(json.dumps(label, ensure_ascii=False))
        self.assertEqual(
            nutrition_label.validate_normalized_label(round_tripped)["declared"],
            label["declared"],
        )

    def test_multiple_armenian_macros_can_share_one_literal_row(self):
        self.assertEqual(
            nutrition_label.canonical_nutrient("Ս."),
            "protein_g",
        )
        macro_row = "Ս՝ 10 գՃ՝ 5 գԱծխ՝ 20 գ"
        payload = _armenian_label(
            nutrition_basis="unknown",
            basis_text="",
            energy=_energy(165),
            nutrients=[
                {
                    "label": "Ս",
                    "value": 10,
                    "unit": "գ",
                    "raw_row_text": macro_row,
                },
                {
                    "label": "Ճ",
                    "value": 5,
                    "unit": "գ",
                    "raw_row_text": macro_row,
                },
                {
                    "label": "Ածխ",
                    "value": 20,
                    "unit": "գ",
                    "raw_row_text": macro_row,
                },
            ],
        )
        payload["raw_label_text"] = "\n".join(
            [
                payload["product_name_original"],
                payload["package_raw_row_text"],
                payload["energy"]["raw_row_text"],
                macro_row,
            ]
        )

        label = nutrition_label.normalize_label_extraction(payload)

        self.assertEqual(
            label["declared"],
            {
                "kcal": 165.0,
                "protein_g": 10.0,
                "fat_g": 5.0,
                "carb_g": 20.0,
            },
        )
        self.assertEqual(label["basis"], nutrition_label.BASIS_UNKNOWN)

    def test_energy_and_macros_accept_unique_whitespace_reconciled_rows(
        self,
    ):
        energy_and_protein = "Energy: 230 kcalProtein: 20 g"
        fat_and_carbs = "Fat: 10 gCarbohydrates: 15 g"
        payload = _armenian_label(
            product_name_original="Synthetic bar A",
            product_name_ru="Синтетический батончик А",
            language="en",
            nutrition_basis="per_container",
            basis_text="Nutrition per container",
            package_amount=180,
            package_unit="g",
            package_raw_row_text="Net weight: 180 g",
            energy={
                "label": "Energy",
                "value": 230,
                "unit": "kcal",
                "raw_row_text": energy_and_protein,
            },
            nutrients=[
                {
                    "label": "Protein",
                    "value": 20,
                    "unit": "g",
                    "raw_row_text": energy_and_protein,
                },
                {
                    "label": "Fat",
                    "value": 10,
                    "unit": "g",
                    "raw_row_text": fat_and_carbs,
                },
                {
                    "label": "Carbohydrates",
                    "value": 15,
                    "unit": "g",
                    "raw_row_text": fat_and_carbs,
                },
            ],
        )
        first_source_row = "Energy: 230 kcal\nProtein: 20 g"
        second_source_row = "Fat: 10 g\nCarbohydrates: 15 g"
        payload["raw_label_text"] = "\n".join(
            [
                "Synthetic bar A",
                "Net weight: 180 g",
                "Nutrition per container",
                first_source_row,
                second_source_row,
            ]
        )

        label = nutrition_label.normalize_label_extraction(payload)

        self.assertEqual(
            label["declared"],
            {
                "kcal": 230.0,
                "protein_g": 20.0,
                "fat_g": 10.0,
                "carb_g": 15.0,
            },
        )
        self.assertEqual(
            label["energy"]["raw_row_text"],
            first_source_row,
        )
        self.assertEqual(
            label["raw_nutrients"][0]["raw_row_text"],
            first_source_row,
        )
        self.assertEqual(
            label["raw_nutrients"][1]["raw_row_text"],
            second_source_row,
        )

    def test_energy_prior_number_requires_exact_kilojoule_bridge(self):
        valid_row = "Energy: 1278 kJ / 305.4 kcal"
        payload = _armenian_label(
            energy={
                "label": "Energy",
                "value": 305.4,
                "unit": "kcal",
                "raw_row_text": valid_row,
            },
        )

        label = nutrition_label.normalize_label_extraction(payload)

        self.assertEqual(label["declared"]["kcal"], 305.4)
        self.assertEqual(label["energy"]["raw_row_text"], valid_row)

        unrelated_field = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        invalid_row = (
            "Energy: 50 kJ; Calories from fat: 305.4 kcal"
        )
        unrelated_field["raw_label_text"] = (
            unrelated_field["raw_label_text"].replace(
                valid_row,
                invalid_row,
            )
        )
        unrelated_field["energy"]["raw_row_text"] = invalid_row
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.normalize_label_extraction(
                unrelated_field
            )

        for duplicate_row in (
            "Energy: 305.4 kcal / 305.4 kcal",
            "Energy: 1278 kJ / 305.4 kcal / 305.4 kcal",
            "Energy: 50 kJ / 99% / 305.4 kcal",
            "Energy: 50 kJ / 2 / 3 / 305.4 kcal",
            "Energy: 1278 kJ / -305.4 kcal",
            "Energy: 1278 kJ / −305.4 kcal",
            "Energy: 1278 kJ / 305.4% kcal",
            "Energy: <305.4 kcal",
            "Energy: ≈305.4 kcal",
            "Energy: -1278 kJ / 305.4 kcal",
            "Energy: 1278% kJ / 305.4 kcal",
            "Energy: 305.4 kcal%",
            "Energy: ⁻305.4 kcal",
            "Energy: ₋305.4 kcal",
            "Energy: ➖305.4 kcal",
            "Energy: ＋305.4 kcal",
            "Energy: %305.4 kcal",
            "Energy: 305.4﹪ kcal",
            "Energy: 305.4؉ kcal",
            "Energy: 305.4؊ kcal",
            "Energy: ≠305.4 kcal",
            "Energy: ≢305.4 kcal",
            "Energy: ≃305.4 kcal",
            "Energy: ≐305.4 kcal",
            "Energy: ≟305.4 kcal",
            "Energy: 1278 kJ / ≠305.4 kcal",
            "Energy: ≠1278 kJ / 305.4 kcal",
            "Energy: –305.4 kcal",
            "Energy: –1278 kJ / 305.4 kcal",
            "Energy: 305.4̸ kcal",
            "Energy: 305.4 kcal(%)",
            "Energy: 305.4 kcal​%",
            "Energy: 305.4 kcal*%",
            "Energy: –(305.4) kcal",
            "Energy: —[305.4] kcal",
            "Energy: 305.4 kcal–",
        ):
            with self.subTest(duplicate_row=duplicate_row):
                duplicate = json.loads(
                    json.dumps(payload, ensure_ascii=False)
                )
                duplicate["raw_label_text"] = (
                    duplicate["raw_label_text"].replace(
                        valid_row,
                        duplicate_row,
                    )
                )
                duplicate["energy"]["raw_row_text"] = duplicate_row
                with self.assertRaises(
                    nutrition_label.NutritionLabelNeedsClarification
                ):
                    nutrition_label.normalize_label_extraction(
                        duplicate
                    )

    def test_energy_and_all_macros_can_share_one_reconciled_row(self):
        shared_row = (
            "Energy: 208 kcal,Protein: 12 g,"
            "Fat: 8 g,Carbohydrates: 22 g"
        )
        source_row = (
            "Energy: 208 kcal, Protein: 12 g,\n"
            "Fat: 8 g, Carbohydrates: 22 g"
        )
        payload = _armenian_label(
            product_name_original="Synthetic bar B",
            product_name_ru="Синтетический батончик Б",
            language="en",
            nutrition_basis="unknown",
            basis_text="",
            package_amount=250,
            package_unit="g",
            package_raw_row_text="Net weight: 250 g",
            energy={
                "label": "Energy",
                "value": 208,
                "unit": "kcal",
                "raw_row_text": shared_row,
            },
            nutrients=[
                {
                    "label": "Protein",
                    "value": 12,
                    "unit": "g",
                    "raw_row_text": shared_row,
                },
                {
                    "label": "Fat",
                    "value": 8,
                    "unit": "g",
                    "raw_row_text": shared_row,
                },
                {
                    "label": "Carbohydrates",
                    "value": 22,
                    "unit": "g",
                    "raw_row_text": shared_row,
                },
            ],
        )
        payload["raw_label_text"] = "\n".join(
            [
                "Synthetic bar B",
                "Net weight: 250 g",
                source_row,
            ]
        )

        label = nutrition_label.normalize_label_extraction(payload)

        self.assertEqual(
            label["declared"],
            {
                "kcal": 208.0,
                "protein_g": 12.0,
                "fat_g": 8.0,
                "carb_g": 22.0,
            },
        )
        self.assertEqual(label["energy"]["raw_row_text"], source_row)
        self.assertTrue(
            all(
                row["raw_row_text"] == source_row
                for row in label["raw_nutrients"]
            )
        )
        round_tripped = json.loads(
            json.dumps(label, ensure_ascii=False)
        )
        self.assertEqual(
            nutrition_label.validate_normalized_label(
                round_tripped
            )["declared"],
            label["declared"],
        )

    def test_shared_row_can_expand_one_unique_missing_leading_label(
        self,
    ):
        energy_and_protein = "Energy: 230 kcalProtein: 20 g"
        fat_and_carbs_without_label = (
            "10 gCarbohydrates: 15 g"
        )
        first_source_row = "Energy: 230 kcal\nProtein: 20 g"
        second_source_row = "Fat: 10 g\nCarbohydrates: 15 g"
        payload = _armenian_label(
            product_name_original="Synthetic bar C",
            product_name_ru="Синтетический батончик В",
            language="en",
            nutrition_basis="unknown",
            basis_text="",
            package_amount=180,
            package_unit="g",
            package_raw_row_text="Net weight: 180 g",
            energy={
                "label": "Energy",
                "value": 230,
                "unit": "kcal",
                "raw_row_text": energy_and_protein,
            },
            nutrients=[
                {
                    "label": "Protein",
                    "value": 20,
                    "unit": "g",
                    "raw_row_text": energy_and_protein,
                },
                {
                    "label": "Fat",
                    "value": 10,
                    "unit": "g",
                    "raw_row_text": fat_and_carbs_without_label,
                },
                {
                    "label": "Carbohydrates",
                    "value": 15,
                    "unit": "g",
                    "raw_row_text": fat_and_carbs_without_label,
                },
            ],
        )
        payload["raw_label_text"] = "\n".join(
            [
                "Synthetic bar C",
                "Net weight: 180 g",
                first_source_row,
                second_source_row,
            ]
        )

        label = nutrition_label.normalize_label_extraction(payload)

        self.assertEqual(
            label["declared"],
            {
                "kcal": 230.0,
                "protein_g": 20.0,
                "fat_g": 10.0,
                "carb_g": 15.0,
            },
        )
        self.assertEqual(
            label["raw_nutrients"][1]["raw_row_text"],
            second_source_row,
        )
        self.assertEqual(
            label["raw_nutrients"][2]["raw_row_text"],
            second_source_row,
        )

        cross_field_capture = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        cross_field_capture["raw_label_text"] = (
            cross_field_capture["raw_label_text"].replace(
                second_source_row,
                "Fat: 10 g, Carbohydrates: 15 g",
            )
        )
        cross_field_capture["nutrients"][1]["raw_row_text"] = (
            "Carbohydrates: 15 g"
        )
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as raised:
            nutrition_label.normalize_label_extraction(
                cross_field_capture
            )
        self.assertEqual(
            raised.exception.code,
            "nutrient_raw_row_not_target",
        )

        unbound_prefix = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        unbound_prefix["raw_label_text"] = (
            unbound_prefix["raw_label_text"].replace(
                second_source_row,
                "Fibre: 10 g\nCarbohydrates: 15 g",
            )
        )
        del unbound_prefix["nutrients"][1]
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.normalize_label_extraction(
                unbound_prefix
            )

    def test_whitespace_reconciliation_stays_unambiguous_and_exact(self):
        shared_row = (
            "Energy: 208 kcal,Protein: 12 g,"
            "Fat: 8 g,Carbohydrates: 22 g"
        )
        source_row = (
            "Energy: 208 kcal, Protein: 12 g,\n"
            "Fat: 8 g, Carbohydrates: 22 g"
        )
        payload = _armenian_label(
            product_name_original="Synthetic duplicate label",
            product_name_ru="Синтетическая повторяющаяся этикетка",
            language="en",
            nutrition_basis="unknown",
            basis_text="",
            package_amount=250,
            package_unit="g",
            package_raw_row_text="Net weight: 250 g",
            energy={
                "label": "Energy",
                "value": 208,
                "unit": "kcal",
                "raw_row_text": shared_row,
            },
            nutrients=[
                {
                    "label": "Protein",
                    "value": 12,
                    "unit": "g",
                    "raw_row_text": shared_row,
                },
                {
                    "label": "Fat",
                    "value": 8,
                    "unit": "g",
                    "raw_row_text": shared_row,
                },
                {
                    "label": "Carbohydrates",
                    "value": 22,
                    "unit": "g",
                    "raw_row_text": shared_row,
                },
            ],
        )
        payload["raw_label_text"] = "\n".join(
            [
                "Synthetic duplicate label",
                "Net weight: 250 g",
                source_row,
                source_row,
            ]
        )

        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.normalize_label_extraction(payload)

        exact_duplicate = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        exact_duplicate["raw_label_text"] = "\n".join(
            [
                "Synthetic duplicate label",
                "Net weight: 250 g",
                source_row,
                shared_row,
            ]
        )
        exact_duplicate["energy"]["raw_row_text"] = source_row
        for nutrient in exact_duplicate["nutrients"]:
            nutrient["raw_row_text"] = source_row
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.normalize_label_extraction(
                exact_duplicate
            )

        non_whitespace_change = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        non_whitespace_change["raw_label_text"] = "\n".join(
            [
                "Synthetic duplicate label",
                "Net weight: 250 g",
                source_row,
            ]
        )
        non_whitespace_change["energy"]["raw_row_text"] = (
            shared_row.replace("kcal,", "kcal/")
        )
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.normalize_label_extraction(
                non_whitespace_change
            )

    def test_shared_macro_row_still_rejects_swapped_values(self):
        macro_row = "Ս՝ 10 գ Ճ՝ 5 գ Ածխ՝ 20 գ"
        payload = _armenian_label(
            nutrition_basis="unknown",
            basis_text="",
            energy=_energy(165),
            nutrients=[
                {
                    "label": "Ս",
                    "value": 5,
                    "unit": "գ",
                    "raw_row_text": macro_row,
                },
                {
                    "label": "Ճ",
                    "value": 10,
                    "unit": "գ",
                    "raw_row_text": macro_row,
                },
                {
                    "label": "Ածխ",
                    "value": 20,
                    "unit": "գ",
                    "raw_row_text": macro_row,
                },
            ],
        )
        payload["raw_label_text"] = "\n".join(
            [
                payload["product_name_original"],
                payload["package_raw_row_text"],
                payload["energy"]["raw_row_text"],
                macro_row,
            ]
        )

        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as raised:
            nutrition_label.normalize_label_extraction(payload)
        self.assertEqual(
            raised.exception.code,
            "nutrient_span_ambiguous",
        )

        wrong_unit = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        wrong_unit["nutrients"][0]["value"] = 10
        wrong_unit["nutrients"][1]["value"] = 5
        wrong_unit["nutrients"][0]["unit"] = "մլ"
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.normalize_label_extraction(wrong_unit)

        out_of_order = json.loads(
            json.dumps(payload, ensure_ascii=False)
        )
        out_of_order["nutrients"][0]["value"] = 10
        out_of_order["nutrients"][1]["value"] = 5
        out_of_order["nutrients"] = [
            out_of_order["nutrients"][1],
            out_of_order["nutrients"][0],
            out_of_order["nutrients"][2],
        ]
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as order_error:
            nutrition_label.normalize_label_extraction(out_of_order)
        self.assertEqual(
            order_error.exception.code,
            "nutrient_spans_out_of_order",
        )

    def test_shared_macro_row_requires_exact_label_and_unit_boundaries(self):
        suffix_label_row = "ԱՍ՝ 10 գՃ՝ 5 գԱծխ՝ 20 գ"
        suffix_label = _armenian_label(
            nutrition_basis="unknown",
            basis_text="",
            energy=_energy(165),
            nutrients=[
                {
                    "label": "Ս",
                    "value": 10,
                    "unit": "գ",
                    "raw_row_text": suffix_label_row,
                },
                {
                    "label": "Ճ",
                    "value": 5,
                    "unit": "գ",
                    "raw_row_text": suffix_label_row,
                },
                {
                    "label": "Ածխ",
                    "value": 20,
                    "unit": "գ",
                    "raw_row_text": suffix_label_row,
                },
            ],
        )
        suffix_label["raw_label_text"] = "\n".join(
            [
                suffix_label["product_name_original"],
                suffix_label["package_raw_row_text"],
                suffix_label["energy"]["raw_row_text"],
                suffix_label_row,
            ]
        )

        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as label_error:
            nutrition_label.normalize_label_extraction(suffix_label)
        self.assertEqual(
            label_error.exception.code,
            "nutrient_shared_row_boundary",
        )

        prefixed_unit_row = "Ս՝ 10 գրամ Ճ՝ 5 գ Ածխ՝ 20 գ"
        prefixed_unit = json.loads(
            json.dumps(suffix_label, ensure_ascii=False)
        )
        prefixed_unit["nutrients"][0]["raw_row_text"] = prefixed_unit_row
        prefixed_unit["nutrients"][1]["raw_row_text"] = prefixed_unit_row
        prefixed_unit["nutrients"][2]["raw_row_text"] = prefixed_unit_row
        prefixed_unit["raw_label_text"] = prefixed_unit[
            "raw_label_text"
        ].replace(suffix_label_row, prefixed_unit_row)

        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as unit_error:
            nutrition_label.normalize_label_extraction(prefixed_unit)
        self.assertEqual(
            unit_error.exception.code,
            "nutrient_unit_boundary",
        )

    def test_full_container_is_not_multiplied_by_package_weight(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label())
        estimate = nutrition_label.estimate_from_consumption_text(label, "Съел всё целиком")

        self.assertEqual(estimate["kcal"], 305.4)
        self.assertEqual(estimate["protein_g"], 17.3)
        self.assertEqual(estimate["fat_g"], 15.8)
        self.assertEqual(estimate["carb_g"], 23.5)
        self.assertEqual(estimate["scale_factor"], 1.0)

    def test_unknown_rows_are_preserved_but_not_misclassified(self):
        payload = _armenian_label()
        payload["nutrients"].append(_nutrient("Աղ", 1.2))
        _sync_raw_label(payload)
        label = nutrition_label.normalize_label_extraction(payload)

        self.assertIsNone(label["raw_nutrients"][-1]["canonical"])
        self.assertEqual(label["raw_nutrients"][-1]["label"], "Աղ")

    def test_macro_energy_mismatch_is_blocked(self):
        payload = _armenian_label()
        payload["nutrients"] = [
            _nutrient("Սպ.", 12.8),
            _nutrient("Ճ.", 20.9),
            _nutrient("Ածխ.", 19.7),
        ]
        payload["energy"]["value"] = 277.6
        payload["energy"] = _energy(277.6)
        _sync_raw_label(payload)

        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.normalize_label_extraction(payload)

    def test_macro_mismatch_precedes_incomplete_provenance(self):
        payload = _armenian_label(
            provenance={"provider": "synthetic-test"},
        )
        payload["nutrients"] = [
            _nutrient("Սպ.", 12.8),
            _nutrient("Ճ.", 20.9),
            _nutrient("Ածխ.", 19.7),
        ]
        payload["energy"] = _energy(277.6)
        _sync_raw_label(payload)

        with self.assertRaises(
            nutrition_label.NutritionLabelValidationError
        ):
            nutrition_label.normalize_label_extraction(payload)

    def test_unknown_basis_is_preserved_but_cannot_be_scaled(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label(nutrition_basis="unknown", basis_text=""))

        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.estimate_from_consumption_text(label, "всю упаковку")

    def test_model_basis_must_match_a_raw_visible_fragment(self):
        payload = _armenian_label(
            nutrition_basis="per_100g",
            basis_text="ամբողջ փաթեթի համար",
        )
        label = nutrition_label.normalize_label_extraction(payload)
        self.assertEqual(label["basis"], nutrition_label.BASIS_UNKNOWN)
        self.assertEqual(label["model_basis"], nutrition_label.BASIS_PER_100G)
        self.assertEqual(label["basis_verification"]["visible_basis"], "per_container")

        payload = _armenian_label()
        payload["raw_label_text"] = payload["raw_label_text"].replace(
            payload["basis_text"] + "\n",
            "",
        )
        label = nutrition_label.normalize_label_extraction(payload)
        self.assertEqual(label["basis"], nutrition_label.BASIS_UNKNOWN)

    def test_bare_package_weight_does_not_verify_per_100_basis(self):
        payload = _armenian_label(
            nutrition_basis="per_100g",
            basis_text="100 г",
            package_amount=100,
            package_unit="г",
        )
        label = nutrition_label.normalize_label_extraction(payload)

        self.assertEqual(label["basis"], nutrition_label.BASIS_UNKNOWN)
        self.assertEqual(
            label["basis_verification"]["visible_basis"],
            nutrition_label.BASIS_UNKNOWN,
        )

    def test_every_value_is_bound_to_an_exact_raw_row(self):
        payload = _armenian_label()
        payload["nutrients"][0]["value"] = 23.5
        payload["nutrients"][2]["value"] = 17.3
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(payload)

        payload = _armenian_label()
        payload["nutrients"][0]["label"] = "protein"
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(payload)

        payload = _armenian_label()
        payload["energy"].pop("raw_row_text")
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(payload)

    def test_product_and_measures_are_bound_to_exact_raw_fragments(self):
        payload = _armenian_label()
        payload["product_name_original"] = "Hallucinated product"
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(payload)

        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(_armenian_label(package_raw_row_text=None))

        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(_armenian_label(package_raw_row_text="Զտաքաշ՝ 999 գ"))

        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(
                _armenian_label(
                    nutrition_basis="per_serving",
                    basis_text="մեկ բաժնի համար",
                    serving_amount=180,
                    serving_unit="գ",
                    serving_raw_row_text=None,
                )
            )

    def test_provenance_envelope_detects_source_metadata_tampering(self):
        label = nutrition_label.normalize_label_extraction(
            _armenian_label(
                provenance={
                    "provider": "synthetic-test",
                    "model": "none",
                    "response_id": "response-1",
                }
            )
        )
        label["provenance"]["model"] = "different-model"

        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.validate_normalized_label(json.loads(json.dumps(label, ensure_ascii=False)))

    def test_missing_product_or_macro_requires_clarification(self):
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(_armenian_label(product_name_original=""))

        payload = _armenian_label()
        payload["nutrients"] = payload["nutrients"][:2]
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.normalize_label_extraction(payload)


class NutrientMappingConfirmationTests(unittest.TestCase):
    proposal = {
        "protein_g": 0,
        "fat_g": 1,
        "carb_g": 2,
    }

    def test_confirmed_unicode_mapping_preserves_raw_extraction(self):
        payload = _unknown_macro_label()
        original = json.loads(json.dumps(payload, ensure_ascii=False))

        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as unresolved:
            nutrition_label.normalize_label_extraction(payload)
        self.assertEqual(unresolved.exception.code, "missing_macro_mapping")

        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        self.assertNotIn("declared", challenge)
        self.assertEqual(
            [item["canonical"] for item in challenge["mapping"]],
            ["protein_g", "fat_g", "carb_g"],
        )
        self.assertEqual(len(challenge["challenge_sha256"]), 64)

        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )

        self.assertEqual(payload, original)
        self.assertEqual(
            label["declared"],
            {
                "kcal": 305.4,
                "protein_g": 17.3,
                "fat_g": 15.8,
                "carb_g": 23.5,
            },
        )
        for actual, source in zip(
            label["raw_nutrients"],
            payload["nutrients"],
        ):
            self.assertEqual(actual["label"], source["label"])
            self.assertEqual(actual["value"], float(source["value"]))
            self.assertEqual(actual["unit"], source["unit"])
            self.assertEqual(actual["raw_row_text"], source["raw_row_text"])
            self.assertIsNone(actual["canonical"])
        resolution = label["nutrient_mapping_resolution"]
        self.assertEqual(
            resolution["artifact_ids"],
            ["synthetic-reply-artifact-1"],
        )
        self.assertEqual(
            resolution["challenge_sha256"],
            challenge["challenge_sha256"],
        )

    def test_confirmation_replay_and_json_roundtrip_are_idempotent(self):
        payload = _unknown_macro_label()
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        confirmation = _mapping_confirmation(challenge)

        first = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            confirmation,
        )
        second = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            confirmation,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            nutrition_label.validate_normalized_label(
                json.loads(json.dumps(first, ensure_ascii=False))
            ),
            first,
        )

    def test_known_rows_cannot_be_overridden_and_full_mapping_is_required(self):
        payload = _armenian_label(
            nutrients=[
                _nutrient("Սպ.", 17.3),
                _nutrient("ცხ", 15.8),
                _nutrient("Ածխ.", 23.5),
            ],
        )
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as partial:
            nutrition_label.prepare_nutrient_mapping_challenge(
                payload,
                {"fat_g": 1},
            )
        self.assertEqual(
            partial.exception.code,
            "nutrient_mapping_proposal_invalid",
        )

        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )
        self.assertEqual(label["declared"]["fat_g"], 15.8)

        contradictory = {
            "protein_g": 1,
            "fat_g": 0,
            "carb_g": 2,
        }
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as conflict:
            nutrition_label.prepare_nutrient_mapping_challenge(
                payload,
                contradictory,
            )
        self.assertEqual(
            conflict.exception.code,
            "nutrient_mapping_contradicts_known_label",
        )

    def test_mapping_requires_distinct_exact_rows(self):
        payload = _unknown_macro_label()
        for proposal in (
            {"protein_g": 0, "fat_g": 0, "carb_g": 2},
            {"protein_g": 0, "fat_g": 1, "carb_g": 99},
        ):
            with self.subTest(proposal=proposal):
                with self.assertRaises(
                    nutrition_label.NutritionLabelNeedsClarification
                ) as raised:
                    nutrition_label.prepare_nutrient_mapping_challenge(
                        payload,
                        proposal,
                    )
                self.assertEqual(
                    raised.exception.code,
                    "nutrient_mapping_proposal_invalid",
                )

        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        tampered = json.loads(
            json.dumps(challenge, ensure_ascii=False)
        )
        tampered["mapping"][0]["value"] = 23.5
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as changed:
            nutrition_label.apply_confirmed_nutrient_mapping(
                payload,
                tampered,
                _mapping_confirmation(tampered),
            )
        self.assertEqual(
            changed.exception.code,
            "nutrient_mapping_challenge_invalid",
        )

    def test_negative_ambiguous_unrelated_and_stale_replies_fail_closed(self):
        payload = _unknown_macro_label()
        original = json.loads(json.dumps(payload, ensure_ascii=False))
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        for text in (
            "нет",
            "может быть",
            "съел 100 г",
            "цифры за 100 г",
            "да, съел 100 г",
        ):
            with self.subTest(text=text):
                with self.assertRaises(
                    nutrition_label.NutritionLabelNeedsClarification
                ) as raised:
                    nutrition_label.apply_confirmed_nutrient_mapping(
                        payload,
                        challenge,
                        _mapping_confirmation(challenge, text=text),
                    )
                self.assertEqual(
                    raised.exception.code,
                    "nutrient_mapping_confirmation_not_explicit",
                )
                self.assertEqual(payload, original)

        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ) as stale:
            nutrition_label.apply_confirmed_nutrient_mapping(
                payload,
                challenge,
                _mapping_confirmation(
                    challenge,
                    challenge_sha256="0" * 64,
                ),
            )
        self.assertEqual(
            stale.exception.code,
            "nutrient_mapping_confirmation_stale",
        )

    def test_challenge_is_bound_to_source_provenance(self):
        first = _unknown_macro_label(
            provenance={
                "provider": "synthetic-test",
                "model": "none",
                "response_id": "synthetic-response-a",
            }
        )
        second = json.loads(json.dumps(first, ensure_ascii=False))
        second["provenance"]["response_id"] = "synthetic-response-b"

        first_challenge = (
            nutrition_label.prepare_nutrient_mapping_challenge(
                first,
                self.proposal,
            )
        )
        second_challenge = (
            nutrition_label.prepare_nutrient_mapping_challenge(
                second,
                self.proposal,
            )
        )
        self.assertEqual(
            first_challenge["extraction_sha256"],
            second_challenge["extraction_sha256"],
        )
        self.assertNotEqual(
            first_challenge["provenance_sha256"],
            second_challenge["provenance_sha256"],
        )
        self.assertNotEqual(
            first_challenge["challenge_sha256"],
            second_challenge["challenge_sha256"],
        )

    def test_mapping_resolution_tampering_is_rejected(self):
        payload = _unknown_macro_label()
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )

        for mutate in ("mapping", "artifact", "text", "fingerprint"):
            tampered = json.loads(json.dumps(label, ensure_ascii=False))
            resolution = tampered["nutrient_mapping_resolution"]
            if mutate == "mapping":
                resolution["mapping"][0]["label"] = "changed"
            elif mutate == "artifact":
                resolution["artifact_ids"] = ["another-artifact"]
            elif mutate == "text":
                resolution["text"] = "Верно"
            else:
                resolution["resolution_sha256"] = "0" * 64
            with self.subTest(mutate=mutate):
                with self.assertRaises(
                    nutrition_label.NutritionLabelValidationError
                ):
                    nutrition_label.validate_normalized_label(tampered)

    def test_mapping_resolution_is_bound_to_consumed_estimate(self):
        payload = _unknown_macro_label()
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )
        estimate = nutrition_label.estimate_from_consumption_text(
            label,
            "всё",
        )
        self.assertEqual(
            estimate["nutrient_mapping_resolution"]["resolution_sha256"],
            label["nutrient_mapping_resolution"]["resolution_sha256"],
        )
        record = nutrition_label.build_label_meal_record(
            {
                "id": "synthetic-meal",
                "tags": ["meal"],
                "metadata": {},
            },
            estimate,
        )
        self.assertEqual(
            record["metadata"]["nutrient_mapping_resolution"],
            label["nutrient_mapping_resolution"],
        )

        tampered = json.loads(json.dumps(estimate, ensure_ascii=False))
        tampered["nutrient_mapping_resolution"]["artifact_ids"] = [
            "different-artifact"
        ]
        with self.assertRaises(
            nutrition_label.NutritionLabelValidationError
        ):
            nutrition_label.build_label_meal_record(
                {
                    "id": "synthetic-meal",
                    "tags": ["meal"],
                    "metadata": {},
                },
                tampered,
            )

    def test_red_flag_confirmation_short_circuits_semantic_parsing(self):
        payload = _unknown_macro_label()
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        challenge["challenge_sha256"] = "0" * 64
        with self.assertRaises(nutrition_label.LabelCorrectionRedFlag):
            nutrition_label.apply_confirmed_nutrient_mapping(
                payload,
                challenge,
                _mapping_confirmation(
                    challenge,
                    schema_version=999,
                    text="да, у меня боль в груди",
                ),
            )

    def test_normalized_label_red_flag_precedes_malformed_envelope(self):
        payload = _unknown_macro_label()
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )
        label["schema_version"] = 999
        label["raw_label_text"] = ""
        label["nutrient_mapping_resolution"]["schema_version"] = 999
        label["nutrient_mapping_resolution"]["text"] = (
            "да, у меня боль в груди"
        )

        with self.assertRaises(nutrition_label.LabelCorrectionRedFlag):
            nutrition_label.validate_normalized_label(label)

    def test_estimate_red_flag_precedes_malformed_amount_envelope(self):
        payload = _unknown_macro_label()
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )
        estimate = nutrition_label.estimate_from_consumption_text(
            label,
            "всё",
        )
        estimate["consumed"] = None
        estimate["scale_factor"] = "not-a-number"
        estimate["nutrient_mapping_resolution"]["schema_version"] = 999
        estimate["nutrient_mapping_resolution"]["text"] = (
            "да, у меня боль в груди"
        )

        with self.assertRaises(nutrition_label.LabelCorrectionRedFlag):
            nutrition_label._validated_label_estimate(estimate)

    def test_exact_shared_row_spans_can_be_confirmed(self):
        shared = "ც 17.3 գ ცხ 15.8 գ ნახ 23.5 գ"
        payload = _armenian_label(
            nutrients=[
                {
                    "label": "ც",
                    "value": 17.3,
                    "unit": "գ",
                    "raw_row_text": shared,
                },
                {
                    "label": "ცხ",
                    "value": 15.8,
                    "unit": "գ",
                    "raw_row_text": shared,
                },
                {
                    "label": "ნახ",
                    "value": 23.5,
                    "unit": "գ",
                    "raw_row_text": shared,
                },
            ],
        )
        payload["raw_label_text"] = "\n".join(
            [
                payload["product_name_original"],
                payload["basis_text"],
                payload["package_raw_row_text"],
                payload["energy"]["raw_row_text"],
                shared,
            ]
        )
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        spans = [entry["span"] for entry in challenge["mapping"]]
        self.assertEqual(spans, sorted(spans))

        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(
                challenge,
                source="voice_transcript",
            ),
        )
        self.assertEqual(label["declared"]["protein_g"], 17.3)

    def test_confirmed_mapping_does_not_resolve_basis_or_amount(self):
        payload = _unknown_macro_label(
            nutrition_basis="unknown",
            basis_text="",
        )
        challenge = nutrition_label.prepare_nutrient_mapping_challenge(
            payload,
            self.proposal,
        )
        label = nutrition_label.apply_confirmed_nutrient_mapping(
            payload,
            challenge,
            _mapping_confirmation(challenge),
        )
        self.assertEqual(
            label["basis"],
            nutrition_label.BASIS_UNKNOWN,
        )
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.estimate_from_consumption_text(
                label,
                "всё",
            )


class ConsumptionTests(unittest.TestCase):
    def test_per_100g_scales_only_from_explicit_grams(self):
        label = nutrition_label.normalize_label_extraction(
            _armenian_label(
                nutrition_basis="per_100g",
                basis_text="100 գ-ում",
                package_amount=220,
                energy=_energy(200),
                nutrients=[
                    _nutrient("Սպիտակուցներ", 10),
                    _nutrient("Ճարպեր", 8),
                    _nutrient("Ածխաջրեր", 22),
                ],
            )
        )
        estimate = nutrition_label.estimate_from_consumption_text(label, "150 г")

        self.assertEqual(estimate["kcal"], 300.0)
        self.assertEqual(estimate["protein_g"], 15.0)
        self.assertEqual(estimate["fat_g"], 12.0)
        self.assertEqual(estimate["carb_g"], 33.0)
        self.assertEqual(estimate["scale_factor"], 1.5)

    def test_per_100ml_full_package_uses_package_volume(self):
        label = nutrition_label.normalize_label_extraction(
            _armenian_label(
                product_name_original="Սպիտակուցային ըմպելիք",
                product_name_ru="Протеиновый напиток",
                nutrition_basis="per_100ml",
                basis_text="100 մլ-ում",
                package_amount=300,
                package_unit="մլ",
                energy=_energy(102.8),
                nutrients=[
                    _nutrient("Սպ.", 10.27),
                    _nutrient("Ճ.", 2.13),
                    _nutrient("Ածխ.", 10.63),
                ],
            )
        )
        estimate = nutrition_label.estimate_from_consumption_text(label, "выпил всё")

        self.assertEqual(estimate["kcal"], 308.4)
        self.assertEqual(estimate["protein_g"], 30.81)
        self.assertEqual(estimate["fat_g"], 6.39)
        self.assertEqual(estimate["carb_g"], 31.89)

    def test_per_serving_accepts_servings_and_requires_serving_size(self):
        label = nutrition_label.normalize_label_extraction(
            _armenian_label(
                nutrition_basis="per_serving",
                basis_text="մեկ բաժնի համար",
                package_amount=360,
                serving_amount=180,
                serving_unit="գ",
                energy=_energy(277.6),
                nutrients=[
                    _nutrient("Սպ.", 19.7),
                    _nutrient("Ճ.", 12.8),
                    _nutrient("Ածխ.", 20.9),
                ],
            )
        )
        estimate = nutrition_label.estimate_from_consumption_text(label, "2 порции")

        self.assertEqual(estimate["kcal"], 555.2)
        self.assertEqual(estimate["scale_factor"], 2.0)

    def test_short_amount_parser_is_deliberately_narrow(self):
        self.assertEqual(
            nutrition_label.parse_consumed_amount("половину")["fraction"],
            0.5,
        )
        self.assertEqual(
            nutrition_label.parse_consumed_amount("примерно 50% упаковки")["fraction"],
            0.5,
        )
        self.assertEqual(
            nutrition_label.parse_consumed_amount("1/4")["fraction"],
            0.25,
        )
        self.assertEqual(
            nutrition_label.parse_consumed_amount("300 мл")["unit"],
            "ml",
        )
        self.assertEqual(
            nutrition_label.parse_consumed_amount("цифры за 100 грамм, съел 150 грамм")["amount"],
            150.0,
        )
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.parse_consumed_amount("примерно 150")
        for ambiguous in (
            "да",
            "ага, примерно 150",
            "не всё",
            "не всю упаковку",
            "почти всё",
            "да, но немного оставил",
            "упаковка 300 мл, выпил 200 мл",
            "150-200 г",
            "150–200 г",
        ):
            with self.subTest(ambiguous=ambiguous):
                with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
                    nutrition_label.parse_consumed_amount(ambiguous)

    def test_consumption_cannot_silently_exceed_one_known_package(self):
        per_container = nutrition_label.normalize_label_extraction(_armenian_label())
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.estimate_from_consumption_text(
                per_container,
                "250 г",
            )

        per_100g = nutrition_label.normalize_label_extraction(
            _armenian_label(
                nutrition_basis="per_100g",
                basis_text="100 գ-ում",
                energy=_energy(200),
                nutrients=[
                    _nutrient("Սպիտակուցներ", 10),
                    _nutrient("Ճարպեր", 8),
                    _nutrient("Ածխաջրեր", 22),
                ],
            )
        )
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.estimate_from_consumption_text(per_100g, "250 г")

        per_serving = nutrition_label.normalize_label_extraction(
            _armenian_label(
                nutrition_basis="per_serving",
                basis_text="մեկ բաժնի համար",
                package_amount=360,
                serving_amount=180,
                serving_unit="գ",
                energy=_energy(277.6),
                nutrients=[
                    _nutrient("Սպ.", 19.7),
                    _nutrient("Ճ.", 12.8),
                    _nutrient("Ածխ.", 20.9),
                ],
            )
        )
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.estimate_from_consumption_text(
                per_serving,
                "3 порции",
            )

    def test_unit_mismatch_is_blocked(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label(nutrition_basis="per_100g"))
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.estimate_from_consumption_text(label, "150 мл")

    def test_direct_fraction_above_one_is_blocked(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label())
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.calculate_consumed_estimate(
                label,
                {"kind": "package_fraction", "fraction": 1.5},
            )

    def test_tampered_normalized_label_is_revalidated(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label())
        label["declared"]["kcal"] = 999
        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.calculate_consumed_estimate(
                label,
                {"kind": "package_fraction", "fraction": 1},
            )

    def test_basis_parser_requires_one_explicit_basis(self):
        self.assertEqual(
            nutrition_label.parse_label_basis_text("значения за 100 г"),
            nutrition_label.BASIS_PER_100G,
        )
        self.assertEqual(
            nutrition_label.parse_label_basis_text("за всю упаковку"),
            nutrition_label.BASIS_PER_CONTAINER,
        )
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.parse_label_basis_text("не знаю")
        self.assertEqual(
            nutrition_label.detect_explicit_basis_correction("нет, цифры за 100 г, съел 150 г"),
            nutrition_label.BASIS_PER_100G,
        )
        self.assertEqual(
            nutrition_label.detect_explicit_basis_correction("цифры за 100 грамм, съел 150 грамм"),
            nutrition_label.BASIS_PER_100G,
        )
        self.assertIsNone(nutrition_label.detect_explicit_basis_correction("съел 100 г"))
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.parse_label_basis_text("100 г")

    def test_combined_basis_reply_requires_explicit_consumption(self):
        basis_only = nutrition_label.parse_basis_and_consumption_text(
            "Да всю упаковку"
        )
        self.assertEqual(
            basis_only["basis"],
            nutrition_label.BASIS_PER_CONTAINER,
        )
        self.assertIsNone(basis_only["consumed"])

        combined = nutrition_label.parse_basis_and_consumption_text(
            "цифры за всю упаковку, съел всё"
        )
        self.assertEqual(
            combined["basis"],
            nutrition_label.BASIS_PER_CONTAINER,
        )
        self.assertEqual(combined["consumed"]["fraction"], 1.0)

        measured = nutrition_label.parse_basis_and_consumption_text(
            "значения за 100 г, съел 150 г"
        )
        self.assertEqual(
            measured["basis"],
            nutrition_label.BASIS_PER_100G,
        )
        self.assertEqual(measured["consumed"]["amount"], 150.0)

        serving = nutrition_label.parse_basis_and_consumption_text(
            "за порцию, съел 2 порции"
        )
        self.assertEqual(
            serving["basis"],
            nutrition_label.BASIS_PER_SERVING,
        )
        self.assertEqual(serving["consumed"]["count"], 2.0)

        for text, expected_count in (
            ("за порцию, съел 1/2 порции", 0.5),
            ("за порцию, съел половину порции", 0.5),
            ("за порцию, съел четверть порции", 0.25),
            ("за порцию, съел две порции", 2.0),
            ("за порцию, съел полторы порции", 1.5),
            ("за порцию, съел 1 1/2 порции", 1.5),
            ("за порцию, съел две с половиной порции", 2.5),
            ("за порцию, съел одну с половиной порцию", 1.5),
        ):
            with self.subTest(text=text):
                fractional_serving = (
                    nutrition_label.parse_basis_and_consumption_text(
                        text
                    )
                )
                self.assertEqual(
                    fractional_serving["basis"],
                    nutrition_label.BASIS_PER_SERVING,
                )
                self.assertEqual(
                    fractional_serving["consumed"]["kind"],
                    "servings",
                )
                self.assertEqual(
                    fractional_serving["consumed"]["count"],
                    expected_count,
                )

        consumption_only = (
            nutrition_label.parse_basis_and_consumption_text(
                "всю упаковку съел"
            )
        )
        self.assertIsNone(consumption_only["basis"])
        self.assertEqual(
            consumption_only["consumed"]["fraction"],
            1.0,
        )

        for text in (
            "съел за раз всю упаковку",
            "выпил за день всю бутылку",
            "съел за обедом всю упаковку",
        ):
            with self.subTest(text=text):
                temporal = (
                    nutrition_label.parse_basis_and_consumption_text(
                        text
                    )
                )
                self.assertIsNone(temporal["basis"])
                self.assertEqual(
                    temporal["consumed"]["fraction"],
                    1.0,
                )

        for text in (
            "съел за одну порцию 150 г",
            "выпил за одну порцию 200 мл",
            "съел за всю упаковку 150 г",
        ):
            with self.subTest(text=text):
                action_order = (
                    nutrition_label.parse_basis_and_consumption_text(
                        text
                    )
                )
                self.assertIsNone(action_order["basis"])
                self.assertIsNone(action_order["consumed"])

        negated = nutrition_label.parse_basis_and_consumption_text(
            "не съел всю упаковку"
        )
        self.assertIsNone(negated["basis"])
        self.assertIsNone(negated["consumed"])

        for text in (
            "цифры за всю упаковку, не съедено всё",
            "цифры за всю упаковку, не выпито всё",
        ):
            with self.subTest(text=text):
                negated_combined = (
                    nutrition_label.parse_basis_and_consumption_text(
                        text
                    )
                )
                self.assertEqual(
                    negated_combined["basis"],
                    nutrition_label.BASIS_PER_CONTAINER,
                )
                self.assertIsNone(
                    negated_combined["consumed"]
                )

        for text in (
            "цифры за всю упаковку, съел всё кроме 50 г",
            "цифры за всю упаковку, съел всю упаковку без 50 г",
            "цифры за всю упаковку, съел всё за исключением 50 г",
            "цифры за всю упаковку, выпил всё кроме пары глотков",
        ):
            with self.subTest(text=text):
                subtractive = (
                    nutrition_label.parse_basis_and_consumption_text(
                        text
                    )
                )
                self.assertEqual(
                    subtractive["basis"],
                    nutrition_label.BASIS_PER_CONTAINER,
                )
                self.assertIsNone(subtractive["consumed"])

        for text in (
            "съел половину упаковки 200 г",
            "съел 1/2 от 200 г",
            "съел 50% от 200 г",
            "съел всё 150 г",
            "всё нормально",
            "всё правильно",
            "всё ок",
            "да, всё нормально",
            "да — всё нормально",
            "ага, всё нормально",
            "ну всё нормально",
            "всё супер",
            "всё отлично",
            "всё так",
            "всё сходится",
            "да, всё супер",
            "полностью согласен",
            "полностью верно",
            "съел одиннадцать порций",
            "съел несколько порций",
        ):
            with self.subTest(text=text):
                with self.assertRaises(
                    nutrition_label.NutritionLabelNeedsClarification
                ):
                    nutrition_label.parse_consumed_amount(text)

        ambiguous = nutrition_label.parse_basis_and_consumption_text(
            "за 100 г, 150"
        )
        self.assertEqual(
            ambiguous["basis"],
            nutrition_label.BASIS_PER_100G,
        )
        self.assertIsNone(ambiguous["consumed"])

    def test_user_basis_resolution_is_strict_after_json_roundtrip(self):
        label = nutrition_label.normalize_label_extraction(_armenian_label(nutrition_basis="unknown", basis_text=""))
        label["basis"] = nutrition_label.BASIS_PER_CONTAINER
        label["basis_resolution"] = {
            "source": "user_reply",
            "text": "цифры на всю упаковку, съел 100 г",
            "artifact_ids": ["reply-1"],
        }
        validated = nutrition_label.validate_normalized_label(json.loads(json.dumps(label, ensure_ascii=False)))
        self.assertEqual(
            validated["basis"],
            nutrition_label.BASIS_PER_CONTAINER,
        )

        label = nutrition_label.normalize_label_extraction(_armenian_label(nutrition_basis="unknown", basis_text=""))
        label["basis"] = nutrition_label.BASIS_PER_100G
        label["basis_resolution"] = {
            "source": "user_reply",
            "text": "съел 100 г",
            "artifact_ids": ["reply-2"],
        }
        with self.assertRaises(nutrition_label.NutritionLabelNeedsClarification):
            nutrition_label.validate_normalized_label(json.loads(json.dumps(label, ensure_ascii=False)))

    def test_contextual_basis_resolution_is_source_bound(self):
        label = nutrition_label.normalize_label_extraction(
            _armenian_label(
                nutrition_basis="unknown",
                basis_text="",
            )
        )
        challenge = nutrition_label.prepare_label_basis_challenge(
            label
        )
        resolved = nutrition_label.apply_confirmed_label_basis(
            label,
            challenge,
            {
                "schema_version": (
                    nutrition_label.BASIS_RESOLUTION_SCHEMA_VERSION
                ),
                "source": "user_reply",
                "confirmation_id": "telegram-42-11",
                "text": "Да всю упаковку",
                "artifact_ids": ["reply-contextual-1"],
                "challenge_sha256": challenge["challenge_sha256"],
            },
        )
        validated = nutrition_label.validate_normalized_label(
            json.loads(json.dumps(resolved, ensure_ascii=False))
        )
        self.assertEqual(
            validated["basis"],
            nutrition_label.BASIS_PER_CONTAINER,
        )
        self.assertEqual(
            validated["basis_resolution"]["confirmation_id"],
            "telegram-42-11",
        )
        estimate = nutrition_label.estimate_from_consumption_text(
            validated,
            "всё",
        )
        self.assertEqual(estimate["scale_factor"], 1.0)
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.apply_confirmed_label_basis(
                validated,
                challenge,
                {
                    "schema_version": (
                        nutrition_label.BASIS_RESOLUTION_SCHEMA_VERSION
                    ),
                    "source": "user_reply",
                    "confirmation_id": "telegram-42-12",
                    "text": "значения за 100 г",
                    "artifact_ids": ["reply-contextual-2"],
                    "challenge_sha256": (
                        challenge["challenge_sha256"]
                    ),
                },
            )

        tampered = json.loads(
            json.dumps(resolved, ensure_ascii=False)
        )
        mutations = {
            "confirmation_id": "telegram-42-12",
            "text": "значения за 100 г",
            "source": "unknown",
            "artifact_ids": ["different-artifact"],
            "basis": nutrition_label.BASIS_PER_100G,
            "challenge_sha256": "1" * 64,
            "resolution_sha256": "2" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                tampered = json.loads(
                    json.dumps(resolved, ensure_ascii=False)
                )
                tampered["basis_resolution"][field] = value
                with self.assertRaises(
                    nutrition_label.NutritionLabelValidationError
                ):
                    nutrition_label.validate_normalized_label(
                        tampered
                    )

        stale = dict(challenge)
        stale["challenge_sha256"] = "0" * 64
        with self.assertRaises(
            nutrition_label.NutritionLabelNeedsClarification
        ):
            nutrition_label.apply_confirmed_label_basis(
                label,
                stale,
                {
                    "schema_version": (
                        nutrition_label.BASIS_RESOLUTION_SCHEMA_VERSION
                    ),
                    "source": "user_reply",
                    "confirmation_id": "telegram-42-11",
                    "text": "Да всю упаковку",
                    "artifact_ids": ["reply-contextual-1"],
                    "challenge_sha256": "0" * 64,
                },
            )

        other_label = nutrition_label.normalize_label_extraction(
            _armenian_label(
                nutrition_basis="unknown",
                basis_text="",
                product_name_original="Այլ ապուր",
            )
        )
        other_challenge = (
            nutrition_label.prepare_label_basis_challenge(
                other_label
            )
        )
        self.assertNotEqual(
            challenge["challenge_sha256"],
            other_challenge["challenge_sha256"],
        )


class LabelCorrectionLedgerTests(unittest.TestCase):
    def test_amount_correction_preserves_decimals_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.sqlite3"
            index.init_db(db)
            index.upsert_record(
                db,
                {
                    "id": "obs-meal-synthetic",
                    "record_type": "Observation",
                    "source_id": "synthetic",
                    "title": "[C2 Weak signal] Could this be soup?",
                    "summary": "[C2 Weak signal] Could this be soup?",
                    "artifact_ids": ["photo-1"],
                    "evidence_class": "personal",
                    "confidence": nutrition_label.LABEL_CONFIDENCE,
                    "date": "2026-07-27",
                    "tags": ["nutrition", "meal", "photo"],
                    "metadata": {"revision": 0},
                    "observation_kind": "nutrition_meal",
                    "metric_name": "meal_kcal",
                    "value": 305.4,
                    "unit": "kcal",
                },
            )
            label = nutrition_label.normalize_label_extraction(_armenian_label())

            result = nutrition_label.apply_label_consumption_correction(
                db,
                record_id="obs-meal-synthetic",
                label=label,
                correction_text="половину",
                revision_id="tg-synthetic-1",
                created_at="2026-07-27T10:00:00Z",
                source_type="text",
                evidence_artifact_ids=["reply-1"],
            )

            self.assertTrue(result["applied"])
            self.assertEqual(result["estimate"]["kcal"], 152.7)
            stored = index.get_record(db, "obs-meal-synthetic")
            self.assertEqual(stored["value"], 152.7)
            self.assertEqual(stored["metadata"]["protein_g"], 8.65)
            self.assertEqual(stored["metadata"]["revision"], 1)
            ledger = index.list_record_revisions(db, "obs-meal-synthetic")
            self.assertEqual(ledger[0]["reason"], "user_label_consumption_correction")
            self.assertEqual(ledger[0]["evidence_artifact_ids"], ["reply-1"])
            self.assertEqual(
                ledger[0]["patch"]["calculation"]["provenance"]["provider"],
                "synthetic-test",
            )

            redelivered = nutrition_label.apply_label_consumption_correction(
                db,
                record_id="obs-meal-synthetic",
                label=label,
                correction_text="всё",
                revision_id="tg-synthetic-1",
                created_at="2026-07-27T10:00:01Z",
                source_type="text",
                evidence_artifact_ids=["reply-2"],
            )
            self.assertFalse(redelivered["applied"])
            self.assertEqual(redelivered["estimate"]["kcal"], 152.7)

    def test_custom_display_is_still_a_c2_question(self):
        record = {
            "id": "meal",
            "tags": ["meal"],
            "metadata": {},
        }
        label = nutrition_label.normalize_label_extraction(_armenian_label())
        estimate = nutrition_label.estimate_from_consumption_text(label, "всё")
        updated = nutrition_label.build_label_meal_record(
            record,
            estimate,
            display_title="Definite title.",
            display_summary="Definitely 305 kcal.",
        )
        self.assertTrue(updated["title"].startswith("[C2"))
        self.assertTrue(updated["title"].endswith("?"))
        self.assertTrue(updated["summary"].startswith("[C2"))
        self.assertTrue(updated["summary"].endswith("?"))

    def test_record_builder_rejects_tampered_calculation_contract(self):
        record = {
            "id": "meal",
            "tags": ["meal"],
            "metadata": {},
        }
        label = nutrition_label.normalize_label_extraction(_armenian_label())
        estimate = nutrition_label.estimate_from_consumption_text(label, "всё")
        estimate["kcal"] = 999
        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.build_label_meal_record(record, estimate)

        estimate = nutrition_label.estimate_from_consumption_text(label, "всё")
        estimate["scale_factor"] = 0.5
        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.build_label_meal_record(record, estimate)

        estimate = nutrition_label.estimate_from_consumption_text(label, "всё")
        estimate["provenance"]["response_id"] = "changed"
        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.build_label_meal_record(record, estimate)

        estimate = nutrition_label.estimate_from_consumption_text(label, "всё")
        estimate["package"]["amount"] = 440
        estimate["package"]["raw_row_text"] = "Զտաքաշ՝ 440 գ"
        with self.assertRaises(nutrition_label.NutritionLabelValidationError):
            nutrition_label.build_label_meal_record(record, estimate)


if __name__ == "__main__":
    unittest.main()
