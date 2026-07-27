"""Deterministic helpers for packaged-food nutrition labels.

Vision/OCR models are useful for transcribing what is visible on a package,
but they must not silently decide what a nutrient means, whether values are
per container or per 100 g, or how much a person consumed.  This module keeps
those decisions local and explicit:

* raw label rows are preserved;
* nutrient names are mapped through a small exact multilingual vocabulary;
* the declared energy is checked against ``4P + 9F + 4C``;
* totals are scaled only from an explicit basis and consumed amount.

The output remains C2 personal evidence.  Passing deterministic validation
reduces transcription mistakes; it does not turn a package label or a meal log
into a clinical-grade measurement.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional

from . import evidence

BASIS_PER_CONTAINER = "per_container"
BASIS_PER_SERVING = "per_serving"
BASIS_PER_100G = "per_100g"
BASIS_PER_100ML = "per_100ml"
BASIS_UNKNOWN = "unknown"

SUPPORTED_BASES = {
    BASIS_PER_CONTAINER,
    BASIS_PER_SERVING,
    BASIS_PER_100G,
    BASIS_PER_100ML,
    BASIS_UNKNOWN,
}

LABEL_CONFIDENCE_LEVEL = evidence.Confidence.C2.value
LABEL_CONFIDENCE = evidence.confidence_to_numeric(evidence.Confidence.C2)
LABEL_SCHEMA_VERSION = 2

_GRAM_UNITS = {"g", "gr", "gram", "grams", "г", "гр", "грамм", "грамма", "գ"}
_ML_UNITS = {"ml", "milliliter", "milliliters", "мл", "մլ"}
_KCAL_UNITS = {"kcal", "ккал", "կկալ"}

_NUTRIENT_ALIASES = {
    "protein_g": {
        "protein",
        "proteins",
        "белок",
        "белки",
        "սպ",
        "սպիտակուց",
        "սպիտակուցներ",
    },
    "fat_g": {
        "fat",
        "fats",
        "жир",
        "жиры",
        "ճ",
        "ճարպ",
        "ճարպեր",
    },
    "carb_g": {
        "carb",
        "carbs",
        "carbohydrate",
        "carbohydrates",
        "углевод",
        "углеводы",
        "ածխ",
        "ածխաջուր",
        "ածխաջրեր",
    },
}

_FULL_RE = re.compile(
    r"\b(?:все|всё|всю|весь|целиком|полностью|целую|целый)\b",
    re.IGNORECASE,
)
_HALF_RE = re.compile(r"\b(?:половин(?:а|у|ы)?|пол-?упаковки)\b", re.IGNORECASE)
_QUARTER_RE = re.compile(r"\b(?:четверт(?:ь|и|ую))\b", re.IGNORECASE)
_THIRD_RE = re.compile(r"\b(?:треть|третью)\b", re.IGNORECASE)
_FRACTION_RE = re.compile(r"(?<!\d)([1-9]\d*)\s*/\s*([1-9]\d*)(?!\d)")
_PERCENT_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*%")
_AMOUNT_RE = re.compile(
    r"(?<!\d)(\d+(?:[.,]\d+)?)\s*" r"(мл|ml|milliliters?|գ|грамм(?:а|ов)?|гр|г|g|grams?)\b",
    re.IGNORECASE,
)
_SERVING_RE = re.compile(
    r"(?:(\d+(?:[.,]\d+)?)\s*)?(?:порци(?:я|и|ю|й)|servings?)\b",
    re.IGNORECASE,
)
_EXPLICIT_BASIS_100G_RE = re.compile(
    r"(?:\b(?:на|за|в|per)\s*100\s*" r"(?:г|гр|грамм(?:а|ов)?|g)\b|" r"100\s*գ(?:-?ում|\s+համար)\b)",
    re.IGNORECASE,
)
_EXPLICIT_BASIS_100ML_RE = re.compile(
    r"(?:\b(?:на|за|в|per)\s*100\s*(?:мл|ml)\b|" r"100\s*մլ(?:-?ում|\s+համար)\b)",
    re.IGNORECASE,
)
_BASIS_SERVING_RE = re.compile(r"\b(?:порци(?:ю|я|и|ю|й)|serving)\b", re.IGNORECASE)
_BASIS_CONTAINER_RE = re.compile(
    r"\b(?:упаковк(?:а|у|и|е)|бутылк(?:а|у|и|е)|бан(?:ка|ку|ки|ке)|контейнер|container)\b",
    re.IGNORECASE,
)
_PARTIAL_CAVEAT_RE = re.compile(
    r"\b(?:не|почти|оставил(?:а)?|осталось|недоел(?:а)?|недопил(?:а)?)\b",
    re.IGNORECASE,
)
_EXPLICIT_BASIS_SERVING_RE = re.compile(
    r"(?:\b(?:на|за|значени[яй]|цифры|этикетк\w*)\b.{0,40}" r"\bпорци\w*\b|\bper\s+(?:one\s+)?serving\b)",
    re.IGNORECASE,
)
_EXPLICIT_BASIS_CONTAINER_RE = re.compile(
    r"(?:\b(?:на|за|значени[яй]|цифры|этикетк\w*)\b.{0,40}"
    r"\b(?:упаковк\w*|бутылк\w*|банк\w*|контейнер\w*)\b|"
    r"\bper\s+(?:the\s+)?(?:package|container|bottle)\b)",
    re.IGNORECASE,
)
_RANGE_AMOUNT_RE = re.compile(
    r"(?<!\d)\d+(?:[.,]\d+)?\s*" r"(?:[-–—]|\b(?:до|to|or)\b)\s*" r"\d+(?:[.,]\d+)?",
    re.IGNORECASE,
)
_NUMBER_TOKEN_RE = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?![\d.,])")


class NutritionLabelError(ValueError):
    """Base class for unsafe or malformed label data."""


class NutritionLabelNeedsClarification(NutritionLabelError):
    """More user-visible information is required before a total is safe."""


class NutritionLabelValidationError(NutritionLabelError):
    """Visible label values conflict strongly enough to block a record."""


class LabelCorrectionRedFlag(NutritionLabelError):
    """A label-consumption correction contains a health red flag."""

    def __init__(self, flags: List[Any]):
        super().__init__("red-flag text must not be interpreted as a label correction")
        self.flags = list(flags)


def _finite_number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise NutritionLabelError("%s must be numeric" % field)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise NutritionLabelError("%s must be numeric" % field)
    if not math.isfinite(number):
        raise NutritionLabelError("%s must be finite" % field)
    if positive and number <= 0:
        raise NutritionLabelError("%s must be positive" % field)
    if not positive and number < 0:
        raise NutritionLabelError("%s cannot be negative" % field)
    return number


def _text(value: Any, field: str, *, required: bool = False, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise NutritionLabelNeedsClarification("%s is not visible" % field)
    return text[:limit]


def _letters_only(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in normalized if char.isalpha())


def _normalized_visible_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def _basis_from_visible_text(text: str) -> str:
    raw = str(text or "").strip()
    matches = []
    if _EXPLICIT_BASIS_100G_RE.search(raw):
        matches.append(BASIS_PER_100G)
    if _EXPLICIT_BASIS_100ML_RE.search(raw):
        matches.append(BASIS_PER_100ML)

    letters = _letters_only(raw)
    serving_markers = (
        "порци",
        "serving",
        "portion",
        "բաժն",
        "չափաբաժն",
    )
    container_markers = (
        "упаковк",
        "бутылк",
        "банк",
        "контейнер",
        "package",
        "container",
        "bottle",
        "փաթեթ",
        "ամբողջ",
        "շիշ",
        "տուփ",
    )
    if any(marker in letters for marker in serving_markers):
        matches.append(BASIS_PER_SERVING)
    if any(marker in letters for marker in container_markers):
        matches.append(BASIS_PER_CONTAINER)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else BASIS_UNKNOWN


def _verify_model_basis(
    model_basis: str,
    basis_text: str,
    raw_label_text: str,
) -> Dict[str, Any]:
    visible_basis = BASIS_UNKNOWN
    exact_fragment = False
    normalized_basis_text = _normalized_visible_text(basis_text)
    if normalized_basis_text:
        exact_fragment = normalized_basis_text in _normalized_visible_text(raw_label_text)
        if exact_fragment:
            visible_basis = _basis_from_visible_text(basis_text)
    verified = (
        visible_basis
        if visible_basis != BASIS_UNKNOWN and model_basis in {BASIS_UNKNOWN, visible_basis}
        else BASIS_UNKNOWN
    )
    return {
        "status": "verified" if verified != BASIS_UNKNOWN else "unverified",
        "model_basis": model_basis,
        "visible_basis": visible_basis,
        "basis_text_is_raw_fragment": exact_fragment,
        "basis": verified,
    }


def canonical_nutrient(label: str) -> Optional[str]:
    """Map one exact multilingual nutrient label to a canonical field.

    Unknown rows (salt, fibre, sugar, and so on) deliberately return ``None``;
    callers preserve them but do not guess that they are a macronutrient.
    """
    key = _letters_only(label)
    for canonical, aliases in _NUTRIENT_ALIASES.items():
        if key in aliases:
            return canonical
    return None


def _numeric_token_value(token: str) -> float:
    return float(token.replace(",", "."))


def _number_is(value: float, token: str) -> bool:
    try:
        candidate = _numeric_token_value(token)
    except ValueError:
        return False
    return math.isclose(candidate, value, rel_tol=0.0, abs_tol=1e-9)


def _contains_literal_token(text: str, token: str) -> bool:
    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            return False
        before = text[index - 1] if index > 0 else ""
        after_index = index + len(token)
        after = text[after_index] if after_index < len(text) else ""
        if (not before or not before.isalnum()) and (not after or not after.isalnum()):
            return True
        start = index + 1


def _raw_fragment_has_field_boundary(
    raw_label_text: str,
    raw_row_text: str,
) -> bool:
    start = 0
    separators = "\n\r|/;"
    while True:
        index = raw_label_text.find(raw_row_text, start)
        if index < 0:
            return False
        boundary = max(raw_label_text.rfind(char, 0, index) for char in separators)
        prefix = raw_label_text[boundary + 1 : index]
        if not any(char.isalpha() for char in prefix):
            return True
        start = index + 1


def _bind_raw_row(
    *,
    label: str,
    value: float,
    raw_unit: str,
    raw_row_text: Any,
    raw_label_text: str,
    field: str,
    allow_prior_numbers: bool = False,
) -> str:
    row_text = _text(
        raw_row_text,
        field + ".raw_row_text",
        required=True,
        limit=1000,
    )
    if row_text not in raw_label_text or not _raw_fragment_has_field_boundary(
        raw_label_text,
        row_text,
    ):
        raise NutritionLabelNeedsClarification(
            "%s raw row must be a literal field-boundary fragment of raw_label_text" % field
        )

    leading = row_text.lstrip()
    while leading and not leading[0].isalpha():
        leading = leading[1:].lstrip()
    if not leading.startswith(label):
        raise NutritionLabelNeedsClarification("%s label must be copied exactly from the start of its raw row" % field)

    suffix = leading[len(label) :]
    number_matches = list(_NUMBER_TOKEN_RE.finditer(suffix))
    if not number_matches:
        raise NutritionLabelNeedsClarification("%s raw row does not contain its numeric value" % field)
    matching_index = next(
        (index for index, match in enumerate(number_matches) if _number_is(value, match.group(0))),
        None,
    )
    if matching_index is None or (matching_index != 0 and not allow_prior_numbers):
        raise NutritionLabelNeedsClarification("%s value is not the first value associated with its label" % field)
    match = number_matches[matching_index]
    next_start = number_matches[matching_index + 1].start() if matching_index + 1 < len(number_matches) else len(suffix)
    value_suffix = suffix[match.end() : next_start]
    if not _contains_literal_token(value_suffix, raw_unit):
        raise NutritionLabelNeedsClarification("%s unit must be copied exactly next to its value" % field)
    return row_text


def _normalize_nutrient_rows(
    rows: Any,
    raw_label_text: str,
) -> tuple[List[Dict[str, Any]], Dict[str, float]]:
    if not isinstance(rows, list):
        raise NutritionLabelError("nutrients must be a list")
    raw_rows: List[Dict[str, Any]] = []
    macros: Dict[str, float] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise NutritionLabelError("nutrients[%d] must be an object" % index)
        label = _text(row.get("label"), "nutrients[%d].label" % index, required=True, limit=160)
        value = _finite_number(row.get("value"), "nutrients[%d].value" % index)
        raw_unit = _text(row.get("unit"), "nutrients[%d].unit" % index, required=True, limit=40)
        unit = normalize_unit(raw_unit)
        canonical = canonical_nutrient(label)
        raw_row_text = _bind_raw_row(
            label=label,
            value=value,
            raw_unit=raw_unit,
            raw_row_text=row.get("raw_row_text"),
            raw_label_text=raw_label_text,
            field="nutrients[%d]" % index,
        )
        normalized_row = {
            "label": label,
            "value": value,
            "unit": raw_unit,
            "raw_row_text": raw_row_text,
            "canonical": canonical,
        }
        raw_rows.append(normalized_row)
        if canonical is None:
            continue
        if unit != "g":
            raise NutritionLabelNeedsClarification("%s must be expressed in grams" % label)
        if canonical in macros:
            raise NutritionLabelNeedsClarification("duplicate nutrient label for %s" % canonical)
        macros[canonical] = value
    missing = [field for field in ("protein_g", "fat_g", "carb_g") if field not in macros]
    if missing:
        raise NutritionLabelNeedsClarification("label is missing unambiguous rows for %s" % ", ".join(missing))
    return raw_rows, macros


def normalize_unit(value: Any) -> Optional[str]:
    """Normalize only units needed for safe nutrition calculations."""
    unit = unicodedata.normalize("NFKC", str(value or "")).strip().casefold().rstrip(".")
    if unit in _GRAM_UNITS:
        return "g"
    if unit in _ML_UNITS:
        return "ml"
    if unit in _KCAL_UNITS:
        return "kcal"
    return None


def _normalize_measure(
    amount: Any,
    unit: Any,
    field: str,
    *,
    required: bool = False,
) -> Optional[Dict[str, Any]]:
    if amount in (None, "") and unit in (None, ""):
        if required:
            raise NutritionLabelNeedsClarification("%s is required" % field)
        return None
    number = _finite_number(amount, field + ".amount", positive=True)
    normalized_unit = normalize_unit(unit)
    if normalized_unit not in {"g", "ml"}:
        raise NutritionLabelNeedsClarification("%s unit must be g or ml" % field)
    return {"amount": number, "unit": normalized_unit}


def _bind_measure_raw_row(
    *,
    amount: float,
    raw_unit: str,
    raw_row_text: Any,
    raw_label_text: str,
    field: str,
) -> str:
    row_text = _text(
        raw_row_text,
        field + ".raw_row_text",
        required=True,
        limit=1000,
    )
    if row_text not in raw_label_text or not _raw_fragment_has_field_boundary(
        raw_label_text,
        row_text,
    ):
        raise NutritionLabelNeedsClarification(
            "%s raw row must be a literal field-boundary fragment of raw_label_text" % field
        )
    number_matches = list(_NUMBER_TOKEN_RE.finditer(row_text))
    for index, match in enumerate(number_matches):
        if not _number_is(amount, match.group(0)):
            continue
        next_start = number_matches[index + 1].start() if index + 1 < len(number_matches) else len(row_text)
        if _contains_literal_token(row_text[match.end() : next_start], raw_unit):
            return row_text
    raise NutritionLabelNeedsClarification("%s amount and unit must be copied together from its raw row" % field)


def _require_literal_field_fragment(
    value: str,
    raw_label_text: str,
    field: str,
) -> None:
    if value not in raw_label_text or not _raw_fragment_has_field_boundary(
        raw_label_text,
        value,
    ):
        raise NutritionLabelNeedsClarification(
            "%s must be copied as a literal field-boundary fragment of raw_label_text" % field
        )


def _normalize_bound_measure(
    amount: Any,
    unit: Any,
    raw_row_text: Any,
    raw_label_text: str,
    field: str,
) -> Optional[Dict[str, Any]]:
    measure = _normalize_measure(amount, unit, field)
    if measure is None:
        if raw_row_text not in (None, ""):
            raise NutritionLabelNeedsClarification("%s raw row has no corresponding measure" % field)
        return None
    raw_unit = _text(
        unit,
        field + ".unit",
        required=True,
        limit=40,
    )
    bound_row = _bind_measure_raw_row(
        amount=measure["amount"],
        raw_unit=raw_unit,
        raw_row_text=raw_row_text,
        raw_label_text=raw_label_text,
        field=field,
    )
    return {
        "amount": measure["amount"],
        "unit": measure["unit"],
        "raw_unit": raw_unit,
        "raw_row_text": bound_row,
    }


def _energy_from_payload(
    payload: Dict[str, Any],
    raw_label_text: str,
) -> Dict[str, Any]:
    energy = payload.get("energy")
    if isinstance(energy, dict):
        label = _text(energy.get("label"), "energy.label", required=True, limit=160)
        value = _finite_number(energy.get("value"), "energy.value", positive=True)
        raw_unit = _text(
            energy.get("raw_unit") or energy.get("unit"),
            "energy.unit",
            required=True,
            limit=40,
        )
        unit = normalize_unit(raw_unit)
        if unit != "kcal":
            raise NutritionLabelNeedsClarification("energy unit must be kcal")
        raw_row_text = _bind_raw_row(
            label=label,
            value=value,
            raw_unit=raw_unit,
            raw_row_text=energy.get("raw_row_text"),
            raw_label_text=raw_label_text,
            field="energy",
            # Energy rows commonly put kJ before kcal. The selected kcal still
            # has to be paired with the exact copied kcal unit.
            allow_prior_numbers=True,
        )
        return {
            "label": label,
            "value": value,
            "unit": unit,
            "raw_unit": raw_unit,
            "raw_row_text": raw_row_text,
        }

    # Backward-compatible input shape for adapters that already extracted a
    # dedicated kcal field.  It remains visibly separate from the raw rows.
    value = _finite_number(payload.get("energy_kcal"), "energy_kcal", positive=True)
    label = _text(
        payload.get("energy_label") or "energy_kcal",
        "energy_label",
        required=True,
        limit=160,
    )
    raw_unit = _text(
        payload.get("energy_unit") or "kcal",
        "energy_unit",
        required=True,
        limit=40,
    )
    if normalize_unit(raw_unit) != "kcal":
        raise NutritionLabelNeedsClarification("energy unit must be kcal")
    raw_row_text = _bind_raw_row(
        label=label,
        value=value,
        raw_unit=raw_unit,
        raw_row_text=payload.get("energy_raw_row_text"),
        raw_label_text=raw_label_text,
        field="energy",
        allow_prior_numbers=True,
    )
    return {
        "label": label,
        "value": value,
        "unit": "kcal",
        "raw_unit": raw_unit,
        "raw_row_text": raw_row_text,
    }


def _macro_validation(kcal: float, protein: float, fat: float, carbs: float) -> Dict[str, Any]:
    macro_kcal = 4.0 * protein + 9.0 * fat + 4.0 * carbs
    difference = abs(kcal - macro_kcal)
    tolerance = max(20.0, kcal * 0.10)
    if difference > tolerance:
        raise NutritionLabelValidationError(
            "declared energy and macronutrients disagree " "(%.2f kcal vs %.2f kcal from 4P+9F+4C)" % (kcal, macro_kcal)
        )
    return {
        "status": "consistent",
        "macro_kcal": round(macro_kcal, 2),
        "difference_kcal": round(difference, 2),
        "tolerance_kcal": round(tolerance, 2),
        "formula": "4P+9F+4C",
    }


def _extraction_fingerprint(
    *,
    product_name_original: str,
    raw_label_text: str,
    model_basis: str,
    basis_text: str,
    package: Optional[Dict[str, Any]],
    serving: Optional[Dict[str, Any]],
    energy: Dict[str, Any],
    raw_rows: List[Dict[str, Any]],
) -> str:
    payload = {
        "product_name_original": product_name_original,
        "raw_label_text": raw_label_text,
        "model_basis": model_basis,
        "basis_text": basis_text,
        "package": package,
        "serving": serving,
        "energy": energy,
        "raw_nutrients": [
            {
                "label": row["label"],
                "value": row["value"],
                "unit": row["unit"],
                "raw_row_text": row["raw_row_text"],
            }
            for row in raw_rows
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _normalize_provenance(
    value: Any,
    extraction_sha256: str,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise NutritionLabelError("provenance must be an object")
    try:
        provenance = json.loads(
            json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError):
        raise NutritionLabelError("provenance must be JSON-serializable")
    provenance.pop("extraction_sha256", None)
    provenance.pop("provenance_sha256", None)
    provider = str(provenance.get("provider") or "").strip()
    model = str(
        provenance.get("model") or provenance.get("response_model") or provenance.get("requested_model") or ""
    ).strip()
    if not provider or not model:
        raise NutritionLabelNeedsClarification("provenance requires provider and model")
    provenance["provider"] = provider
    provenance["model"] = model
    provenance["extraction_sha256"] = extraction_sha256
    serialized = json.dumps(
        provenance,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    provenance["provenance_sha256"] = hashlib.sha256(serialized).hexdigest()
    return provenance


def _calculation_context_fingerprint(
    *,
    basis: str,
    package: Optional[Dict[str, Any]],
    serving: Optional[Dict[str, Any]],
    declared: Dict[str, Any],
    extraction_sha256: str,
) -> str:
    serialized = json.dumps(
        {
            "basis": basis,
            "package": package,
            "serving": serving,
            "declared": declared,
            "extraction_sha256": extraction_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def normalize_label_extraction(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one model/OCR transcription without inventing missing fields."""
    if not isinstance(payload, dict):
        raise NutritionLabelError("label extraction must be an object")
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"label", "nutrition_label"}:
        raise NutritionLabelError("label extraction mode is required")

    model_basis = str(payload.get("nutrition_basis") or payload.get("basis") or "").strip().lower()
    if model_basis not in SUPPORTED_BASES:
        raise NutritionLabelNeedsClarification("nutrition basis is not explicit")

    product_name_original = _text(
        payload.get("product_name_original"),
        "product_name_original",
        required=True,
        limit=240,
    )
    product_name_ru = _text(payload.get("product_name_ru"), "product_name_ru", limit=240)
    raw_label_text = _text(
        payload.get("raw_label_text"),
        "raw_label_text",
        required=True,
        limit=8000,
    )
    _require_literal_field_fragment(
        product_name_original,
        raw_label_text,
        "product_name_original",
    )
    basis_text = _text(payload.get("basis_text"), "basis_text", limit=300)
    basis_verification = _verify_model_basis(
        model_basis,
        basis_text,
        raw_label_text,
    )
    basis = basis_verification["basis"]

    package = _normalize_bound_measure(
        payload.get("package_amount"),
        payload.get("package_unit"),
        payload.get("package_raw_row_text"),
        raw_label_text,
        "package",
    )
    serving = _normalize_bound_measure(
        payload.get("serving_amount"),
        payload.get("serving_unit"),
        payload.get("serving_raw_row_text"),
        raw_label_text,
        "serving",
    )
    if basis == BASIS_PER_SERVING and serving is None:
        raise NutritionLabelNeedsClarification("serving size is required for per-serving values")

    energy = _energy_from_payload(payload, raw_label_text)
    raw_rows, macros = _normalize_nutrient_rows(
        payload.get("nutrients"),
        raw_label_text,
    )

    declared = {
        "kcal": energy["value"],
        "protein_g": macros["protein_g"],
        "fat_g": macros["fat_g"],
        "carb_g": macros["carb_g"],
    }
    validation = _macro_validation(
        declared["kcal"],
        declared["protein_g"],
        declared["fat_g"],
        declared["carb_g"],
    )

    uncertainties = payload.get("uncertainties") or []
    if not isinstance(uncertainties, list):
        raise NutritionLabelError("uncertainties must be a list")
    normalized_uncertainties = [_text(item, "uncertainty", limit=300) for item in uncertainties if str(item).strip()]
    if (
        model_basis != BASIS_UNKNOWN
        and basis == BASIS_UNKNOWN
        and "nutrition basis could not be verified locally" not in normalized_uncertainties
    ):
        normalized_uncertainties.append("nutrition basis could not be verified locally")
    extraction_sha256 = _extraction_fingerprint(
        product_name_original=product_name_original,
        raw_label_text=raw_label_text,
        model_basis=model_basis,
        basis_text=basis_text,
        package=package,
        serving=serving,
        energy=energy,
        raw_rows=raw_rows,
    )
    provenance = _normalize_provenance(
        payload.get("provenance"),
        extraction_sha256,
    )

    return {
        "schema_version": LABEL_SCHEMA_VERSION,
        "mode": "label",
        "product_name_original": product_name_original,
        "product_name_ru": product_name_ru,
        # A translation is another model hypothesis.  Keep the exact visible
        # product name canonical and expose the translation separately.
        "title": product_name_original,
        "language": _text(payload.get("language"), "language", limit=40),
        "raw_label_text": raw_label_text,
        "basis": basis,
        "model_basis": model_basis,
        "basis_text": basis_text,
        "basis_verification": basis_verification,
        "package": package,
        "serving": serving,
        "energy": energy,
        "raw_nutrients": raw_rows,
        "declared": declared,
        "validation": validation,
        "uncertainties": normalized_uncertainties,
        "model_confidence": (
            str(payload.get("confidence") or "").strip().lower()
            if str(payload.get("confidence") or "").strip().lower() in {"low", "medium", "high"}
            else "unknown"
        ),
        "confidence_level": LABEL_CONFIDENCE_LEVEL,
        "confidence": LABEL_CONFIDENCE,
        "provenance": provenance,
    }


