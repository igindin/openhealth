import pytest

from openhealth import index
from openhealth.modules import nutrition


def synthetic_meal():
    return {
        "id": "obs-meal-synthetic-1",
        "record_type": "Observation",
        "source_id": "nutrition",
        "title": "Meal estimate: synthetic bowl",
        "summary": "Synthetic bowl — ~400 kcal.",
        "artifact_ids": ["artifact-photo-synthetic"],
        "evidence_class": "personal",
        "confidence": 0.3,
        "date": "2026-01-01",
        "tags": ["nutrition", "meal", "photo"],
        "metadata": {
            "kcal": 400,
            "protein_g": 20,
            "carb_g": 40,
            "fat_g": 18,
            "title": "synthetic bowl",
            "method": "photo-estimate",
            "confidence_level": "C2",
        },
        "observation_kind": "nutrition_meal",
        "metric_name": "meal_kcal",
        "value": 400,
        "unit": "kcal",
    }


def corrected_estimate():
    return {
        "title": "synthetic bowl with corrected sauce",
        "kcal": 460,
        "protein_g": 21,
        "carb_g": 42,
        "fat_g": 24,
        "note": "Includes the user-reported sauce.",
        "ingredients": ["synthetic grain", "synthetic sauce"],
    }


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "health.sqlite3"
    index.init_db(path)
    index.upsert_record(path, synthetic_meal())
    return path


def test_telegram_links_resolve_exact_record_and_cannot_be_reassigned(db):
    index.link_telegram_message(db, 111, 10, "obs-meal-synthetic-1", "source_message")
    index.link_telegram_message(db, 111, 11, "obs-meal-synthetic-1", "bot_reply")
    index.link_telegram_message(
        db,
        111,
        12,
        "obs-meal-synthetic-1",
        "confirmation_reply",
    )
    assert index.resolve_telegram_reply(db, 111, 10) == "obs-meal-synthetic-1"
    assert index.resolve_telegram_reply(db, 111, 11) == "obs-meal-synthetic-1"
    assert index.resolve_telegram_reply(db, 111, 12) == "obs-meal-synthetic-1"
    assert index.resolve_telegram_reply(db, 222, 11) is None

    index.link_telegram_message(db, 111, 11, "obs-meal-synthetic-1", "bot_reply")
    index.link_telegram_message(
        db,
        111,
        12,
        "obs-meal-synthetic-1",
        "confirmation_reply",
    )
    with pytest.raises(ValueError, match="already linked"):
        index.link_telegram_message(db, 111, 11, "another-record", "bot_reply")
    with pytest.raises(ValueError, match="already linked"):
        index.link_telegram_message(
            db, 111, 11, "obs-meal-synthetic-1", "source_message"
        )


def test_meal_correction_updates_same_record_and_appends_before_after(db):
    result = nutrition.apply_meal_correction(
        db,
        record_id="obs-meal-synthetic-1",
        estimate=corrected_estimate(),
        correction_text="The sauce was omitted from the first estimate.",
        revision_id="tg-111-12",
        created_at="2026-01-01T12:00:00+00:00",
        source_type="text",
        evidence_artifact_ids=["artifact-correction-synthetic"],
        expected_revision=0,
    )
    assert result["applied"] is True
    assert result["revision"] == 1

    records = index.list_records_by_source(db, "nutrition")
    assert len(records) == 1
    current = records[0]
    assert current["id"] == "obs-meal-synthetic-1"
    assert current["value"] == 460
    assert current["confidence"] == 0.3
    assert current["metadata"]["confidence_level"] == "C2"
    assert current["metadata"]["revision"] == 1
    assert current["title"].startswith("[C2 Weak signal]")
    assert current["title"].endswith("?")
    assert current["summary"].startswith("[C2 Weak signal]")
    assert current["summary"].endswith("?")
    assert current["artifact_ids"] == [
        "artifact-photo-synthetic",
        "artifact-correction-synthetic",
    ]

    (revision,) = index.list_record_revisions(db, current["id"])
    assert revision["before"]["value"] == 400
    assert revision["after"]["value"] == 460
    assert revision["patch"]["source_type"] == "text"
    assert revision["evidence_artifact_ids"] == ["artifact-correction-synthetic"]


