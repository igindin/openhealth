# Strain (daily load)
> algo_version: strain@v1 · data source: WHOOP raw (passthrough) · editability: description only

## What this is

The day's cardiovascular load on WHOOP's 0-21 scale. We do **not** compute it: the provider's value is passed through as is, and the engine only validates and clamps it to range.

## Formula / algorithm

`strain = clamp(raw_whoop_strain, 0.0, 21.0)` — that is all. If the value had to be clamped, `clamped: true` is set in metadata.

A C-note that matters when reading the number: the WHOOP scale is **logarithmic and derived from Borg RPE** (Borg ~ HR/10), not linear. Moving from 16 to 17 takes noticeably more load than moving from 4 to 5. Comparing days by subtraction is simply invalid, and "strain 18 is twice as hard as 9" is wrong.

For sources without the WHOOP scale (Apple Health, Garmin) strain is not produced at all, which is more honest than redrawing someone else's metrics onto someone else's scale.

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| scale minimum | 0.0 | `openhealth/modules/recovery.py: STRAIN_MIN` | the lower bound of the WHOOP scale |
| scale maximum | 21.0 | `openhealth/modules/recovery.py: STRAIN_MAX` | the upper bound of the WHOOP scale |
| "hard day" | 14.0 | `openhealth/insights.py: STRAIN_HIGH` | threshold for the strain/recovery mismatch detector |

## Sources and confidence

- The scale and its semantics come from WHOOP's public documentation (Borg RPE-derived, logarithmic).
- Written with `evidence_class: personal` (a fact from the provider); `scale_note` in metadata records the logarithmic nature.

## Known limitations

- WHOOP's formula is closed, so we inherit it sight unseen and cannot reproduce it.
- There is no in-house strain for non-WHOOP sources (a deliberate omission, see above).
- strain@v1 is bumped only when our handling changes (clamping, notes), not when the WHOOP scale itself changes.