def validate_normalized_label(label: Dict[str, Any]) -> Dict[str, Any]:
    """Revalidate a JSON-round-tripped normalized label before calculation."""
    if not isinstance(label, dict):
        raise NutritionLabelError("normalized label must be an object")
    if label.get("schema_version") != LABEL_SCHEMA_VERSION:
        raise NutritionLabelError("normalized label schema version is invalid")
    if label.get("mode") != "label":
        raise NutritionLabelError("normalized label mode is invalid")

    product_name_original = _text(
        label.get("product_name_original"),
        "product_name_original",
        required=True,
        limit=240,
    )
    raw_label_text = _text(
        label.get("raw_label_text"),
        "raw_label_text",
        required=True,
        limit=8000,
    )
    _require_literal_field_fragment(
        product_name_original,
        raw_label_text,
        "product_name_original",
    )
    model_basis = str(label.get("model_basis") or "").strip().lower()
    if model_basis not in SUPPORTED_BASES:
        raise NutritionLabelNeedsClarification("model basis is invalid")
    basis_text = _text(label.get("basis_text"), "basis_text", limit=300)
    basis_verification = _verify_model_basis(
        model_basis,
        basis_text,
        raw_label_text,
    )

    package_value = label.get("package")
    package = (
        _normalize_bound_measure(
            package_value.get("amount"),
            package_value.get("raw_unit") or package_value.get("unit"),
            package_value.get("raw_row_text"),
            raw_label_text,
            "package",
        )
        if isinstance(package_value, dict)
        else None
    )
    if package_value is not None and package is None:
        raise NutritionLabelNeedsClarification("package measure is invalid")
    serving_value = label.get("serving")
    serving = (
        _normalize_bound_measure(
            serving_value.get("amount"),
            serving_value.get("raw_unit") or serving_value.get("unit"),
            serving_value.get("raw_row_text"),
            raw_label_text,
            "serving",
        )
        if isinstance(serving_value, dict)
        else None
    )
    if serving_value is not None and serving is None:
        raise NutritionLabelNeedsClarification("serving measure is invalid")

    energy_value = label.get("energy")
    if not isinstance(energy_value, dict):
        raise NutritionLabelError("energy is required")
    energy = _energy_from_payload(
        {"energy": energy_value},
        raw_label_text,
    )
    stored_rows = label.get("raw_nutrients")
    if not isinstance(stored_rows, list) or any(not isinstance(row, dict) for row in stored_rows):
        raise NutritionLabelError("raw_nutrients must be a list of objects")
    normalized_rows, macros = _normalize_nutrient_rows(
        [
            {
                "label": row.get("label"),
                "value": row.get("value"),
                "unit": row.get("unit"),
                "raw_row_text": row.get("raw_row_text"),
            }
            for row in stored_rows
        ],
        raw_label_text,
    )
    declared = label.get("declared")
    if not isinstance(declared, dict):
        raise NutritionLabelError("declared values are required")
    expected_declared = {
        "kcal": energy["value"],
        "protein_g": macros["protein_g"],
        "fat_g": macros["fat_g"],
        "carb_g": macros["carb_g"],
    }
    for field, expected in expected_declared.items():
        actual = _finite_number(
            declared.get(field),
            "declared.%s" % field,
            positive=(field == "kcal"),
        )
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9):
            raise NutritionLabelValidationError("declared.%s does not match the raw label row" % field)
    validation = _macro_validation(
        expected_declared["kcal"],
        expected_declared["protein_g"],
        expected_declared["fat_g"],
        expected_declared["carb_g"],
    )

    extraction_sha256 = _extraction_fingerprint(
        product_name_original=product_name_original,
        raw_label_text=raw_label_text,
        model_basis=model_basis,
        basis_text=basis_text,
        package=package,
        serving=serving,
        energy=energy,
        raw_rows=normalized_rows,
    )
    stored_provenance = label.get("provenance")
    provenance = _normalize_provenance(stored_provenance, extraction_sha256)
    if str((stored_provenance or {}).get("extraction_sha256") or "") != extraction_sha256:
        raise NutritionLabelValidationError("label extraction fingerprint does not match")
    if str((stored_provenance or {}).get("provenance_sha256") or "") != provenance["provenance_sha256"]:
        raise NutritionLabelValidationError("label provenance fingerprint does not match")

    basis = str(label.get("basis") or "").strip().lower()
    if basis not in SUPPORTED_BASES:
        raise NutritionLabelNeedsClarification("nutrition basis is invalid")
    resolution = label.get("basis_resolution")
    if resolution is not None:
        resolution = _normalize_basis_resolution(resolution, basis)
    elif basis != basis_verification["basis"]:
        raise NutritionLabelValidationError("normalized basis does not match visible label text")
    if basis == BASIS_PER_SERVING and serving is None:
        raise NutritionLabelNeedsClarification("serving size is required for per-serving values")

    validated = json.loads(json.dumps(label, ensure_ascii=False))
    validated.update(
        {
            "schema_version": LABEL_SCHEMA_VERSION,
            "mode": "label",
            "title": product_name_original,
            "product_name_original": product_name_original,
            "raw_label_text": raw_label_text,
            "model_basis": model_basis,
            "basis": basis,
            "basis_text": basis_text,
            "basis_verification": basis_verification,
            "package": package,
            "serving": serving,
            "energy": energy,
            "raw_nutrients": normalized_rows,
            "declared": expected_declared,
            "validation": validation,
            "confidence_level": LABEL_CONFIDENCE_LEVEL,
            "confidence": LABEL_CONFIDENCE,
            "provenance": provenance,
        }
    )
    if resolution is not None:
        validated["basis_resolution"] = resolution
    return validated


