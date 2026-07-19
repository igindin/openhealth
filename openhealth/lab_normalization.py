"""Numeric lab normalization — canonicalize units and values before interpretation.

A lab report can state the same marker in different units (US conventional vs SI),
with localized number formatting (a comma decimal: ``13,5``), or with qualifier
prefixes (``<0.01``, ``>100``). The reference-range table in
``openhealth.reference_ranges`` always compares against a marker's *conventional*
unit. So before any flag is computed, a value must be brought to that canonical
unit and to a clean float.

This module is that pre-step. It does NOT interpret, flag, or diagnose — it only
makes a value comparable. It reuses the marker identities and SI factors already
defined in ``reference_ranges`` (single source of truth) and adds the reverse
(SI -> conventional) direction plus tolerant value parsing.

Design rules:
- Never invent a value. If a unit is not recognised, the value is passed through
  untouched and flagged ``unit_recognised=False`` so the caller stays cautious.
- A converted value carries ``converted_from`` so provenance is never lost.
- Pure stdlib, zero external deps (core rule).
"""

from typing import Dict, List, Optional, Tuple

from . import reference_ranges

# --- unit aliases ----------------------------------------------------------
#
# Map many spellings of a unit (lowercased, Latin + Cyrillic) to a canonical
# UCUM-ish token. Both the conventional and SI unit of every marker in
# reference_ranges must resolve here so a report stated either way normalizes.
#
# CYRILLIC KEYS ARE INPUT DATA, NOT UNTRANSLATED TEXT. Russian-language lab
# reports print unit names in Cyrillic, so those exact byte sequences are what
# arrives from a PDF/OCR extraction. They are written below as \uXXXX escapes
# (with an English gloss) to keep this source ASCII while still matching real
# reports. Removing them would break parsing of Russian lab PDFs.

_UNIT_ALIASES: Dict[str, str] = {
    # -- mass / volume concentrations, Latin spellings
    "mg/dl": "mg/dL",
    "mg/dl.": "mg/dL",
    "g/dl": "g/dL",
    "g/l": "g/L",
    # -- molar concentrations, Latin spellings
    "mmol/l": "mmol/L",
    "umol/l": "umol/L",
    "µmol/l": "umol/L",
    "nmol/l": "nmol/L",
    "pmol/l": "pmol/L",
    # -- small mass per volume, Latin spellings
    "ng/ml": "ng/mL",
    "ug/l": "ug/L",
    "µg/l": "ug/L",
    "pg/ml": "pg/mL",
    # -- cell counts, Latin spellings
    "10^9/l": "10^9/L",
    "10*9/l": "10^9/L",
    "x10^9/l": "10^9/L",
    "10e9/l": "10^9/L",
    # -- endocrine / misc, Latin spellings
    "miu/l": "mIU/L",
    "mg/l": "mg/L",
    "%": "%",
    "percent": "%",

    # -- Cyrillic spellings as printed on Russian lab reports (see note above).
    #    Each key is the Cyrillic rendering of the unit named in its comment.
    "\u043c\u0433/\u0434\u043b": "mg/dL",                     # Cyrillic "mg/dL"
    "\u0433/\u0434\u043b": "g/dL",                            # Cyrillic "g/dL"
    "\u0433/\u043b": "g/L",                                   # Cyrillic "g/L"
    "\u043c\u043c\u043e\u043b\u044c/\u043b": "mmol/L",        # Cyrillic "mmol/L"
    "\u043c\u043a\u043c\u043e\u043b\u044c/\u043b": "umol/L",  # Cyrillic "umol/L"
    "\u043d\u043c\u043e\u043b\u044c/\u043b": "nmol/L",        # Cyrillic "nmol/L"
    "\u043f\u043c\u043e\u043b\u044c/\u043b": "pmol/L",        # Cyrillic "pmol/L"
    "\u043d\u0433/\u043c\u043b": "ng/mL",                     # Cyrillic "ng/mL"
    "\u043c\u043a\u0433/\u043b": "ug/L",                      # Cyrillic "ug/L"
    "\u043f\u0433/\u043c\u043b": "pg/mL",                     # Cyrillic "pg/mL"
    "10^9/\u043b": "10^9/L",                                  # Cyrillic "10^9/L"
    "\u043c\u043c\u0435/\u043b": "mIU/L",                     # Cyrillic "mIU/L"
    "\u043c\u0433/\u043b": "mg/L",                            # Cyrillic "mg/L"
}


def canonical_unit(unit: Optional[str]) -> Optional[str]:
    """Resolve a unit string as printed on a report to a canonical token.

    Case- and whitespace-insensitive; understands a few common Cyrillic and
    Latin spellings. Returns ``None`` when the unit is empty, and the cleaned
    original (unchanged token) when it is non-empty but unrecognised — so the
    caller can decide, never this function.
    """
    if unit is None:
        return None
    cleaned = unit.strip()
    if not cleaned:
        return None
    key = cleaned.lower().replace(" ", "")
    return _UNIT_ALIASES.get(key, cleaned)


# --- value parsing ---------------------------------------------------------

