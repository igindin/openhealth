---
name: numeric-lab-normalization
description: >-
  Brings numeric lab values to a canonical form BEFORE interpretation: units
  (mmol/L ↔ mg/dL, SI ↔ conventional), decimal comma, "<" / ">" prefixes,
  Cyrillic and Latin spellings of units. Use it when a lab result carries values
  in unfamiliar units (Russian laboratories often use SI), when two lab results
  in different units need comparing, or before computing a reference_ranges
  flag. A wrapper around the openhealth.lab_normalization module. Triggers:
  "fix the units", "convert mmol to mg/dL", "normalize this lab result",
  "different units", "normalize lab units", "convert units".
---

# numeric-lab-normalization

A framework wrapping the `openhealth.lab_normalization` module. Its job is to make a value **comparable**, not to interpret it. Interpretation belongs to `lab-interpretation-guardrails`. Normalization strictly comes first: otherwise the comparison against a reference range lies.

Why this matters: the same glucose reading is either `99 mg/dL` or `5.5 mmol/L`. Cholesterol, B12, creatinine, vitamin D - Russian laboratories usually report these in SI units. The `reference_ranges` table compares in the marker's conventional units. So SI has to be converted to conventional before any flag is computed.

## What the module does

Pure stdlib; it builds on the marker identities and SI factors from `reference_ranges` (the single source of truth), adding the reverse direction and tolerant parsing.

- `parse_numeric(raw)` → `(value, qualifier)`. Understands a decimal comma (`"13,5"`), the prefixes `<` `>` `≤` `≥` (kept separately, so that "<0.01" isn't treated as an exact value), and a space as a thousands separator.
- `canonical_unit(unit)` → a canonical token. Understands Cyrillic and Latin spellings alike (the Russian spelling of `mmol/L` → `mmol/L`, of `mg/dL` → `mg/dL`). An unknown unit is returned as-is - the caller decides, not the function.
- `to_conventional(spec, value, unit_token)` → `(value_in_conventional, converted)`. The inverse of `reference_ranges.to_si`: if the unit is the marker's SI unit, it divides by the factor.
- `normalize_marker(name, raw_value, unit)` → a canonical dict (ready for `assess_marker`), or `None` if the marker isn't recognized.
- `normalize_panel(markers)` → a list; unrecognized markers are not lost, they're marked `marker_key=None`, `unit_recognised=False`, `raw=True`.

## How to call it

```
python3 -c "from openhealth import lab_normalization as ln; import json; \
print(json.dumps(ln.normalize_marker('Glucose', '5,55', 'mmol/L'), ensure_ascii=False))"
```

Returns: `value` already in `mg/dL` (≈100), `value_si` back in mmol/L, and `converted_from` holding the original - provenance is not lost.

A whole set of markers:

```
python3 -c "from openhealth import lab_normalization as ln; import json; \
print(json.dumps(ln.normalize_panel([{'name':'Glucose','value':'5,55','unit':'mmol/L'}, \
{'name':'Hemoglobin','value':'145','unit':'g/L'}]), ensure_ascii=False))"
```

## Hard rules

1. **Don't invent a conversion.** If the unit isn't recognized for that marker, the value passes through unchanged and is marked `unit_recognised=False`. You then say so honestly to the person.
2. **Don't lose provenance.** A converted value carries `converted_from` with the original value and unit.
3. **Don't interpret here.** Normalization ends at the canonical number. Flags, reference ranges and confidence grades belong to the next framework (`lab-interpretation-guardrails`).
4. **If you're unsure about a unit, ask the person** what's printed on their report. Don't guess between mmol/L and mg/dL: for glucose that's a factor of roughly 18.

## Connections

- → `lab-interpretation-guardrails`: after normalization.
- ← called from `/openhealth` (the `lab-interpreter` mode) and from `doctor-note-intake`, when a summary contains numeric markers.

This is not a medical interpretation - only bringing numbers to a comparable form.