def test_revision_id_is_idempotent_and_stale_writer_conflicts(db):
    kwargs = {
        "db_path": db,
        "record_id": "obs-meal-synthetic-1",
        "estimate": corrected_estimate(),
        "correction_text": "The sauce was omitted.",
        "revision_id": "tg-111-12",
        "created_at": "2026-01-01T12:00:00+00:00",
        "source_type": "voice",
        "expected_revision": 0,
    }
    first = nutrition.apply_meal_correction(**kwargs)
    second = nutrition.apply_meal_correction(**kwargs)
    assert first["applied"] is True
    assert second["applied"] is False
    assert len(index.list_record_revisions(db, "obs-meal-synthetic-1")) == 1

    with pytest.raises(index.RecordRevisionConflict):
        nutrition.apply_meal_correction(
            db,
            record_id="obs-meal-synthetic-1",
            estimate={**corrected_estimate(), "kcal": 470},
            correction_text="A second correction from a stale view.",
            revision_id="tg-111-13",
            created_at="2026-01-01T12:01:00+00:00",
            source_type="text",
            expected_revision=0,
        )
    assert index.get_record(db, "obs-meal-synthetic-1")["value"] == 460


def test_reimport_does_not_erase_audited_correction(db):
    original = synthetic_meal()
    nutrition.apply_meal_correction(
        db,
        record_id=original["id"],
        estimate=corrected_estimate(),
        correction_text="The sauce was omitted.",
        revision_id="tg-111-12",
        created_at="2026-01-01T12:00:00+00:00",
        source_type="text",
    )

    index.upsert_record(db, original)

    current = index.get_record(db, original["id"])
    assert current["value"] == 460
    assert current["metadata"]["revision"] == 1
    assert len(index.list_record_revisions(db, original["id"])) == 1


def test_red_flag_short_circuits_meal_interpretation(db):
    with pytest.raises(nutrition.MealCorrectionRedFlag):
        nutrition.apply_meal_correction(
            db,
            record_id="obs-meal-synthetic-1",
            estimate=corrected_estimate(),
            correction_text="I also have chest pain.",
            revision_id="tg-111-14",
            created_at="2026-01-01T12:02:00+00:00",
            source_type="text",
        )
    assert index.list_record_revisions(db, "obs-meal-synthetic-1") == []


def test_russian_red_flag_short_circuits_meal_interpretation(db):
    with pytest.raises(nutrition.MealCorrectionRedFlag) as exc_info:
        nutrition.apply_meal_correction(
            db,
            record_id="obs-meal-synthetic-1",
            estimate=corrected_estimate(),
            correction_text="И ещё у меня боль в груди.",
            revision_id="tg-111-15",
            created_at="2026-01-01T12:03:00+00:00",
            source_type="voice",
        )
    assert exc_info.value.flags[0].code == "chest_pain"
    assert index.list_record_revisions(db, "obs-meal-synthetic-1") == []


@pytest.mark.parametrize(
    ("source_type", "prior_source_types", "required"),
    [
        ("text", [], False),
        ("text", ["text"], False),
        ("voice", [], True),
        ("text", ["voice"], True),
        ("text", [""], True),
        ("", [], True),
    ],
)
def test_meal_correction_confirmation_policy_fails_closed(
    source_type,
    prior_source_types,
    required,
):
    assert nutrition.meal_correction_confirmation_required(
        source_type,
        prior_source_types,
    ) is required


def test_text_only_correction_summary_is_informational_and_stays_c2():
    message = nutrition.format_meal_correction_confirmation(
        corrected_estimate(),
        confirmation_required=False,
    )

    assert message.startswith("[C2 Weak signal]")
    assert "Could the revised estimate" in message
    assert "No second confirmation is needed" in message
    assert "Does that reflect your correction?" not in message


def test_voice_correction_summary_keeps_explicit_confirmation_by_default():
    message = nutrition.format_meal_correction_confirmation(
        corrected_estimate()
    )

    assert message.startswith("[C2 Weak signal]")
    assert message.endswith("Does that reflect your correction?")