def parse_consumed_amount(text: str) -> Dict[str, Any]:
    """Parse a short Russian/English answer about how much was consumed.

    The parser intentionally accepts only explicit units, fractions, servings,
    or clear whole-package wording.  A bare number is ambiguous and therefore
    rejected.
    """
    raw = str(text or "").strip()
    if not raw:
        raise NutritionLabelNeedsClarification("consumed amount is missing")
    if _PARTIAL_CAVEAT_RE.search(raw):
        raise NutritionLabelNeedsClarification("ambiguous or partial consumption needs an explicit amount")
    if _RANGE_AMOUNT_RE.search(raw):
        raise NutritionLabelNeedsClarification("consumed amount ranges need one explicit amount")

    amount_source = raw
    for basis_pattern in (
        _EXPLICIT_BASIS_100G_RE,
        _EXPLICIT_BASIS_100ML_RE,
    ):
        amount_source = basis_pattern.sub(
            lambda match: " " * len(match.group(0)),
            amount_source,
        )
    amount_matches = list(_AMOUNT_RE.finditer(amount_source))
    if len(amount_matches) > 1:
        raise NutritionLabelNeedsClarification("more than one consumed amount is ambiguous")
    if amount_matches:
        amount_match = amount_matches[0]
        amount = _finite_number(amount_match.group(1).replace(",", "."), "consumed amount", positive=True)
        unit = normalize_unit(amount_match.group(2))
        if unit not in {"g", "ml"}:
            raise NutritionLabelNeedsClarification("consumed amount unit must be g or ml")
        return {"kind": "amount", "amount": amount, "unit": unit, "raw_text": raw}

    serving_match = _SERVING_RE.search(raw)
    if serving_match:
        count = _finite_number(
            (serving_match.group(1) or "1").replace(",", "."),
            "serving count",
            positive=True,
        )
        return {"kind": "servings", "count": count, "raw_text": raw}

    fraction_match = _FRACTION_RE.search(raw)
    if fraction_match:
        numerator = float(fraction_match.group(1))
        denominator = float(fraction_match.group(2))
        fraction = numerator / denominator
        if not (0 < fraction <= 1):
            raise NutritionLabelNeedsClarification("package fraction must be between 0 and 1")
        return {"kind": "package_fraction", "fraction": fraction, "raw_text": raw}

    percent_match = _PERCENT_RE.search(raw)
    if percent_match:
        fraction = float(percent_match.group(1).replace(",", ".")) / 100.0
        if not (0 < fraction <= 1):
            raise NutritionLabelNeedsClarification("consumed percent must be between 0 and 100")
        return {"kind": "package_fraction", "fraction": fraction, "raw_text": raw}

    if _HALF_RE.search(raw):
        return {"kind": "package_fraction", "fraction": 0.5, "raw_text": raw}
    if _QUARTER_RE.search(raw):
        return {"kind": "package_fraction", "fraction": 0.25, "raw_text": raw}
    if _THIRD_RE.search(raw):
        return {"kind": "package_fraction", "fraction": 1.0 / 3.0, "raw_text": raw}
    if _FULL_RE.search(raw):
        return {"kind": "package_fraction", "fraction": 1.0, "raw_text": raw}

    raise NutritionLabelNeedsClarification("say whether the whole package was consumed or give grams/ml")


