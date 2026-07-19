# VO2max (cardio fitness estimate)
> algo_version: vo2max@v1 · data source: engine (from WHOOP raw) · editability: parameters in code

## What this is

An estimate of maximal oxygen uptake (mL/kg/min) without a lab, using the heart rate ratio method of Uth-Sørensen-Overgaard-Pedersen (2004). It is always an **estimate, not a measurement**: every record carries C2 and an explicit disclaimer.

## Formula / algorithm

`VO2max ≈ 15.3 × (HRmax / HRrest)` [mL/(kg·min)]

Sources of HRmax, in priority order:

1. **Measured** HRmax from a WHOOP body measurement (`max_heart_rate`) — preferred, since the method was validated on it;
2. fallback **220 − age**, only if age is known (large individual error, flagged as `hrmax_source: age_estimate_220_minus_age`);
3. with neither available, the module **refuses to compute** (ValueError) rather than inventing a number.

HRrest is the most recent resting heart rate on or before the date. The result is checked for plausibility (10-90 mL/kg/min, outside which the `plausible_range: false` flag is set).

**ACSM categories** (excellent/good/fair/poor) are attached only when both sex and age are known, using the `_CATEGORY_BANDS` table of population bands (sex × age decade, with threshold floors; for example men aged 30-39: excellent >= 50, good >= 44, fair >= 40). This is a rough population reference point, not a verdict.

**C2 disclaimer (always in the output):** the Uth method was validated on well-trained men aged 21-51; transfer to women, untrained people and older adults is not established; it is most reliable with a measured HRmax. Treat it as a rough range for discussion.

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| Uth coefficient | 15.3 | `openhealth/modules/vo2max.py: UTH_COEFFICIENT` | the original coefficient from Uth et al. (2004) |
| plausible minimum | 10.0 | `openhealth/modules/vo2max.py: _VO2_MIN` | below this is not seen in a living person, so the inputs are wrong |
| plausible maximum | 90.0 | `openhealth/modules/vo2max.py: _VO2_MAX` | above this is record territory, almost certainly an error |
| ACSM bands | table | `openhealth/modules/vo2max.py: _CATEGORY_BANDS` | population categories by sex and decade |

## Sources and confidence

- Uth N., Sørensen H., Overgaard K., Pedersen P.K. (2004) — Heart Rate Ratio Method.
- Categories come from ACSM-style summary tables of cardiorespiratory fitness.
- Always C2 (`confidence: "C2"` in metadata) plus a `DISCLAIMER` on every record and in the summary.

## Known limitations

- Transferring the validation (trained men aged 21-51) to other groups is not established.
- `220 − age` carries an individual error of ±10-12 bpm, so an estimate via that fallback is cruder.
- HRrest from WHOOP is measured overnight, while the Uth formula was written for morning rest, so a systematic bias is possible.