def parse_numeric(raw: object) -> Tuple[Optional[float], Optional[str]]:
    """Parse a lab value into ``(value, qualifier)``.

    Tolerates a comma decimal (``13,5``), surrounding whitespace, a leading
    ``<`` / ``>`` / ``≤`` / ``≥`` qualifier (kept separately so a below-range
    value is not silently treated as exact), and thousands separators in cell
    counts. Returns ``(None, None)`` for empty / unparseable input rather than
    guessing.
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool):  # guard: bool is an int subclass
        return None, None
    if isinstance(raw, (int, float)):
        return float(raw), None

    s = str(raw).strip()
    if not s:
        return None, None

    qualifier: Optional[str] = None
    if s[0] in "<>≤≥":
        head = s[0]
        qualifier = {"≤": "<=", "≥": ">="}.get(head, head)
        s = s[1:].strip()

    # Normalize a decimal comma to a dot, but only when it is acting as the
    # decimal separator (a single comma, no dot present).
    if "," in s and "." not in s and s.count(",") == 1:
        s = s.replace(",", ".")
    # Drop spaces used as thousands separators (e.g. "1 500").
    s = s.replace(" ", "")

    try:
        return float(s), qualifier
    except ValueError:
        return None, qualifier


# --- SI <-> conventional ---------------------------------------------------

def to_conventional(
    spec: reference_ranges.MarkerSpec, value: Optional[float], unit_token: Optional[str]
) -> Tuple[Optional[float], bool]:
    """Convert a value to the marker's conventional unit.

    Returns ``(value_in_conventional_unit, converted)``. If the stated unit is
    the marker's SI unit and an SI factor is known, divide it out (the reverse
    of ``reference_ranges.to_si``). If the stated unit already matches the
    conventional unit, or no factor is known, the value passes through with
    ``converted=False``.
    """
    if value is None:
        return None, False
    if unit_token is None:
        # No unit given: assume it is already the conventional unit.
        return value, False
    if unit_token == spec.unit:
        return value, False
    if spec.si_unit and unit_token == spec.si_unit and spec.to_si_factor:
        # conventional * factor = SI  =>  conventional = SI / factor
        return round(value / spec.to_si_factor, 6), True
    # Unit recognised by the alias table but not a known unit for this marker:
    # do not fabricate a conversion. Pass through; caller is warned via
    # unit_recognised below.
    return value, False


def normalize_marker(
    name: str,
    raw_value: object,
    unit: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    """Normalize one marker's value+unit to the canonical conventional form.

    Returns a dict with the canonical numeric value (ready for
    ``reference_ranges.assess_marker``), or ``None`` when the marker name is not
    recognised (the caller should keep the raw value and stay cautious).

    Keys:
    - ``marker_key`` / ``display_name`` — resolved marker identity.
    - ``value`` — value in the marker's conventional unit (or raw if no
      conversion was possible).
    - ``unit`` — the marker's conventional unit token.
    - ``value_si`` / ``si_unit`` — SI form, when a factor exists.
    - ``qualifier`` — ``"<"`` / ``">"`` etc. when the report gave an inequality.
    - ``unit_recognised`` — whether the stated unit mapped to a known unit for
      this marker. False means the value was passed through unconverted.
    - ``converted_from`` — the original ``(value, unit)`` when a conversion ran.
    """
    spec = reference_ranges.match_marker(name)
    if spec is None:
        return None

    value, qualifier = parse_numeric(raw_value)
    unit_token = canonical_unit(unit)

    conventional, converted = to_conventional(spec, value, unit_token)

    known_units = {spec.unit}
    if spec.si_unit:
        known_units.add(spec.si_unit)
    unit_recognised = unit_token is None or unit_token in known_units

    result: Dict[str, object] = {
        "marker_key": spec.key,
        "display_name": spec.display_name,
        "loinc": spec.loinc,
        "value": conventional,
        "unit": spec.unit,
        "value_si": reference_ranges.to_si(spec, conventional),
        "si_unit": spec.si_unit,
        "qualifier": qualifier,
        "unit_recognised": unit_recognised,
    }
    if converted:
        result["converted_from"] = {"value": value, "unit": unit_token}
    return result


def normalize_panel(markers: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Normalize a list of ``{name, value, unit}`` marker dicts.

    Recognised markers come back canonicalized (see ``normalize_marker``).
    Unrecognised markers are passed through with ``marker_key=None`` and
    ``unit_recognised=False`` so nothing is silently dropped, and the caller can
    still store the raw value.
    """
    out: List[Dict[str, object]] = []
    for m in markers:
        name = str(m.get("name") or m.get("marker") or "")
        normalized = normalize_marker(name, m.get("value"), m.get("unit"))
        if normalized is None:
            value, qualifier = parse_numeric(m.get("value"))
            out.append({
                "marker_key": None,
                "display_name": name,
                "value": value,
                "unit": canonical_unit(m.get("unit")),
                "qualifier": qualifier,
                "unit_recognised": False,
                "raw": True,
            })
        else:
            out.append(normalized)
    return out