def parse_label_basis_text(text: str) -> str:
    """Parse a user's explicit answer about what the printed values refer to."""
    basis = _basis_from_visible_text(str(text or "").strip())
    if basis == BASIS_UNKNOWN:
        raise NutritionLabelNeedsClarification(
            "say whether values are per 100 g, per 100 ml, per serving, or per package"
        )
    return basis


def detect_explicit_basis_correction(text: str) -> Optional[str]:
    """Return a basis only when a consumption reply explicitly corrects it."""
    raw = str(text or "").strip()
    matches = []
    if _EXPLICIT_BASIS_100G_RE.search(raw):
        matches.append(BASIS_PER_100G)
    if _EXPLICIT_BASIS_100ML_RE.search(raw):
        matches.append(BASIS_PER_100ML)
    if _EXPLICIT_BASIS_SERVING_RE.search(raw):
        matches.append(BASIS_PER_SERVING)
    if _EXPLICIT_BASIS_CONTAINER_RE.search(raw):
        matches.append(BASIS_PER_CONTAINER)
    unique = list(dict.fromkeys(matches))
    if len(unique) > 1:
        raise NutritionLabelNeedsClarification("basis correction contains more than one basis")
    return unique[0] if unique else None


def _normalize_basis_resolution(
    resolution: Any,
    basis: str,
) -> Dict[str, Any]:
    if not isinstance(resolution, dict):
        raise NutritionLabelNeedsClarification("basis resolution is invalid")
    source = str(resolution.get("source") or "")
    text = str(resolution.get("text") or "").strip()
    artifact_ids = resolution.get("artifact_ids")
    normalized_artifact_ids = [str(item).strip() for item in artifact_ids] if isinstance(artifact_ids, list) else []
    if (
        source not in {"user_reply", "user_correction"}
        or not text
        or not normalized_artifact_ids
        or any(not item for item in normalized_artifact_ids)
        or detect_explicit_basis_correction(text) != basis
    ):
        raise NutritionLabelNeedsClarification("user-confirmed basis lacks provenance")
    return {
        "source": source,
        "text": text,
        "artifact_ids": list(dict.fromkeys(normalized_artifact_ids)),
    }


