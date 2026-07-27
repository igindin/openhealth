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
