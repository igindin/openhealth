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
NUTRIENT_MAPPING_SCHEMA_VERSION = 1
BASIS_RESOLUTION_SCHEMA_VERSION = 1

_MACRO_FIELDS = ("protein_g", "fat_g", "carb_g")
_NUTRIENT_MAPPING_SOURCES = {"user_reply", "voice_transcript"}
_EXPLICIT_MAPPING_CONFIRMATIONS = {
    "yes",
    "yes correct",
    "yes thats correct",
    "confirm",
    "confirmed",
    "да",
    "да верно",
    "да все верно",
    "верно",
    "все верно",
    "подтверждаю",
    "подтверждаю бжу",
}

_GRAM_UNITS = {"g", "gr", "gram", "grams", "г", "гр", "грамм", "грамма", "գ"}
_ML_UNITS = {"ml", "milliliter", "milliliters", "мл", "մլ"}
_KCAL_UNITS = {"kcal", "ккал", "կկալ"}

_NUTRIENT_ALIASES = {
    "protein_g": {
        "protein",
        "proteins",
        "белок",
        "белки",
        "ս",
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
_NON_AMOUNT_ACKNOWLEDGEMENT_RE = re.compile(
    r"\b(?:все|всё)\b.{0,30}"
    r"\b(?:нормально|правильно|верно|ок(?:ей)?|хорошо)\b",
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
_SERVING_TOKEN_RE = re.compile(
    r"\b(?:порци\w*|servings?)\b",
    re.IGNORECASE,
)
_MIXED_SERVING_RE = re.compile(
    r"(?<!\d)(\d+)\s+([1-9]\d*)\s*/\s*([1-9]\d*)"
    r"\s*(?:порци\w*|servings?)\b",
    re.IGNORECASE,
)
_WORD_SERVING_RE = re.compile(
    r"\b(одн(?:у|а|ой)|целую|целая|две|два|три|четыре|пять|шесть|семь|"
    r"восемь|девять|десять|полторы|полтора|one|two|three|four|"
    r"five|six|seven|eight|nine|ten|a|whole)\s+"
    r"(?:порци\w*|servings?)\b",
    re.IGNORECASE,
)
_WORD_AND_HALF_SERVING_RE = re.compile(
    r"\b(одн(?:у|а|ой)|две|два|три|четыре|пять|шесть|семь|"
    r"восемь|девять|десять)\s+с\s+половиной\s+"
    r"(?:порци\w*)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_SERVING_COUNT_RE = re.compile(
    r"\b(?:несколько|пару|пара|около|примерно|some|several|couple)"
    r"\s+(?:порци\w*|servings?)\b",
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
    r"\b(?:"
    r"не|почти|оставил(?:а)?|осталось|недоел(?:а)?|недопил(?:а)?|"
    r"кроме|без|исключени\w*|минус|except|without|minus"
    r")\b",
    re.IGNORECASE,
)
_BASIS_SEMANTIC_MARKER_RE = re.compile(
    r"\b(?:значени[яй]|цифры|этикетк\w*|values?|label|per)\b",
    re.IGNORECASE,
)
_EXPLICIT_BASIS_SERVING_RE = re.compile(
    r"(?:"
    r"\b(?:на|за)\s+(?:одн(?:у|ой)\s+)?порци\w*\b|"
    r"\b(?:значени[яй]|цифры|этикетк\w*)\b.{0,40}"
    r"\bпорци\w*\b|"
    r"\bper\s+(?:one\s+)?serving\b"
    r")",
    re.IGNORECASE,
)
_EXPLICIT_BASIS_CONTAINER_RE = re.compile(
    r"(?:"
    r"\b(?:на|за)\s+(?:(?:всю|весь|целую|целый|одну)\s+)?"
    r"(?:упаковк\w*|бутылк\w*|банк\w*|контейнер\w*)\b|"
    r"\b(?:значени[яй]|цифры|этикетк\w*)\b.{0,40}"
    r"\b(?:упаковк\w*|бутылк\w*|банк\w*|контейнер\w*)\b|"
    r"\bper\s+(?:the\s+)?(?:package|container|bottle)\b)",
    re.IGNORECASE,
)
_RANGE_AMOUNT_RE = re.compile(
    r"(?<!\d)\d+(?:[.,]\d+)?\s*" r"(?:[-–—]|\b(?:до|to|or)\b)\s*" r"\d+(?:[.,]\d+)?",
    re.IGNORECASE,
)
_NUMBER_TOKEN_RE = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?![\d.,])")
_CONSUMPTION_ACTION_RE = re.compile(
    r"\b(?:"
    r"съел(?:а|и)?|съедено|выпил(?:а|и)?|выпито|"
    r"доел(?:а|и)?|допил(?:а|и)?|"
    r"ate|drank|consumed|finished"
    r")\b",
    re.IGNORECASE,
)
_NEGATED_CONSUMPTION_ACTION_RE = re.compile(
    r"\b(?:не|not)\s+(?:"
    r"съел(?:а|и)?|съедено|выпил(?:а|и)?|выпито|"
    r"доел(?:а|и)?|допил(?:а|и)?|"
    r"ate|drank|consumed|finished"
    r")\b",
    re.IGNORECASE,
)


class NutritionLabelError(ValueError):
    """Base class for unsafe or malformed label data."""

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or self.__class__.__name__


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


_KILOJOULE_UNIT_WORDS = {
    "kj",
    "кдж",
    "կջ",
}
_PROHIBITED_NORMALIZED_NUMBER_MARKERS = {
    "+",
    "-",
    "<",
    ">",
    "±",
    "~",
    "≈",
    "≤",
    "≥",
    "−",
    "﹣",
    "－",
    "%",
    "٪",
    "‰",
    "‱",
    "％",
}
_PROHIBITED_NUMBER_MARKER_NAMES = (
    "APPROXIMATELY",
    "GREATER-THAN",
    "LESS-THAN",
    "MINUS",
    "PERCENT",
    "PER MILLE",
    "PER TEN THOUSAND",
    "PLUS",
    "TILDE",
)


def _is_prohibited_number_marker(char: str) -> bool:
    normalized = unicodedata.normalize("NFKC", char)
    if (
        unicodedata.category(char)
        in {"Cf", "Me", "Mn", "Pd", "Sc", "Sm"}
        and normalized != "="
    ):
        return True
    if any(
        item in _PROHIBITED_NORMALIZED_NUMBER_MARKERS
        for item in normalized
    ):
        return True
    name = unicodedata.name(char, "").upper()
    return any(
        marker_name in name
        for marker_name in _PROHIBITED_NUMBER_MARKER_NAMES
    )


def _number_has_prohibited_prefix(
    text: str,
    number_start: int,
) -> bool:
    prefix = text[:number_start]
    if any(
        _is_prohibited_number_marker(char)
        for char in prefix
    ):
        return True
    attached = prefix.rstrip()
    return bool(
        attached
        and unicodedata.category(attached[-1]) == "Pd"
    )


def _is_kilojoule_bridge(text: str) -> bool:
    words = list(
        re.finditer(r"[^\W\d_]+", text, flags=re.UNICODE)
    )
    return (
        not any(char.isdigit() for char in text)
        and len(words) == 1
        and words[0].group(0).casefold()
        in _KILOJOULE_UNIT_WORDS
        and not any(
            _is_prohibited_number_marker(char)
            for char in text[: words[0].start()]
        )
    )


def _bound_value_unit_spans(
    text: str,
    value: float,
    raw_unit: str,
    *,
    allow_prior_energy_kj: bool = False,
) -> List[tuple[int, int]]:
    """Bind one value/unit, allowing only an explicit kJ-to-kcal bridge."""
    numbers = list(_NUMBER_TOKEN_RE.finditer(text))
    candidates: List[tuple[int, int]] = []
    exact_energy_occurrences: List[tuple[int, int]] = []
    invalid_energy_occurrence = False
    for number_index, number in enumerate(numbers):
        if not allow_prior_energy_kj and number_index > 0:
            break
        if not _number_is(value, number.group(0)):
            continue
        if _number_has_prohibited_prefix(
            text,
            number.start(),
        ):
            invalid_energy_occurrence = (
                invalid_energy_occurrence
                or allow_prior_energy_kj
            )
            continue
        next_number_start = (
            numbers[number_index + 1].start()
            if number_index + 1 < len(numbers)
            else len(text)
        )
        value_suffix = text[number.end() : next_number_start]
        value_unit_spans: List[tuple[int, int]] = []
        unit_search_start = 0
        while True:
            unit_start = value_suffix.find(
                raw_unit,
                unit_search_start,
            )
            if unit_start < 0:
                break
            before_unit = value_suffix[:unit_start]
            after_unit = value_suffix[
                unit_start + len(raw_unit) :
            ].lstrip()
            post_unit_separator = []
            for char in after_unit:
                if char.isalnum():
                    break
                post_unit_separator.append(char)
            if (
                not any(char.isalnum() for char in before_unit)
                and not any(
                    _is_prohibited_number_marker(char)
                    or unicodedata.category(char) == "Pd"
                    for char in before_unit
                )
                and not any(
                    _is_prohibited_number_marker(char)
                    for char in post_unit_separator
                )
            ):
                value_unit_spans.append(
                    (
                        number.start(),
                        number.end()
                        + unit_start
                        + len(raw_unit),
                    )
                )
            elif allow_prior_energy_kj:
                invalid_energy_occurrence = True
            unit_search_start = unit_start + 1
        if allow_prior_energy_kj:
            exact_energy_occurrences.extend(value_unit_spans)
        if not value_unit_spans:
            continue
        if allow_prior_energy_kj and number_index > 1:
            continue
        if number_index == 0:
            if any(
                char.isalnum()
                for char in text[: number.start()]
            ):
                continue
        else:
            first_number = numbers[0]
            if (
                _number_has_prohibited_prefix(
                    text,
                    first_number.start(),
                )
                or any(
                    char.isalnum()
                    for char in text[: first_number.start()]
                )
                or not _is_kilojoule_bridge(
                    text[
                        first_number.end() : number.start()
                    ]
                )
            ):
                continue
        candidates.extend(value_unit_spans)
    if (
        allow_prior_energy_kj
        and (
            invalid_energy_occurrence
            or len(set(exact_energy_occurrences)) != 1
        )
    ):
        return []
    return list(dict.fromkeys(candidates))


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


def _expand_raw_row_to_field_boundary(
    raw_label_text: str,
    raw_row_text: str,
) -> str:
    """Expand one unique literal suffix back to its physical field boundary."""
    if _raw_fragment_has_field_boundary(raw_label_text, raw_row_text):
        return raw_row_text
    index = raw_label_text.find(raw_row_text)
    if index < 0 or raw_label_text.find(
        raw_row_text,
        index + 1,
    ) >= 0:
        return raw_row_text
    separators = "\n\r|/;"
    boundary = max(
        raw_label_text.rfind(char, 0, index)
        for char in separators
    )
    return raw_label_text[
        boundary + 1 : index + len(raw_row_text)
    ]


def _raw_row_starts_with_bound_value(
    row_text: str,
    value: float,
    raw_unit: str,
) -> bool:
    """Return whether a suffix starts with the exact value/unit evidence."""
    numbers = list(_NUMBER_TOKEN_RE.finditer(row_text))
    if not numbers:
        return False
    number = numbers[0]
    if (
        any(char.isalnum() for char in row_text[: number.start()])
        or not _number_is(value, number.group(0))
    ):
        return False
    next_start = (
        numbers[1].start()
        if len(numbers) > 1
        else len(row_text)
    )
    return _contains_literal_token(
        row_text[number.end() : next_start],
        raw_unit,
    )


def _resolve_raw_row_fragment(
    raw_label_text: str,
    raw_row_text: str,
    *,
    field: str,
    code: Optional[str] = None,
) -> str:
    """Return the exact source fragment for a row with safe whitespace drift.

    Provider envelopes occasionally preserve the same visible row with spaces
    in one field and line breaks (or no separator) in another.  Reconciliation
    is limited to whitespace: every non-whitespace character must match, in
    order, and the compacted row must occur exactly once in the source text.
    """
    compact_row = "".join(
        char for char in raw_row_text if not char.isspace()
    )
    compact_label_chars: List[str] = []
    compact_label_offsets: List[int] = []
    for offset, char in enumerate(raw_label_text):
        if char.isspace():
            continue
        compact_label_chars.append(char)
        compact_label_offsets.append(offset)
    compact_label = "".join(compact_label_chars)

    matches: List[tuple[int, int]] = []
    start = 0
    while compact_row:
        index = compact_label.find(compact_row, start)
        if index < 0:
            break
        raw_start = compact_label_offsets[index]
        raw_end = compact_label_offsets[
            index + len(compact_row) - 1
        ] + 1
        matches.append((raw_start, raw_end))
        start = index + 1

    if len(matches) != 1:
        raise NutritionLabelNeedsClarification(
            "%s raw row must match one unambiguous whitespace-only "
            "fragment of raw_label_text" % field,
            code=code,
        )
    if raw_row_text in raw_label_text:
        return raw_row_text
    raw_start, raw_end = matches[0]
    return raw_label_text[raw_start:raw_end]


def _bind_raw_row(
    *,
    label: str,
    value: float,
    raw_unit: str,
    raw_row_text: Any,
    raw_label_text: str,
    field: str,
    allow_prior_energy_kj: bool = False,
) -> str:
    row_text = _text(
        raw_row_text,
        field + ".raw_row_text",
        required=True,
        limit=1000,
    )
    row_text = _resolve_raw_row_fragment(
        raw_label_text,
        row_text,
        field=field,
    )
    if not _raw_fragment_has_field_boundary(
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
    spans = _bound_value_unit_spans(
        suffix,
        value,
        raw_unit,
        allow_prior_energy_kj=allow_prior_energy_kj,
    )
    if len(spans) != 1:
        raise NutritionLabelNeedsClarification(
            "%s value/unit must bind once to its label" % field
        )
    return row_text


def _nutrient_span_in_raw_row(
    *,
    label: str,
    value: float,
    raw_unit: str,
    row_text: str,
    field: str,
    allow_prior_energy_kj: bool = False,
) -> tuple[int, int]:
    """Bind one exact label/value/unit span inside a shared literal row."""
    candidates: List[tuple[int, int]] = []
    search_start = 0
    while True:
        label_start = row_text.find(label, search_start)
        if label_start < 0:
            break
        suffix = row_text[label_start + len(label) :]
        value_spans = _bound_value_unit_spans(
            suffix,
            value,
            raw_unit,
            allow_prior_energy_kj=allow_prior_energy_kj,
        )
        for _, value_end in value_spans:
            candidates.append(
                (
                    label_start,
                    label_start + len(label) + value_end,
                )
            )
        search_start = label_start + 1

    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise NutritionLabelNeedsClarification(
            "%s must bind to one exact label/value/unit span" % field,
            code="nutrient_span_ambiguous",
        )
    return unique[0]


def _energy_row_binding(
    energy: Dict[str, Any],
) -> tuple[str, tuple[int, int], str]:
    row_text = energy["raw_row_text"]
    span = _nutrient_span_in_raw_row(
        label=energy["label"],
        value=energy["value"],
        raw_unit=energy["raw_unit"],
        row_text=row_text,
        field="energy",
        # A physical energy field may print kJ before the selected kcal.
        allow_prior_energy_kj=True,
    )
    return row_text, span, "energy"


def _bind_nutrient_raw_row(
    *,
    label: str,
    value: float,
    raw_unit: str,
    raw_row_text: Any,
    raw_label_text: str,
    field: str,
) -> tuple[str, tuple[int, int]]:
    row_text = _text(
        raw_row_text,
        field + ".raw_row_text",
        required=True,
        limit=1000,
    )
    source_row_text = _resolve_raw_row_fragment(
        raw_label_text,
        row_text,
        field=field,
        code="nutrient_raw_row_not_literal",
    )
    row_text = _expand_raw_row_to_field_boundary(
        raw_label_text,
        source_row_text,
    )

    if not _raw_fragment_has_field_boundary(
        raw_label_text,
        row_text,
    ):
        raise NutritionLabelNeedsClarification(
            "%s shared raw row must start at a field boundary" % field,
            code="nutrient_shared_row_boundary",
        )

    span = _nutrient_span_in_raw_row(
        label=label,
        value=value,
        raw_unit=raw_unit,
        row_text=row_text,
        field=field,
    )
    if row_text != source_row_text:
        added_prefix_length = len(row_text) - len(source_row_text)
        if span[1] <= added_prefix_length:
            raise NutritionLabelNeedsClarification(
                "%s original raw row does not overlap its target field"
                % field,
                code="nutrient_raw_row_not_target",
            )
        if (
            span[0] < added_prefix_length
            and not _raw_row_starts_with_bound_value(
                source_row_text,
                value,
                raw_unit,
            )
        ):
            raise NutritionLabelNeedsClarification(
                "%s may add only its missing leading label" % field,
                code="nutrient_raw_row_not_target",
            )
    return row_text, span


def _validate_nutrient_row_boundaries(
    bindings: List[tuple[str, tuple[int, int], str]],
) -> None:
    """Require exact ordered field boundaries on shared label rows."""
    rows: Dict[str, List[tuple[tuple[int, int], str]]] = {}
    for row_text, span, field in bindings:
        rows.setdefault(row_text, []).append((span, field))

    for row_text, row_bindings in rows.items():
        for index in range(1, len(row_bindings)):
            prior_span, prior_field = row_bindings[index - 1]
            span, field = row_bindings[index]
            if max(span[0], prior_span[0]) < min(
                span[1],
                prior_span[1],
            ):
                raise NutritionLabelNeedsClarification(
                    "label spans overlap between %s and %s"
                    % (prior_field, field),
                    code="nutrient_spans_overlap",
                )
            if span[0] <= prior_span[0]:
                raise NutritionLabelNeedsClarification(
                    "label spans are out of order between %s and %s"
                    % (prior_field, field),
                    code="nutrient_spans_out_of_order",
                )

        for index, (span, field) in enumerate(row_bindings):
            if index == 0:
                prefix = row_text[: span[0]]
                if any(char.isalnum() for char in prefix):
                    raise NutritionLabelNeedsClarification(
                        "%s label must start at a field boundary" % field,
                        code="nutrient_shared_row_boundary",
                    )
            else:
                prior_span, prior_field = row_bindings[index - 1]
                gap = row_text[prior_span[1] : span[0]]
                if any(char.isalnum() for char in gap):
                    raise NutritionLabelNeedsClarification(
                        "unbound text separates %s and %s"
                        % (prior_field, field),
                        code="nutrient_shared_row_boundary",
                    )

            next_start = (
                row_bindings[index + 1][0][0]
                if index + 1 < len(row_bindings)
                else None
            )
            if (
                span[1] < len(row_text)
                and row_text[span[1]].isalnum()
                and next_start != span[1]
            ):
                raise NutritionLabelNeedsClarification(
                    "%s unit must end at a field boundary" % field,
                    code="nutrient_unit_boundary",
                )


def _bind_nutrient_rows(
    rows: Any,
    raw_label_text: str,
) -> tuple[
    List[Dict[str, Any]],
    List[tuple[str, tuple[int, int], str]],
]:
    if not isinstance(rows, list):
        raise NutritionLabelError("nutrients must be a list")
    raw_rows: List[Dict[str, Any]] = []
    row_bindings: List[tuple[str, tuple[int, int], str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise NutritionLabelError("nutrients[%d] must be an object" % index)
        label = _text(row.get("label"), "nutrients[%d].label" % index, required=True, limit=160)
        value = _finite_number(row.get("value"), "nutrients[%d].value" % index)
        raw_unit = _text(row.get("unit"), "nutrients[%d].unit" % index, required=True, limit=40)
        canonical = canonical_nutrient(label)
        raw_row_text, span = _bind_nutrient_raw_row(
            label=label,
            value=value,
            raw_unit=raw_unit,
            raw_row_text=row.get("raw_row_text"),
            raw_label_text=raw_label_text,
            field="nutrients[%d]" % index,
        )
        row_bindings.append(
            (
                raw_row_text,
                span,
                "nutrients[%d]" % index,
            )
        )
        normalized_row = {
            "label": label,
            "value": value,
            "unit": raw_unit,
            "raw_row_text": raw_row_text,
            "canonical": canonical,
        }
        raw_rows.append(normalized_row)
    return raw_rows, row_bindings


def _resolve_macro_values(
    raw_rows: List[Dict[str, Any]],
    confirmed_mapping: Optional[Dict[int, str]] = None,
) -> Dict[str, float]:
    mapping = confirmed_mapping or {}
    macros: Dict[str, float] = {}
    for index, row in enumerate(raw_rows):
        known = row["canonical"]
        confirmed = mapping.get(index)
        if confirmed is not None and confirmed not in _MACRO_FIELDS:
            raise NutritionLabelNeedsClarification(
                "confirmed nutrient mapping target is invalid",
                code="nutrient_mapping_invalid",
            )
        if known is not None and confirmed is not None and known != confirmed:
            raise NutritionLabelNeedsClarification(
                "confirmed nutrient mapping contradicts an exact known label",
                code="nutrient_mapping_contradicts_known_label",
            )
        canonical = confirmed or known
        if canonical is None:
            continue
        if normalize_unit(row["unit"]) != "g":
            raise NutritionLabelNeedsClarification(
                "%s must be expressed in grams" % row["label"]
            )
        if canonical in macros:
            raise NutritionLabelNeedsClarification(
                "duplicate nutrient label for %s" % canonical
            )
        macros[canonical] = row["value"]
    missing = [field for field in _MACRO_FIELDS if field not in macros]
    if missing:
        raise NutritionLabelNeedsClarification(
            "label is missing unambiguous rows for %s"
            % ", ".join(missing),
            code="missing_macro_mapping",
        )
    return macros


def _normalize_nutrient_rows(
    rows: Any,
    raw_label_text: str,
    confirmed_mapping: Optional[Dict[int, str]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, float]]:
    raw_rows, _ = _bind_nutrient_rows(rows, raw_label_text)
    return raw_rows, _resolve_macro_values(raw_rows, confirmed_mapping)


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
            allow_prior_energy_kj=True,
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
        allow_prior_energy_kj=True,
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


def _json_sha256(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise NutritionLabelError("value must be JSON-serializable")
    return hashlib.sha256(serialized).hexdigest()


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
    return _json_sha256(payload)


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
    provenance["provenance_sha256"] = _json_sha256(provenance)
    return provenance


def _calculation_context_fingerprint(
    *,
    basis: str,
    package: Optional[Dict[str, Any]],
    serving: Optional[Dict[str, Any]],
    declared: Dict[str, Any],
    extraction_sha256: str,
    nutrient_mapping_resolution_sha256: Optional[str] = None,
    basis_resolution_sha256: Optional[str] = None,
) -> str:
    payload = {
        "basis": basis,
        "package": package,
        "serving": serving,
        "declared": declared,
        "extraction_sha256": extraction_sha256,
    }
    if nutrient_mapping_resolution_sha256 is not None:
        payload["nutrient_mapping_resolution_sha256"] = (
            nutrient_mapping_resolution_sha256
        )
    if basis_resolution_sha256 is not None:
        payload["basis_resolution_sha256"] = (
            basis_resolution_sha256
        )
    return _json_sha256(payload)


def _normalize_extraction_context(payload: Dict[str, Any]) -> Dict[str, Any]:
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
    raw_rows, row_bindings = _bind_nutrient_rows(
        payload.get("nutrients"),
        raw_label_text,
    )
    _validate_nutrient_row_boundaries(
        [_energy_row_binding(energy)] + row_bindings
    )

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

    return {
        "product_name_original": product_name_original,
        "product_name_ru": product_name_ru,
        "language": _text(payload.get("language"), "language", limit=40),
        "raw_label_text": raw_label_text,
        "basis": basis,
        "model_basis": model_basis,
        "basis_text": basis_text,
        "basis_verification": basis_verification,
        "package": package,
        "serving": serving,
        "energy": energy,
        "raw_rows": raw_rows,
        "row_bindings": row_bindings,
        "uncertainties_input": payload.get("uncertainties"),
        "model_confidence_input": payload.get("confidence"),
        "extraction_sha256": extraction_sha256,
        "provenance_input": payload.get("provenance"),
    }


def _context_with_provenance(
    context: Dict[str, Any],
) -> Dict[str, Any]:
    if "provenance" in context:
        return context
    enriched = dict(context)
    enriched["provenance"] = _normalize_provenance(
        context.get("provenance_input"),
        context["extraction_sha256"],
    )
    return enriched


def _normalized_label_from_context(
    context: Dict[str, Any],
    *,
    confirmed_mapping: Optional[Dict[int, str]] = None,
    nutrient_mapping_resolution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    macros = _resolve_macro_values(
        context["raw_rows"],
        confirmed_mapping,
    )
    declared = {
        "kcal": context["energy"]["value"],
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
    uncertainties = context.get("uncertainties_input") or []
    if not isinstance(uncertainties, list):
        raise NutritionLabelError("uncertainties must be a list")
    normalized_uncertainties = [
        _text(item, "uncertainty", limit=300)
        for item in uncertainties
        if str(item).strip()
    ]
    if (
        context["model_basis"] != BASIS_UNKNOWN
        and context["basis"] == BASIS_UNKNOWN
        and "nutrition basis could not be verified locally"
        not in normalized_uncertainties
    ):
        normalized_uncertainties.append(
            "nutrition basis could not be verified locally"
        )
    model_confidence = str(
        context.get("model_confidence_input") or ""
    ).strip().lower()
    if model_confidence not in {"low", "medium", "high"}:
        model_confidence = "unknown"
    provenance = _context_with_provenance(context)["provenance"]
    normalized = {
        "schema_version": LABEL_SCHEMA_VERSION,
        "mode": "label",
        "product_name_original": context["product_name_original"],
        "product_name_ru": context["product_name_ru"],
        # A translation is another model hypothesis.  Keep the exact visible
        # product name canonical and expose the translation separately.
        "title": context["product_name_original"],
        "language": context["language"],
        "raw_label_text": context["raw_label_text"],
        "basis": context["basis"],
        "model_basis": context["model_basis"],
        "basis_text": context["basis_text"],
        "basis_verification": context["basis_verification"],
        "package": context["package"],
        "serving": context["serving"],
        "energy": context["energy"],
        "raw_nutrients": context["raw_rows"],
        "declared": declared,
        "validation": validation,
        "uncertainties": normalized_uncertainties,
        "model_confidence": model_confidence,
        "confidence_level": LABEL_CONFIDENCE_LEVEL,
        "confidence": LABEL_CONFIDENCE,
        "provenance": provenance,
    }
    if nutrient_mapping_resolution is not None:
        normalized["nutrient_mapping_resolution"] = json.loads(
            json.dumps(
                nutrient_mapping_resolution,
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    return normalized


def normalize_label_extraction(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one model/OCR transcription without inventing missing fields."""
    return _normalized_label_from_context(
        _normalize_extraction_context(payload)
    )


def _prepare_nutrient_mapping_challenge_from_context(
    context: Dict[str, Any],
    proposed_mapping: Any,
    *,
    require_unmapped: bool = True,
) -> Dict[str, Any]:
    if not isinstance(proposed_mapping, dict):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping proposal must be an object",
            code="nutrient_mapping_proposal_invalid",
        )
    if set(proposed_mapping) != set(_MACRO_FIELDS):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping proposal must map protein, fat, and carbohydrate",
            code="nutrient_mapping_proposal_invalid",
        )

    mapping: Dict[int, str] = {}
    for canonical in _MACRO_FIELDS:
        index = proposed_mapping.get(canonical)
        if isinstance(index, bool) or not isinstance(index, int):
            raise NutritionLabelNeedsClarification(
                "nutrient mapping row indexes must be integers",
                code="nutrient_mapping_proposal_invalid",
            )
        if index < 0 or index >= len(context["raw_rows"]):
            raise NutritionLabelNeedsClarification(
                "nutrient mapping row index is out of range",
                code="nutrient_mapping_proposal_invalid",
            )
        if index in mapping:
            raise NutritionLabelNeedsClarification(
                "each nutrient mapping must use a distinct raw row",
                code="nutrient_mapping_proposal_invalid",
            )
        mapping[index] = canonical

    selected_rows = [
        context["raw_rows"][proposed_mapping[canonical]]
        for canonical in _MACRO_FIELDS
    ]
    if require_unmapped and all(
        row["canonical"] is not None for row in selected_rows
    ):
        raise NutritionLabelNeedsClarification(
            "nutrient labels are already mapped unambiguously",
            code="nutrient_mapping_not_required",
        )

    macros = _resolve_macro_values(context["raw_rows"], mapping)
    _macro_validation(
        context["energy"]["value"],
        macros["protein_g"],
        macros["fat_g"],
        macros["carb_g"],
    )

    entries = []
    for canonical in _MACRO_FIELDS:
        index = proposed_mapping[canonical]
        row = context["raw_rows"][index]
        if normalize_unit(row["unit"]) != "g":
            raise NutritionLabelNeedsClarification(
                "confirmed macronutrient rows must be expressed in grams",
                code="nutrient_mapping_proposal_invalid",
            )
        known = row["canonical"]
        if known is not None and known != canonical:
            raise NutritionLabelNeedsClarification(
                "nutrient mapping contradicts an exact known label",
                code="nutrient_mapping_contradicts_known_label",
            )
        span = context["row_bindings"][index][1]
        entries.append(
            {
                "canonical": canonical,
                "row_index": index,
                "label": row["label"],
                "value": row["value"],
                "unit": row["unit"],
                "raw_row_text": row["raw_row_text"],
                "span": [span[0], span[1]],
            }
        )

    challenge = {
        "schema_version": NUTRIENT_MAPPING_SCHEMA_VERSION,
        "kind": "nutrition_nutrient_mapping",
        "extraction_sha256": context["provenance"]["extraction_sha256"],
        "provenance_sha256": context["provenance"]["provenance_sha256"],
        "mapping": entries,
    }
    challenge["challenge_sha256"] = _json_sha256(challenge)
    return challenge


def prepare_nutrient_mapping_challenge(
    payload: Dict[str, Any],
    proposed_mapping: Dict[str, int],
) -> Dict[str, Any]:
    """Build a non-calculable, source-bound proposal for user confirmation.

    ``proposed_mapping`` maps each canonical macronutrient name to one index in
    the extraction's original ``nutrients`` list.  No semantic mapping is
    applied until :func:`apply_confirmed_nutrient_mapping` receives a separate
    explicit confirmation.
    """
    return _prepare_nutrient_mapping_challenge_from_context(
        _context_with_provenance(
            _normalize_extraction_context(payload)
        ),
        proposed_mapping,
    )


def _proposal_from_mapping_entries(entries: Any) -> Dict[str, int]:
    if not isinstance(entries, list) or len(entries) != len(_MACRO_FIELDS):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping challenge is invalid",
            code="nutrient_mapping_challenge_invalid",
        )
    proposal: Dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise NutritionLabelNeedsClarification(
                "nutrient mapping challenge is invalid",
                code="nutrient_mapping_challenge_invalid",
            )
        canonical = str(entry.get("canonical") or "")
        index = entry.get("row_index")
        if (
            canonical not in _MACRO_FIELDS
            or canonical in proposal
            or isinstance(index, bool)
            or not isinstance(index, int)
        ):
            raise NutritionLabelNeedsClarification(
                "nutrient mapping challenge is invalid",
                code="nutrient_mapping_challenge_invalid",
            )
        proposal[canonical] = index
    if set(proposal) != set(_MACRO_FIELDS):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping challenge is invalid",
            code="nutrient_mapping_challenge_invalid",
        )
    return proposal


def _explicit_mapping_confirmation(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    words = []
    current = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return " ".join(words) in _EXPLICIT_MAPPING_CONFIRMATIONS


def _normalize_mapping_confirmation(
    confirmation: Any,
    challenge: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(confirmation, dict):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation must be an object",
            code="nutrient_mapping_confirmation_invalid",
        )
    text = str(confirmation.get("text") or "").strip()
    if not text or len(text) > 500:
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation text is required",
            code="nutrient_mapping_confirmation_invalid",
        )
    red_flags = evidence.scan_text_red_flags(text)
    if red_flags:
        raise LabelCorrectionRedFlag(red_flags)
    if confirmation.get("schema_version") != NUTRIENT_MAPPING_SCHEMA_VERSION:
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation schema is invalid",
            code="nutrient_mapping_confirmation_invalid",
        )
    source = str(confirmation.get("source") or "").strip()
    if source not in _NUTRIENT_MAPPING_SOURCES:
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation source is invalid",
            code="nutrient_mapping_confirmation_invalid",
        )
    confirmation_id = str(confirmation.get("confirmation_id") or "").strip()
    if not confirmation_id or len(confirmation_id) > 240:
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation id is required",
            code="nutrient_mapping_confirmation_invalid",
        )
    if not _explicit_mapping_confirmation(text):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation is not explicit",
            code="nutrient_mapping_confirmation_not_explicit",
        )
    challenge_sha256 = str(
        confirmation.get("challenge_sha256") or ""
    ).strip()
    if challenge_sha256 != challenge["challenge_sha256"]:
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation is stale",
            code="nutrient_mapping_confirmation_stale",
        )
    artifact_ids = confirmation.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation artifacts are required",
            code="nutrient_mapping_confirmation_invalid",
        )
    normalized_artifact_ids = [
        str(item).strip()
        for item in artifact_ids
    ]
    if (
        not normalized_artifact_ids
        or any(not item or len(item) > 240 for item in normalized_artifact_ids)
    ):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping confirmation artifacts are required",
            code="nutrient_mapping_confirmation_invalid",
        )
    return {
        "schema_version": NUTRIENT_MAPPING_SCHEMA_VERSION,
        "source": source,
        "confirmation_id": confirmation_id,
        "text": text,
        "artifact_ids": list(dict.fromkeys(normalized_artifact_ids)),
        "challenge_sha256": challenge["challenge_sha256"],
    }


def _build_nutrient_mapping_resolution(
    challenge: Dict[str, Any],
    confirmation: Any,
) -> Dict[str, Any]:
    normalized = _normalize_mapping_confirmation(confirmation, challenge)
    resolution = {
        **normalized,
        "mapping": json.loads(
            json.dumps(
                challenge["mapping"],
                ensure_ascii=False,
                allow_nan=False,
            )
        ),
    }
    resolution["resolution_sha256"] = _json_sha256(resolution)
    return resolution


def apply_confirmed_nutrient_mapping(
    payload: Dict[str, Any],
    challenge: Dict[str, Any],
    confirmation: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive a normalized label from one exact, explicitly confirmed mapping."""
    if isinstance(confirmation, dict):
        red_flags = evidence.scan_text_red_flags(
            str(confirmation.get("text") or "")
        )
        if red_flags:
            raise LabelCorrectionRedFlag(red_flags)
    context = _context_with_provenance(
        _normalize_extraction_context(payload)
    )
    if not isinstance(challenge, dict):
        raise NutritionLabelNeedsClarification(
            "nutrient mapping challenge is invalid",
            code="nutrient_mapping_challenge_invalid",
        )
    proposal = _proposal_from_mapping_entries(challenge.get("mapping"))
    expected_challenge = _prepare_nutrient_mapping_challenge_from_context(
        context,
        proposal,
    )
    if challenge != expected_challenge:
        raise NutritionLabelNeedsClarification(
            "nutrient mapping challenge does not match the extraction",
            code="nutrient_mapping_challenge_invalid",
        )
    resolution = _build_nutrient_mapping_resolution(
        expected_challenge,
        confirmation,
    )
    confirmed_mapping = {
        index: canonical
        for canonical, index in proposal.items()
    }
    return _normalized_label_from_context(
        context,
        confirmed_mapping=confirmed_mapping,
        nutrient_mapping_resolution=resolution,
    )


def _normalize_stored_nutrient_mapping_resolution(
    context: Dict[str, Any],
    value: Any,
) -> tuple[Dict[str, Any], Dict[int, str]]:
    if not isinstance(value, dict):
        raise NutritionLabelValidationError(
            "nutrient mapping resolution is invalid",
            code="nutrient_mapping_resolution_invalid",
        )
    try:
        proposal = _proposal_from_mapping_entries(value.get("mapping"))
        challenge = _prepare_nutrient_mapping_challenge_from_context(
            context,
            proposal,
            require_unmapped=False,
        )
        expected = _build_nutrient_mapping_resolution(
            challenge,
            {
                "schema_version": value.get("schema_version"),
                "source": value.get("source"),
                "confirmation_id": value.get("confirmation_id"),
                "text": value.get("text"),
                "artifact_ids": value.get("artifact_ids"),
                "challenge_sha256": value.get("challenge_sha256"),
            },
        )
    except LabelCorrectionRedFlag:
        raise
    except NutritionLabelError as exc:
        raise NutritionLabelValidationError(
            "nutrient mapping resolution is invalid",
            code="nutrient_mapping_resolution_invalid",
        ) from exc
    if value != expected:
        raise NutritionLabelValidationError(
            "nutrient mapping resolution fingerprint does not match",
            code="nutrient_mapping_resolution_invalid",
        )
    return expected, {
        index: canonical
        for canonical, index in proposal.items()
    }


def _normalize_nutrient_mapping_resolution_envelope(
    value: Any,
    provenance: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise NutritionLabelValidationError(
            "nutrient mapping resolution is invalid",
            code="nutrient_mapping_resolution_invalid",
        )
    try:
        _proposal_from_mapping_entries(value.get("mapping"))
        challenge = {
            "schema_version": NUTRIENT_MAPPING_SCHEMA_VERSION,
            "kind": "nutrition_nutrient_mapping",
            "extraction_sha256": provenance["extraction_sha256"],
            "provenance_sha256": provenance["provenance_sha256"],
            "mapping": value.get("mapping"),
        }
        challenge["challenge_sha256"] = _json_sha256(challenge)
        expected = _build_nutrient_mapping_resolution(
            challenge,
            {
                "schema_version": value.get("schema_version"),
                "source": value.get("source"),
                "confirmation_id": value.get("confirmation_id"),
                "text": value.get("text"),
                "artifact_ids": value.get("artifact_ids"),
                "challenge_sha256": value.get("challenge_sha256"),
            },
        )
    except LabelCorrectionRedFlag:
        raise
    except NutritionLabelError as exc:
        raise NutritionLabelValidationError(
            "nutrient mapping resolution is invalid",
            code="nutrient_mapping_resolution_invalid",
        ) from exc
    if value != expected:
        raise NutritionLabelValidationError(
            "nutrient mapping resolution fingerprint does not match",
            code="nutrient_mapping_resolution_invalid",
        )
    return expected


def _scan_mapping_resolution_red_flags(value: Any) -> None:
    if not isinstance(value, dict):
        return
    resolution = value.get("nutrient_mapping_resolution")
    if not isinstance(resolution, dict):
        return
    red_flags = evidence.scan_text_red_flags(
        str(resolution.get("text") or "")
    )
    if red_flags:
        raise LabelCorrectionRedFlag(red_flags)


def validate_normalized_label(label: Dict[str, Any]) -> Dict[str, Any]:
    """Revalidate a JSON-round-tripped normalized label before calculation."""
    _scan_mapping_resolution_red_flags(label)
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
    normalized_rows, row_bindings = _bind_nutrient_rows(
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
    _validate_nutrient_row_boundaries(
        [_energy_row_binding(energy)] + row_bindings
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

    mapping_resolution_value = label.get("nutrient_mapping_resolution")
    mapping_resolution = None
    confirmed_mapping = None
    if mapping_resolution_value is not None:
        mapping_resolution, confirmed_mapping = (
            _normalize_stored_nutrient_mapping_resolution(
                {
                    "energy": energy,
                    "raw_rows": normalized_rows,
                    "row_bindings": row_bindings,
                    "provenance": provenance,
                },
                mapping_resolution_value,
            )
        )
    macros = _resolve_macro_values(
        normalized_rows,
        confirmed_mapping,
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

    basis = str(label.get("basis") or "").strip().lower()
    if basis not in SUPPORTED_BASES:
        raise NutritionLabelNeedsClarification("nutrition basis is invalid")
    resolution = label.get("basis_resolution")
    if resolution is not None:
        resolution = _normalize_basis_resolution(
            resolution,
            basis,
            provenance=provenance,
            basis_verification=basis_verification,
        )
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
    if mapping_resolution is not None:
        validated["nutrient_mapping_resolution"] = mapping_resolution
    return validated


def _whole_consumption_is_explicit(text: str) -> bool:
    if _CONSUMPTION_ACTION_RE.search(text):
        return True
    tokens = re.findall(
        r"[^\W\d_]+",
        unicodedata.normalize("NFKC", text).casefold(),
        re.UNICODE,
    )
    if not tokens:
        return False
    allowed = {
        "все",
        "всё",
        "всю",
        "весь",
        "целиком",
        "полностью",
        "целую",
        "целый",
    }
    return all(
        token in allowed
        or token.startswith("упаковк")
        or token.startswith("бутылк")
        or token.startswith("банк")
        or token.startswith("контейнер")
        for token in tokens
    )


def parse_consumed_amount(text: str) -> Dict[str, Any]:
    """Parse a short Russian/English answer about how much was consumed.

    The parser intentionally accepts only explicit units, fractions, servings,
    or clear whole-package wording.  A bare number is ambiguous and therefore
    rejected.
    """
    raw = str(text or "").strip()
    if not raw:
        raise NutritionLabelNeedsClarification("consumed amount is missing")
    if _NON_AMOUNT_ACKNOWLEDGEMENT_RE.search(raw):
        raise NutritionLabelNeedsClarification(
            "acknowledgement is not a consumed amount"
        )
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
        if (
            _FRACTION_RE.search(amount_source)
            or _PERCENT_RE.search(amount_source)
            or _HALF_RE.search(amount_source)
            or _QUARTER_RE.search(amount_source)
            or _THIRD_RE.search(amount_source)
            or _FULL_RE.search(amount_source)
            or _SERVING_TOKEN_RE.search(amount_source)
        ):
            raise NutritionLabelNeedsClarification(
                "consumed amount contains conflicting quantity signals"
            )
        amount_match = amount_matches[0]
        amount = _finite_number(amount_match.group(1).replace(",", "."), "consumed amount", positive=True)
        unit = normalize_unit(amount_match.group(2))
        if unit not in {"g", "ml"}:
            raise NutritionLabelNeedsClarification("consumed amount unit must be g or ml")
        return {"kind": "amount", "amount": amount, "unit": unit, "raw_text": raw}

    serving_word = _SERVING_TOKEN_RE.search(raw)
    fraction_match = _FRACTION_RE.search(raw)
    percent_match = _PERCENT_RE.search(raw)
    mixed_serving = _MIXED_SERVING_RE.search(raw)
    if mixed_serving:
        whole = float(mixed_serving.group(1))
        numerator = float(mixed_serving.group(2))
        denominator = float(mixed_serving.group(3))
        count = whole + (numerator / denominator)
        if not math.isfinite(count) or count <= 0:
            raise NutritionLabelNeedsClarification(
                "mixed serving count is invalid"
            )
        return {
            "kind": "servings",
            "count": count,
            "raw_text": raw,
        }
    if serving_word and fraction_match:
        numerator = float(fraction_match.group(1))
        denominator = float(fraction_match.group(2))
        count = numerator / denominator
        if not (0 < count <= 1):
            raise NutritionLabelNeedsClarification(
                "fractional serving must be between 0 and 1"
            )
        return {
            "kind": "servings",
            "count": count,
            "raw_text": raw,
        }
    if serving_word and percent_match:
        count = float(
            percent_match.group(1).replace(",", ".")
        ) / 100.0
        if not (0 < count <= 1):
            raise NutritionLabelNeedsClarification(
                "serving percent must be between 0 and 100"
            )
        return {
            "kind": "servings",
            "count": count,
            "raw_text": raw,
        }
    if serving_word:
        for pattern, count in (
            (_HALF_RE, 0.5),
            (_QUARTER_RE, 0.25),
            (_THIRD_RE, 1.0 / 3.0),
        ):
            if pattern.search(raw):
                return {
                    "kind": "servings",
                    "count": count,
                    "raw_text": raw,
                }

    word_counts = {
        "одну": 1.0,
        "одна": 1.0,
        "одной": 1.0,
        "целую": 1.0,
        "целая": 1.0,
        "две": 2.0,
        "два": 2.0,
        "три": 3.0,
        "четыре": 4.0,
        "пять": 5.0,
        "шесть": 6.0,
        "семь": 7.0,
        "восемь": 8.0,
        "девять": 9.0,
        "десять": 10.0,
        "полторы": 1.5,
        "полтора": 1.5,
        "one": 1.0,
        "two": 2.0,
        "three": 3.0,
        "four": 4.0,
        "five": 5.0,
        "six": 6.0,
        "seven": 7.0,
        "eight": 8.0,
        "nine": 9.0,
        "ten": 10.0,
        "a": 1.0,
        "whole": 1.0,
    }
    word_and_half = _WORD_AND_HALF_SERVING_RE.search(raw)
    if word_and_half:
        return {
            "kind": "servings",
            "count": (
                word_counts[word_and_half.group(1).casefold()]
                + 0.5
            ),
            "raw_text": raw,
        }
    word_serving = _WORD_SERVING_RE.search(raw)
    if word_serving:
        return {
            "kind": "servings",
            "count": word_counts[word_serving.group(1).casefold()],
            "raw_text": raw,
        }
    if _AMBIGUOUS_SERVING_COUNT_RE.search(raw):
        raise NutritionLabelNeedsClarification(
            "serving count must be explicit"
        )

    serving_match = _SERVING_RE.search(raw)
    if serving_match:
        if serving_match.group(1) is None:
            prefix_words = re.findall(
                r"[^\W\d_]+",
                raw[: serving_match.start()],
                re.UNICODE,
            )
            if (
                prefix_words
                and _CONSUMPTION_ACTION_RE.fullmatch(
                    prefix_words[-1]
                )
                is None
            ):
                raise NutritionLabelNeedsClarification(
                    "serving count modifier is unsupported"
                )
        count = _finite_number(
            (serving_match.group(1) or "1").replace(",", "."),
            "serving count",
            positive=True,
        )
        return {"kind": "servings", "count": count, "raw_text": raw}

    if fraction_match:
        numerator = float(fraction_match.group(1))
        denominator = float(fraction_match.group(2))
        fraction = numerator / denominator
        if not (0 < fraction <= 1):
            raise NutritionLabelNeedsClarification("package fraction must be between 0 and 1")
        return {"kind": "package_fraction", "fraction": fraction, "raw_text": raw}

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
        if not _whole_consumption_is_explicit(raw):
            raise NutritionLabelNeedsClarification(
                "whole-package wording contains non-amount context"
            )
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


def parse_basis_and_consumption_text(text: str) -> Dict[str, Any]:
    """Parse a basis-question reply without double-counting the same words.

    A basis is accepted from strict basis wording (``за 100 г``,
    ``цифры за упаковку``), or contextually from a basis-only answer such as
    ``Да всю упаковку``.  Once an eating/drinking action is present, broad
    container wording is treated as consumption rather than as a basis.  The
    consumed amount is parsed only from the independent action clause.
    """
    raw = str(text or "").strip()
    red_flags = evidence.scan_text_red_flags(raw)
    if red_flags:
        raise LabelCorrectionRedFlag(red_flags)

    strict_basis = detect_explicit_basis_correction(raw)
    action = _CONSUMPTION_ACTION_RE.search(raw)
    negated_action = _NEGATED_CONSUMPTION_ACTION_RE.search(raw)
    basis: Optional[str] = strict_basis
    if (
        basis is None
        and action is None
        and not _PARTIAL_CAVEAT_RE.search(raw)
    ):
        try:
            basis = parse_label_basis_text(raw)
        except NutritionLabelNeedsClarification:
            basis = None

    consumed = None
    if (
        action is not None
        and negated_action is None
        and not _PARTIAL_CAVEAT_RE.search(raw)
    ):
        try:
            consumption_source = (
                raw[action.start() :]
                if strict_basis is not None
                else raw
            )
            consumed = parse_consumed_amount(consumption_source)
        except NutritionLabelNeedsClarification:
            consumed = None
    return {
        "basis": basis,
        "consumed": consumed,
        "raw_text": raw,
    }


def detect_explicit_basis_correction(text: str) -> Optional[str]:
    """Return a basis only when a consumption reply explicitly corrects it."""
    raw = str(text or "").strip()
    patterns = {
        BASIS_PER_100G: _EXPLICIT_BASIS_100G_RE,
        BASIS_PER_100ML: _EXPLICIT_BASIS_100ML_RE,
        BASIS_PER_SERVING: _EXPLICIT_BASIS_SERVING_RE,
        BASIS_PER_CONTAINER: _EXPLICIT_BASIS_CONTAINER_RE,
    }
    evidence_matches = {
        basis: list(pattern.finditer(raw))
        for basis, pattern in patterns.items()
    }
    unique = [
        basis
        for basis, matches in evidence_matches.items()
        if matches
    ]
    if len(unique) > 1:
        raise NutritionLabelNeedsClarification("basis correction contains more than one basis")
    if not unique:
        return None
    basis = unique[0]
    action = _CONSUMPTION_ACTION_RE.search(raw)
    if action is None:
        return basis
    for match in evidence_matches[basis]:
        if match.start() < action.start():
            return basis
        prefix = raw[max(0, match.start() - 60) : match.start()]
        if (
            _BASIS_SEMANTIC_MARKER_RE.search(match.group(0))
            or _BASIS_SEMANTIC_MARKER_RE.search(prefix)
        ):
            return basis
    return None


def _prepare_label_basis_challenge_from_context(
    provenance: Any,
    basis_verification: Any,
    *,
    require_unresolved: bool,
) -> Dict[str, Any]:
    if not isinstance(provenance, dict):
        raise NutritionLabelNeedsClarification(
            "label basis challenge requires provenance",
            code="basis_challenge_invalid",
        )
    extraction_sha256 = str(
        provenance.get("extraction_sha256") or ""
    ).strip()
    provenance_sha256 = str(
        provenance.get("provenance_sha256") or ""
    ).strip()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", extraction_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", provenance_sha256)
    ):
        raise NutritionLabelNeedsClarification(
            "label basis challenge requires fingerprinted provenance",
            code="basis_challenge_invalid",
        )
    if not isinstance(basis_verification, dict):
        raise NutritionLabelNeedsClarification(
            "label basis verification is invalid",
            code="basis_challenge_invalid",
        )
    expected_keys = {
        "status",
        "model_basis",
        "visible_basis",
        "basis_text_is_raw_fragment",
        "basis",
    }
    if set(basis_verification) != expected_keys:
        raise NutritionLabelNeedsClarification(
            "label basis verification is invalid",
            code="basis_challenge_invalid",
        )
    normalized_verification = json.loads(
        json.dumps(
            basis_verification,
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    if (
        normalized_verification.get("basis") not in SUPPORTED_BASES
        or normalized_verification.get("model_basis") not in SUPPORTED_BASES
        or normalized_verification.get("visible_basis") not in SUPPORTED_BASES
        or normalized_verification.get("status")
        not in {"verified", "unverified"}
        or not isinstance(
            normalized_verification.get("basis_text_is_raw_fragment"),
            bool,
        )
    ):
        raise NutritionLabelNeedsClarification(
            "label basis verification is invalid",
            code="basis_challenge_invalid",
        )
    if (
        require_unresolved
        and normalized_verification["basis"] != BASIS_UNKNOWN
    ):
        raise NutritionLabelNeedsClarification(
            "label basis is already visible",
            code="basis_challenge_not_required",
        )
    challenge = {
        "schema_version": BASIS_RESOLUTION_SCHEMA_VERSION,
        "kind": "nutrition_label_basis",
        "extraction_sha256": extraction_sha256,
        "provenance_sha256": provenance_sha256,
        "basis_verification": normalized_verification,
    }
    challenge["challenge_sha256"] = _json_sha256(challenge)
    return challenge


def prepare_label_basis_challenge(
    label: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a source-bound prompt challenge for one unresolved label basis."""
    validated = validate_normalized_label(label)
    if (
        validated["basis"] != BASIS_UNKNOWN
        or validated.get("basis_resolution") is not None
    ):
        raise NutritionLabelNeedsClarification(
            "label basis is already resolved",
            code="basis_challenge_not_required",
        )
    return _prepare_label_basis_challenge_from_context(
        validated["provenance"],
        validated["basis_verification"],
        require_unresolved=True,
    )


def _normalize_basis_confirmation(
    confirmation: Any,
    challenge: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(confirmation, dict):
        raise NutritionLabelNeedsClarification(
            "label basis confirmation must be an object",
            code="basis_confirmation_invalid",
        )
    text = str(confirmation.get("text") or "").strip()
    red_flags = evidence.scan_text_red_flags(text)
    if red_flags:
        raise LabelCorrectionRedFlag(red_flags)
    if (
        confirmation.get("schema_version")
        != BASIS_RESOLUTION_SCHEMA_VERSION
    ):
        raise NutritionLabelNeedsClarification(
            "label basis confirmation schema is invalid",
            code="basis_confirmation_invalid",
        )
    source = str(confirmation.get("source") or "").strip()
    if source not in {
        "user_reply",
        "voice_transcript",
        "user_correction",
    }:
        raise NutritionLabelNeedsClarification(
            "label basis confirmation source is invalid",
            code="basis_confirmation_invalid",
        )
    confirmation_id = str(
        confirmation.get("confirmation_id") or ""
    ).strip()
    if (
        not text
        or len(text) > 500
        or not confirmation_id
        or len(confirmation_id) > 240
    ):
        raise NutritionLabelNeedsClarification(
            "label basis confirmation text and id are required",
            code="basis_confirmation_invalid",
        )
    if (
        str(confirmation.get("challenge_sha256") or "").strip()
        != challenge["challenge_sha256"]
    ):
        raise NutritionLabelNeedsClarification(
            "label basis confirmation is stale",
            code="basis_confirmation_stale",
        )
    artifact_ids = confirmation.get("artifact_ids")
    normalized_artifact_ids = (
        [str(item).strip() for item in artifact_ids]
        if isinstance(artifact_ids, list)
        else []
    )
    if (
        not normalized_artifact_ids
        or any(
            not item or len(item) > 240
            for item in normalized_artifact_ids
        )
    ):
        raise NutritionLabelNeedsClarification(
            "label basis confirmation artifacts are required",
            code="basis_confirmation_invalid",
        )
    parsed = parse_basis_and_consumption_text(text)
    basis = parsed["basis"]
    if basis is None:
        raise NutritionLabelNeedsClarification(
            "label basis confirmation is not explicit",
            code="basis_confirmation_not_explicit",
        )
    return {
        "schema_version": BASIS_RESOLUTION_SCHEMA_VERSION,
        "source": source,
        "confirmation_id": confirmation_id,
        "text": text,
        "artifact_ids": list(
            dict.fromkeys(normalized_artifact_ids)
        ),
        "challenge_sha256": challenge["challenge_sha256"],
        "basis": basis,
    }


def _build_basis_resolution(
    challenge: Dict[str, Any],
    confirmation: Any,
) -> Dict[str, Any]:
    normalized = _normalize_basis_confirmation(
        confirmation,
        challenge,
    )
    resolution = {
        **normalized,
        "basis_verification": json.loads(
            json.dumps(
                challenge["basis_verification"],
                ensure_ascii=False,
                allow_nan=False,
            )
        ),
    }
    resolution["resolution_sha256"] = _json_sha256(resolution)
    return resolution


def apply_confirmed_label_basis(
    label: Dict[str, Any],
    challenge: Dict[str, Any],
    confirmation: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply one exact source-bound basis reply to an unresolved label."""
    if isinstance(confirmation, dict):
        red_flags = evidence.scan_text_red_flags(
            str(confirmation.get("text") or "")
        )
        if red_flags:
            raise LabelCorrectionRedFlag(red_flags)
    validated = validate_normalized_label(label)
    if (
        validated["basis"] != BASIS_UNKNOWN
        or validated.get("basis_resolution") is not None
    ):
        raise NutritionLabelNeedsClarification(
            "label basis is already resolved",
            code="basis_challenge_not_required",
        )
    expected_challenge = _prepare_label_basis_challenge_from_context(
        validated["provenance"],
        validated["basis_verification"],
        require_unresolved=True,
    )
    if challenge != expected_challenge:
        raise NutritionLabelNeedsClarification(
            "label basis challenge does not match the extraction",
            code="basis_challenge_invalid",
        )
    resolution = _build_basis_resolution(
        expected_challenge,
        confirmation,
    )
    resolved = json.loads(
        json.dumps(validated, ensure_ascii=False, allow_nan=False)
    )
    resolved["basis"] = resolution["basis"]
    resolved["basis_resolution"] = resolution
    return validate_normalized_label(resolved)


def _normalize_basis_resolution(
    resolution: Any,
    basis: str,
    *,
    provenance: Optional[Dict[str, Any]] = None,
    basis_verification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(resolution, dict):
        raise NutritionLabelNeedsClarification("basis resolution is invalid")
    v1_markers = {
        "schema_version",
        "confirmation_id",
        "challenge_sha256",
        "basis_verification",
        "resolution_sha256",
        "basis",
    }
    if any(key in resolution for key in v1_markers):
        try:
            if (
                resolution.get("schema_version")
                != BASIS_RESOLUTION_SCHEMA_VERSION
            ):
                raise NutritionLabelNeedsClarification(
                    "basis resolution schema is unsupported",
                    code="basis_resolution_invalid",
                )
            challenge = _prepare_label_basis_challenge_from_context(
                provenance,
                resolution.get("basis_verification"),
                require_unresolved=False,
            )
            if (
                basis_verification is not None
                and resolution.get("basis_verification")
                != basis_verification
            ):
                raise NutritionLabelNeedsClarification(
                    "basis resolution verification context is stale",
                    code="basis_resolution_invalid",
                )
            expected = _build_basis_resolution(
                challenge,
                {
                    "schema_version": resolution.get(
                        "schema_version"
                    ),
                    "source": resolution.get("source"),
                    "confirmation_id": resolution.get(
                        "confirmation_id"
                    ),
                    "text": resolution.get("text"),
                    "artifact_ids": resolution.get("artifact_ids"),
                    "challenge_sha256": resolution.get(
                        "challenge_sha256"
                    ),
                },
            )
        except LabelCorrectionRedFlag:
            raise
        except NutritionLabelError as exc:
            raise NutritionLabelValidationError(
                "basis resolution is invalid",
                code="basis_resolution_invalid",
            ) from exc
        if resolution != expected or expected["basis"] != basis:
            raise NutritionLabelValidationError(
                "basis resolution fingerprint does not match",
                code="basis_resolution_invalid",
            )
        return expected
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
    nutrient_mapping_resolution = label.get(
        "nutrient_mapping_resolution"
    )
    nutrient_mapping_resolution_sha256 = (
        nutrient_mapping_resolution["resolution_sha256"]
        if isinstance(nutrient_mapping_resolution, dict)
        else None
    )
    basis_resolution = label.get("basis_resolution")
    basis_resolution_sha256 = (
        basis_resolution["resolution_sha256"]
        if (
            isinstance(basis_resolution, dict)
            and basis_resolution.get("schema_version")
            == BASIS_RESOLUTION_SCHEMA_VERSION
        )
        else None
    )
    calculation_context_sha256 = _calculation_context_fingerprint(
        basis=label["basis"],
        package=label.get("package"),
        serving=label.get("serving"),
        declared=declared,
        extraction_sha256=label["provenance"]["extraction_sha256"],
        nutrient_mapping_resolution_sha256=(
            nutrient_mapping_resolution_sha256
        ),
        basis_resolution_sha256=basis_resolution_sha256,
    )
    estimate = {
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
    if nutrient_mapping_resolution is not None:
        estimate["nutrient_mapping_resolution"] = json.loads(
            json.dumps(
                nutrient_mapping_resolution,
                ensure_ascii=False,
            )
        )
    return estimate


def estimate_from_consumption_text(label: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Convenience wrapper used by text and locally transcribed voice replies."""
    return calculate_consumed_estimate(label, parse_consumed_amount(text))


def _validated_label_estimate(estimate: Dict[str, Any]) -> Dict[str, Any]:
    _scan_mapping_resolution_red_flags(estimate)
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
    nutrient_mapping_resolution = (
        _normalize_nutrient_mapping_resolution_envelope(
            estimate.get("nutrient_mapping_resolution"),
            provenance,
        )
    )
    resolution_value = estimate.get("basis_resolution")
    resolution = (
        _normalize_basis_resolution(
            resolution_value,
            basis,
            provenance=provenance,
        )
        if resolution_value is not None
        else None
    )
    calculation_context_sha256 = _calculation_context_fingerprint(
        basis=basis,
        package=package,
        serving=serving,
        declared=declared,
        extraction_sha256=extraction_sha256,
        nutrient_mapping_resolution_sha256=(
            nutrient_mapping_resolution["resolution_sha256"]
            if nutrient_mapping_resolution is not None
            else None
        ),
        basis_resolution_sha256=(
            resolution["resolution_sha256"]
            if (
                resolution is not None
                and resolution.get("schema_version")
                == BASIS_RESOLUTION_SCHEMA_VERSION
            )
            else None
        ),
    )
    if str(estimate.get("calculation_context_sha256") or "") != calculation_context_sha256:
        raise NutritionLabelValidationError("label estimate calculation context fingerprint does not match")

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
    if nutrient_mapping_resolution is not None:
        normalized["nutrient_mapping_resolution"] = (
            nutrient_mapping_resolution
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