def _same_dimension(left: str, right: str) -> bool:
    return left == right and left in {"g", "ml"}


def _reject_beyond_known_package(
    amount: float,
    unit: str,
    package: Optional[Dict[str, Any]],
) -> None:
    if not package or not _same_dimension(unit, package.get("unit")):
        return
    package_amount = _finite_number(
        package.get("amount"),
        "package amount",
        positive=True,
    )
    tolerance = max(1e-9, package_amount * 1e-9)
    if amount > package_amount + tolerance:
        raise NutritionLabelNeedsClarification("consumed amount exceeds one known package; say how many packages")


def _factor_for_consumption(label: Dict[str, Any], consumed: Dict[str, Any]) -> float:
    basis = label["basis"]
    if basis not in SUPPORTED_BASES:
        raise NutritionLabelNeedsClarification("nutrition basis is invalid")
    if basis == BASIS_UNKNOWN:
        raise NutritionLabelNeedsClarification("nutrition basis is unknown")

    kind = consumed.get("kind")
    package = label.get("package")
    serving = label.get("serving")

    if basis == BASIS_PER_CONTAINER:
        if kind == "package_fraction":
            fraction = _finite_number(
                consumed.get("fraction"),
                "package fraction",
                positive=True,
            )
            if fraction > 1:
                raise NutritionLabelNeedsClarification("package fraction must be between 0 and 1")
            return fraction
        if kind == "amount" and package:
            if not _same_dimension(consumed.get("unit"), package["unit"]):
                raise NutritionLabelNeedsClarification("consumed amount and package use different units")
            amount = _finite_number(
                consumed.get("amount"),
                "consumed amount",
                positive=True,
            )
            _reject_beyond_known_package(amount, consumed.get("unit"), package)
            return amount / package["amount"]
        raise NutritionLabelNeedsClarification("per-container values require a package fraction or matching grams/ml")

    if basis == BASIS_PER_SERVING:
        if kind == "servings":
            count = _finite_number(
                consumed.get("count"),
                "serving count",
                positive=True,
            )
            if (
                serving
                and package
                and _same_dimension(
                    serving["unit"],
                    package["unit"],
                )
            ):
                _reject_beyond_known_package(
                    count * serving["amount"],
                    serving["unit"],
                    package,
                )
            return count
        if kind == "amount" and serving:
            if not _same_dimension(consumed.get("unit"), serving["unit"]):
                raise NutritionLabelNeedsClarification("consumed amount and serving use different units")
            amount = _finite_number(
                consumed.get("amount"),
                "consumed amount",
                positive=True,
            )
            _reject_beyond_known_package(amount, consumed.get("unit"), package)
            return amount / serving["amount"]
        if kind == "package_fraction" and package and serving:
            if not _same_dimension(package["unit"], serving["unit"]):
                raise NutritionLabelNeedsClarification("package and serving use different units")
            fraction = _finite_number(consumed.get("fraction"), "package fraction", positive=True)
            if fraction > 1:
                raise NutritionLabelNeedsClarification("package fraction must be between 0 and 1")
            return (package["amount"] * fraction) / serving["amount"]
        raise NutritionLabelNeedsClarification("per-serving values require servings or an explicit matching amount")

    expected_unit = "g" if basis == BASIS_PER_100G else "ml"
    amount = None
    if kind == "amount":
        if consumed.get("unit") != expected_unit:
            raise NutritionLabelNeedsClarification("%s values require consumed amount in %s" % (basis, expected_unit))
        amount = _finite_number(consumed.get("amount"), "consumed amount", positive=True)
        _reject_beyond_known_package(amount, expected_unit, package)
    elif kind == "package_fraction" and package:
        if package["unit"] != expected_unit:
            raise NutritionLabelNeedsClarification("%s values require package amount in %s" % (basis, expected_unit))
        fraction = _finite_number(consumed.get("fraction"), "package fraction", positive=True)
        if fraction > 1:
            raise NutritionLabelNeedsClarification("package fraction must be between 0 and 1")
        amount = package["amount"] * fraction
    else:
        raise NutritionLabelNeedsClarification("%s values require explicit consumed amount or package size" % basis)
    return amount / 100.0


def calculate_consumed_estimate(
    label: Dict[str, Any],
    consumed: Dict[str, Any],
) -> Dict[str, Any]:
    """Scale validated declared values into one C2 meal estimate."""
    if not isinstance(label, dict) or label.get("schema_version") != LABEL_SCHEMA_VERSION:
        label = normalize_label_extraction(label)
    else:
        label = validate_normalized_label(label)
    if not isinstance(consumed, dict):
        raise NutritionLabelNeedsClarification("consumed amount is required")

    factor = _factor_for_consumption(label, consumed)
    if not math.isfinite(factor) or factor <= 0:
        raise NutritionLabelNeedsClarification("consumed amount must be positive")

    declared = label["declared"]
    totals = {key: round(float(declared[key]) * factor, 2) for key in ("kcal", "protein_g", "fat_g", "carb_g")}
    calculation_context_sha256 = _calculation_context_fingerprint(
        basis=label["basis"],
        package=label.get("package"),
        serving=label.get("serving"),
        declared=declared,
        extraction_sha256=label["provenance"]["extraction_sha256"],
    )
    return {
        "title": label["title"],
        **totals,
        "note": "с этикетки; база %s, множитель %.4g" % (label["basis"], factor),
        "method": "package-label",
        "confidence_level": LABEL_CONFIDENCE_LEVEL,
        "confidence": LABEL_CONFIDENCE,
        "nutrition_basis": label["basis"],
        "scale_factor": factor,
        "consumed": dict(consumed),
        "package": json.loads(json.dumps(label.get("package"), ensure_ascii=False)),
        "serving": json.loads(json.dumps(label.get("serving"), ensure_ascii=False)),
        "declared": dict(declared),
        "validation": dict(label["validation"]),
        "calculation_context_sha256": calculation_context_sha256,
        "basis_resolution": json.loads(json.dumps(label.get("basis_resolution"), ensure_ascii=False)),
        "provenance": json.loads(json.dumps(label["provenance"], ensure_ascii=False)),
    }


def estimate_from_consumption_text(label: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Convenience wrapper used by text and locally transcribed voice replies."""
    return calculate_consumed_estimate(label, parse_consumed_amount(text))


def _validated_label_estimate(estimate: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(estimate, dict):
        raise NutritionLabelError("label estimate must be an object")
    title = _text(estimate.get("title"), "title", required=True, limit=240)
    basis = str(estimate.get("nutrition_basis") or "").strip().lower()
    if basis not in SUPPORTED_BASES or basis == BASIS_UNKNOWN:
        raise NutritionLabelNeedsClarification("label estimate requires a resolved nutrition basis")
    package_value = estimate.get("package")
    if package_value is not None and not isinstance(package_value, dict):
        raise NutritionLabelNeedsClarification("estimate package is invalid")
    package = (
        _normalize_bound_measure(
            package_value.get("amount"),
            package_value.get("raw_unit") or package_value.get("unit"),
            package_value.get("raw_row_text"),
            str(package_value.get("raw_row_text") or ""),
            "estimate package",
        )
        if package_value is not None
        else None
    )
    if package_value is not None and package is None:
        raise NutritionLabelNeedsClarification("estimate package is invalid")
    serving_value = estimate.get("serving")
    if serving_value is not None and not isinstance(serving_value, dict):
        raise NutritionLabelNeedsClarification("estimate serving is invalid")
    serving = (
        _normalize_bound_measure(
            serving_value.get("amount"),
            serving_value.get("raw_unit") or serving_value.get("unit"),
            serving_value.get("raw_row_text"),
            str(serving_value.get("raw_row_text") or ""),
            "estimate serving",
        )
        if serving_value is not None
        else None
    )
    if serving_value is not None and serving is None:
        raise NutritionLabelNeedsClarification("estimate serving is invalid")
    consumed_value = estimate.get("consumed")
    if not isinstance(consumed_value, dict):
        raise NutritionLabelNeedsClarification("label estimate requires consumed amount provenance")
    try:
        consumed = json.loads(json.dumps(consumed_value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        raise NutritionLabelError("consumed amount must be JSON-serializable")
    calculated_factor = _factor_for_consumption(
        {
            "basis": basis,
            "package": package,
            "serving": serving,
        },
        consumed,
    )
    stored_factor = _finite_number(
        estimate.get("scale_factor"),
        "scale_factor",
        positive=True,
    )
    if not math.isclose(
        stored_factor,
        calculated_factor,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise NutritionLabelValidationError("scale_factor does not match consumed amount")

    declared_value = estimate.get("declared")
    if not isinstance(declared_value, dict):
        raise NutritionLabelError("label estimate requires declared values")
    declared: Dict[str, float] = {}
    for field in ("kcal", "protein_g", "fat_g", "carb_g"):
        declared[field] = _finite_number(
            declared_value.get(field),
            "declared.%s" % field,
            positive=(field == "kcal"),
        )
    validation = _macro_validation(
        declared["kcal"],
        declared["protein_g"],
        declared["fat_g"],
        declared["carb_g"],
    )

    normalized: Dict[str, Any] = {"title": title}
    for field in ("kcal", "protein_g", "fat_g", "carb_g"):
        actual = _finite_number(
            estimate.get(field),
            field,
            positive=(field == "kcal"),
        )
        expected = round(declared[field] * calculated_factor, 2)
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9):
            raise NutritionLabelValidationError("%s does not match declared value times scale_factor" % field)
        normalized[field] = expected

    provenance_value = estimate.get("provenance")
    if not isinstance(provenance_value, dict):
        raise NutritionLabelNeedsClarification("label estimate requires provenance")
    extraction_sha256 = str(provenance_value.get("extraction_sha256") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", extraction_sha256):
        raise NutritionLabelValidationError("label estimate extraction fingerprint is invalid")
    provenance = _normalize_provenance(
        provenance_value,
        extraction_sha256,
    )
    if str(provenance_value.get("provenance_sha256") or "") != provenance["provenance_sha256"]:
        raise NutritionLabelValidationError("label estimate provenance fingerprint does not match")
    calculation_context_sha256 = _calculation_context_fingerprint(
        basis=basis,
        package=package,
        serving=serving,
        declared=declared,
        extraction_sha256=extraction_sha256,
    )
    if str(estimate.get("calculation_context_sha256") or "") != calculation_context_sha256:
        raise NutritionLabelValidationError("label estimate calculation context fingerprint does not match")

    resolution_value = estimate.get("basis_resolution")
    resolution = _normalize_basis_resolution(resolution_value, basis) if resolution_value is not None else None
    normalized.update(
        {
            "note": _text(estimate.get("note"), "note", limit=500),
            "method": "package-label",
            "confidence_level": LABEL_CONFIDENCE_LEVEL,
            "confidence": LABEL_CONFIDENCE,
            "nutrition_basis": basis,
            "scale_factor": calculated_factor,
            "consumed": consumed,
            "package": package,
            "serving": serving,
            "declared": declared,
            "validation": validation,
            "calculation_context_sha256": calculation_context_sha256,
            "basis_resolution": resolution,
            "provenance": provenance,
        }
    )
    return normalized


def _c2_question(text: str) -> str:
    body = str(text or "").strip().rstrip(".?")
    if not body.startswith("[C2"):
        body = "[C2 Weak signal] " + body
    return body + "?"


def build_label_meal_record(
    current_record: Dict[str, Any],
    estimate: Dict[str, Any],
    *,
    display_title: Optional[str] = None,
    display_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a revised meal record while preserving decimal label values."""
    if not isinstance(current_record, dict) or not current_record.get("id"):
        raise NutritionLabelError("current meal record is required")
    tags = set(current_record.get("tags") or [])
    kind = current_record.get("observation_kind")
    if "meal" not in tags and kind not in ("meal", "nutrition_meal"):
        raise NutritionLabelError("record is not a meal observation")

    normalized = _validated_label_estimate(estimate)
    updated = json.loads(json.dumps(current_record, ensure_ascii=False))
    metadata = dict(updated.get("metadata") or {})
    metadata.update(normalized)
    metadata["method"] = "package-label+user-consumption"
    metadata["confidence_level"] = LABEL_CONFIDENCE_LEVEL
    updated["metadata"] = metadata
    updated["confidence"] = LABEL_CONFIDENCE
    updated["metric_name"] = "meal_kcal"
    updated["value"] = normalized["kcal"]
    updated["unit"] = "kcal"
    updated["title"] = _c2_question(display_title or "Could this be %s" % normalized["title"])
    updated["summary"] = _c2_question(
        display_summary
        or (
            "Could %s be ~%.2f kcal (P%.2f / F%.2f / C%.2f)"
            % (
                normalized["title"],
                normalized["kcal"],
                normalized["protein_g"],
                normalized["fat_g"],
                normalized["carb_g"],
            )
        )
    )
    return updated


def apply_label_consumption_correction(
    db_path: Any,
    record_id: str,
    label: Dict[str, Any],
    correction_text: str,
    revision_id: str,
    created_at: str,
    source_type: str,
    evidence_artifact_ids: Optional[List[str]] = None,
    expected_revision: Optional[int] = None,
    display_title: Optional[str] = None,
    display_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply an audited text/voice amount correction using local arithmetic."""
    correction = str(correction_text or "").strip()
    if not correction:
        raise NutritionLabelNeedsClarification("consumption correction text is required")
    red_flags = evidence.scan_text_red_flags(correction)
    if red_flags:
        raise LabelCorrectionRedFlag(red_flags)

    from . import index

    current = index.get_record(db_path, record_id)
    if current is None:
        raise KeyError("record not found: %s" % record_id)
    observed_revision = int((current.get("metadata") or {}).get("revision") or 0)
    estimate = estimate_from_consumption_text(label, correction)
    updated = build_label_meal_record(
        current,
        estimate,
        display_title=display_title,
        display_summary=display_summary,
    )
    result = index.apply_record_revision(
        db_path,
        updated_record=updated,
        revision_id=revision_id,
        created_at=created_at,
        reason="user_label_consumption_correction",
        actor="user",
        evidence_artifact_ids=evidence_artifact_ids,
        patch={
            "source_type": source_type,
            "correction_text": correction,
            "calculation": _validated_label_estimate(estimate),
            "confidence_level": LABEL_CONFIDENCE_LEVEL,
        },
        expected_revision=(observed_revision if expected_revision is None else expected_revision),
    )
    if result.get("applied"):
        result["estimate"] = estimate
    else:
        stored_metadata = (result.get("record") or {}).get("metadata") or {}
        try:
            result["estimate"] = _validated_label_estimate(stored_metadata)
        except NutritionLabelError:
            result.pop("estimate", None)
    return result
